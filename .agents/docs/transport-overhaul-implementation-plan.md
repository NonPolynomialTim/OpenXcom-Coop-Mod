# Transport overhaul — implementation work order (P2P WebRTC)

**Audience: the implementing agent.** This is a step-by-step work order with
acceptance criteria. Design rationale lives in `p2p-webrtc-migration-plan.md`
(read it first); current-system reference is `coop-networking.md`. Where this
doc and the migration plan conflict, this doc wins (it is newer and more
specific).

**Goal in one sentence:** add a third transport backend (WebRTC data channels
via libdatachannel, signaled through a Cloudflare Worker) that feeds the same
`g_txQ`/`g_rxQ` message queues the TCP and UDP transports use today, then
delete the rendezvous server + custom UDP stack.

---

## 0. Ground rules (read before writing any code)

1. **Do not modify gameplay logic.** Nothing in `connectionTCP.cpp` below the
   queue layer, nothing in `src/Battlescape`, `src/Geoscape`, `src/Savegame`.
   The ONLY existing files you may touch are listed per-phase below.
2. **The TCP path must keep working at every phase.** `hostTCPServer()` /
   `connectTCPServer()` (Direct Connect, LAN, port-forward hosting) are the
   fallback and regression baseline. Run a TCP session after every phase.
3. **Do not fix 2-player game-logic assumptions** (single `clientSock`,
   `SDLNet_AllocSocketSet(2)` at connectionTCP.cpp:1556, singular peer state).
   But do NOT add new 2-player assumptions in code you write: every new
   message carries `peerId`, host code loops over a peer collection, room
   `maxPlayers` is plumbed through.
4. **Do not reintroduce the browser-teardown bug.** Signaling-server
   unreachable must NEVER set the global `onConnect` error state from the
   list-refresh path. Follow the `loadBuiltInKeysOrWarn` /
   `consumeMasterServerUnavailableWarning()` / `ServerList::think` →
   `CoopState(446)` pattern documented in `coop-networking.md` ("Historic bug
   (fixed)"). Hard errors are only for explicit host/join actions.
5. **GPL-clean deps only** (MIT/BSD/Apache). libdatachannel = MPL-2.0 (OK).
   No proprietary SDKs. EOS was evaluated and rejected — do not revisit.
6. **Every new .cpp/.h must be registered in `src/OpenXcom.2010.vcxproj`**
   (and CMakeLists.txt where the pattern exists). The committed vcxproj lags
   on CoopMod files and the user has a local uncommitted
   `Directory.Build.props` supplying include/link scaffolding — if your file
   compiles but its symbols are "unresolved external", you forgot vcxproj
   registration. Build command:
   `MSBuild src\OpenXcom.2010.sln -p:Configuration=Release -p:Platform=x64 -m`
   (Release|x64 only; Debug config is unmaintained).
7. **Commit at each phase boundary** with a green build + the phase's
   acceptance test passing.

## 1. The integration seam (what you plug into)

`src/CoopMod/connectionTCP.h:90-151` defines the seam:

- `SPSCQueue<1024> g_txQ, g_rxQ` (connectionTCP.h:137-138) — lock-free
  single-producer/single-consumer ring buffers of whole-message
  `std::string`s (JSON text). Definitions live in connectionTCP.cpp.
- Game → network: game code calls `sendTCPPacketStaticData(std::string)` /
  `enqueueTx(std::string&&)` (connectionTCP.h:143-147). Transport backends
  pop `g_txQ` and put bytes on the wire.
- Network → game: transport backends push received whole messages into
  `g_rxQ`; the main-thread pump (`updateCoopTask()`) pops and dispatches to
  `onTCPMessage()`.
- `clearNetworkSessionQueues()` (connectionTCP.h:151) must be called when a
  session ends — call it from your disconnect path too.

**CRITICAL — SPSC means exactly ONE producer thread.** libdatachannel invokes
`onMessage` callbacks on its own internal threads, and with multiple peers
(star topology) there are multiple channels ⇒ potentially concurrent
callbacks. You MUST serialize pushes to `g_rxQ`: hold a `std::mutex` in
`p2p_transport` around every `g_rxQ.push()` (and likewise around `g_txQ.pop()`
if more than one thread drains it — keep the drain in ONE pump thread and you
won't need that one). Same rule the UDP backend already follows. Getting this
wrong = rare corrupted-message heisenbugs; do not skip the mutex because "it
seems to work".

Queue-full handling: `push()` returns false when full (1024 slots). On tx
overflow, log via `DebugLog()` and retry after draining (the pump loop sleeps
1-5 ms); on rx overflow, log loudly — losing an rx message desyncs the game.

## 2. Components to build

### 2a. `signaling-worker/` — Cloudflare Worker (TypeScript, new top-level dir)

Files: `wrangler.toml`, `src/index.ts`, `src/directory.ts` (directory Durable
Object), `src/room.ts` (room Durable Object). Use WebSocket Hibernation API
(`state.acceptWebSocket(ws)` + `webSocketMessage`/`webSocketClose` handlers)
so idle rooms cost nothing.

Wire protocol (WSS, one JSON object per message, `op` field discriminates):

| Direction | Message | Reply / effect |
|---|---|---|
| host→W | `{op:"host", name, gamemode, modHash, passwordProtected, maxPlayers, version}` | `{op:"hosted", roomId}` — room listed while this socket stays open; socket close ⇒ delisted (presence = liveness; the old system's `kRoomTtlMs=0` never-expire bug becomes structurally impossible) |
| any→W | `{op:"list"}` | `{op:"rooms", rooms:[{roomId, name, gamemode, modHash, passwordProtected, players, maxPlayers, version}]}` |
| joiner→W | `{op:"join", roomId, playerName}` | W assigns `peerId`, notifies host `{op:"peer-joined", peerId, playerName}`; errors: `{op:"error", code:"room-not-found"\|"room-full"}` |
| both→W | `{op:"offer"\|"answer"\|"candidate", to:peerId, sdp\|candidate}` | W forwards to the target socket with `from:peerId` added (trickle ICE) |
| W→both | `{op:"peer-left", peerId}` | on socket close |

- Room password check stays app-level AFTER the data channel opens (same as
  today's TCP flow) — the Worker never sees passwords, only the
  `passwordProtected` boolean for the browser row.
- `version` + `modHash` are carried so the client browser can grey out
  incompatible rows (client already has `hasRequiredMods()`,
  connectionTCP.h:190).
- Directory DO holds `roomId → {metadata, roomStub}`; room DO owns the
  sockets. Keep it small — target ~300 lines total.
- Deploy: `wrangler deploy`; the wss URL goes in `p2p_config.cpp` (2f).

### 2b. `src/CoopMod/connectionP2P/p2p_transport.{h,cpp}`

Wraps libdatachannel. Suggested shape:

```cpp
class P2PTransport {
public:
    // host: N peer connections; client: 1. One reliable-ordered
    // data channel per peer, label "game".
    void startAsHost();
    void addPeer(const std::string& peerId);          // on peer-joined
    void startAsClient(const std::string& peerId);    // joiner side
    void setLocalDescriptionCallback(...);            // → p2p_signaling send
    void onRemoteDescription(peerId, sdp);            // ← signaling
    void onRemoteCandidate(peerId, candidate);
    bool allChannelsOpen() const;
    void shutdown();                                  // also clearNetworkSessionQueues()
private:
    void pumpLoop();     // ONE thread: drains g_txQ → channel(s) (host fans out)
    std::mutex _rxMutex; // serializes g_rxQ.push from onMessage callbacks
    ...
};
```

- ICE config from `p2p_config` (STUN + TURN, see 2f). Use
  `rtc::Configuration`; expose a `forceRelay` flag (sets ICE transport policy
  to relay-only) for TURN testing.
- **Chunking:** SCTP messages are size-limited (~256 KB safe default).
  Base/map/save transfers exceed this. Chunk at the transport layer,
  transparent to game code: a message ≤ CHUNK_LIMIT (use 200 KB) goes as-is;
  larger becomes `{op:"__chunk", id:<uint>, idx, total, data:<base64>}`
  frames, reassembled on receive before pushing the original string to
  `g_rxQ`. Reserve the `__chunk` op name; game messages never start with
  `__`. Keep plain JSON (debuggability > size at this traffic volume).
- **Envelope:** wrap outgoing game messages as
  `{op:"__env", from:<peerId>, payload:<original JSON string>}` OR simply
  inject a `peerId` key into the top-level JSON object. Prefer injection
  (less nesting churn); the 2-player game logic ignores it today, 4-player
  work uses it later.
- Threading: libdatachannel callbacks (onOpen/onMessage/onStateChange) fire
  on its worker threads. Never call game/UI code from them; only set atomics,
  push queues (under `_rxMutex`), or signal the pump.

### 2c. `src/CoopMod/connectionP2P/p2p_signaling.{h,cpp}`

WSS client to the Worker using libdatachannel's built-in `rtc::WebSocket`
(ships with the library — no extra dependency). Responsibilities: connect,
send/receive the 2a protocol, surface callbacks (`onRooms`, `onPeerJoined`,
`onOffer/Answer/Candidate`, `onError`, `onClosed`). Reconnect with capped
backoff for the browser-refresh use case; NO auto-reconnect mid-session (data
path is already P2P; the Worker is only needed again for a new join).

### 2d. `src/CoopMod/connectionP2P/p2p_glue.{h,cpp}`

The UI-facing entry points, mirroring `connection_rendezvous_glue.{h,cpp}`
flow-for-flow so menu code changes are one-line call-target swaps:

| Old (rendezvous) | New (p2p_glue) | Called from |
|---|---|---|
| `refreshServerListViaRendezvous` | `refreshServerListP2P` | `ServerList` auto-refresh |
| `hostListedViaRendezvous` | `hostP2P(public/private)` | `HostMenu` |
| `joinListedViaRendezvous` | `joinP2P(roomId)` | `ServerList` row / `AddServerMenu` |
| `joinLanRoomByAddressViaRendezvous` | (out of scope — LAN stays TCP) | `DirectConnect` |

Non-fatal warning path (ground rule 4): refresh failure sets a thread-safe
flag consumed once by `ServerList::think` → informational `CoopState(446)`;
browser stays up; Direct Connect (TCP) untouched. Host/join failures MAY set
hard error state (user explicitly acted).

After ICE connects and channels open, session state proceeds exactly as the
TCP path does after `connectTCPServer` succeeds (name exchange, mod-hash
check, lobby) — those messages already flow through g_txQ/g_rxQ untouched.

### 2f. `src/CoopMod/connectionP2P/p2p_config.{h,cpp}`

Mirrors `rendezvous_config.cpp` pattern (compile-time constants a fork can
swap): signaling wss URL, ICE servers:

```
stun:stun.relay.metered.ca:80          (+ stun:stun.l.google.com:19302 alternate)
turn:global.relay.metered.ca:80?transport=udp
turns:global.relay.metered.ca:443?transport=tcp
```

TURN username/credential from the user's free metered.ca account, checked in
(accepted risk: quota burn only; relay carries only symmetric-NAT pairs).
**Blocked on user:** Cloudflare account + `wrangler` auth, metered.ca creds.
Ask before phase 1; stub the values and keep building if not yet available.

## 3. Build integration

- vcpkg: `vcpkg install libdatachannel[ws]:x64-windows` (pulls libjuice,
  usrsctp, OpenSSL). If the repo has no vcpkg manifest, classic mode +
  `vcpkg integrate install` is fine; note what you did in the phase commit.
- Wire include/lib paths into the vcxproj (or extend the
  `Directory.Build.props` pattern — coordinate with the user, theirs is
  uncommitted).
- Add all new sources to `OpenXcom.2010.vcxproj` + `.filters`, and to
  `src/CMakeLists.txt` alongside the existing CoopMod entries (Linux CI /
  other platforms).

## 4. Phases (each ends: green build + acceptance test + commit)

**Phase 1 — Worker.** Build + deploy `signaling-worker/`. Acceptance: with
`wscat` (or a node script committed to `signaling-worker/test/`): create room
→ appears in list; second socket joins → host gets `peer-joined`; offer/answer
/candidate forwarded both ways with `from` filled; closing host socket delists
the room. No game code touched.

**Phase 2 — deps.** libdatachannel builds + links. Acceptance: a temporary
smoke test (behind `OXC_TEST_PORT` harness command or a `--p2p-selftest` CLI
flag) opens two `rtc::PeerConnection`s in-process, connects them loopback,
echoes one message. Full game still builds; TCP session still works.

**Phase 3 — transport + signaling.** Implement 2b + 2c. Acceptance: two game
instances on one PC connect via the deployed Worker + real ICE (host
candidates), exchange echo packets that land in `g_rxQ`, and a >1 MB synthetic
message survives chunking intact (byte-identical). Force-relay flag verified
against Open Relay TURN.

**Phase 4 — glue + UI.** Implement 2d; swap call targets in `ServerList`,
`HostMenu`, `AddServerMenu`. Acceptance: server browser lists a hosted room
with correct metadata; join from browser row reaches lobby; password +
mod-hash checks pass; with Worker URL pointed at an unreachable host, browser
shows CoopState(446) once and Direct Connect TCP still works.

**Phase 5 — end-to-end coop.** Full session over P2P: campaign start, both
players in geoscape, launch mission, battlescape turns both sides, mission
end, save/load. Use the autonomous harness (`tools/coop_test/`,
`coop-test-harness.md`) for the scripted run; biggest packets are base/map/
save-progress transfers at mission start — verify chunk reassembly. Ideally
one real cross-network test (phone hotspot).

**Phase 6 — cutover + deletion.** Remove from build AND repo:
`rendezvous_server.cpp`, `rendezvous_client.{h,cpp}`,
`rendezvous_config.{h,cpp}`, `connection_rendezvous_glue.{h,cpp}`,
`connectionUDP.{h,cpp}`, `connection_udp_glue.{h,cpp}`; drop the libsodium
dep after `grep -r sodium src/` confirms no other user. KEEP:
`connectionTCP.*`, `connection_lan_discovery.*`. Update `coop-networking.md`,
AGENTS.md file maps, and the checklist doc. Acceptance: green build, TCP
session works, P2P session works, no references to deleted symbols.

## 5. Definition of done

- [ ] All 6 phase acceptance tests pass; phases committed separately
- [ ] TCP Direct Connect + LAN discovery regression-tested after phase 6
- [ ] No `g_rxQ.push` outside the mutex; no game/UI calls from rtc callbacks
- [ ] New messages all carry `peerId`; no new hardcoded-2 assumptions
- [ ] Worker + game handle: room-full, room-gone, join-while-closing,
      signaling-down (non-fatal), ICE-failed (clear error dialog, not hang)
- [ ] `clearNetworkSessionQueues()` called on every P2P disconnect path
- [ ] Docs updated (coop-networking.md rewrite, checklist item #1 ticked)

## 6. Known pitfalls (learned the hard way — do not rediscover)

- SPSC queue + multi-threaded rtc callbacks ⇒ mutex (see §1). 
- Refresh-failure must not touch `onConnect` (see ground rule 4).
- vcxproj registration is manual; missing file = silent link failure (§0.6).
- Debug|x64 does not build; don't try to "fix" it in this work.
- `sendTCPPacketStaticData` name is legacy — it is transport-agnostic; do not
  rename it in this refactor (that's checklist item #3's job).
- Mod loading takes ~2.5 min with XCF active and blocks the harness pump —
  XCF is currently deactivated for vanilla testing; keep it that way.
