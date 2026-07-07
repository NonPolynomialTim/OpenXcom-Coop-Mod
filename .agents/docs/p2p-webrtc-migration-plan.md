# P2P connection rewrite — WebRTC/ICE + Cloudflare Worker signaling (implementation plan)

**Status: approved design, not yet implemented.** Written 2026-07-03 after an
architecture/risk analysis session. This doc is the spec for the implementing
agent. Read `coop-networking.md` first for how the *current* networking works.

## Goal

Replace the custom rendezvous server + custom encrypted-UDP transport with a
zero-maintenance, zero-cost, GPL-clean stack:

| Job | Current (to be replaced) | New |
|---|---|---|
| NAT observation/punch | custom rendezvous daemon (raw TCP 39000 + UDP 39001 on a VPS) | ICE via **libdatachannel** + free public STUN |
| Relay fallback (symmetric NAT) | none (punch fails ⇒ fails) | **Open Relay Project** TURN (metered.ca, free 20 GB/mo) |
| Session encryption/keying | libsodium key brokered by rendezvous server | DTLS (built into WebRTC data channels) |
| Room list + peer introduction (SDP signaling) | rendezvous server | **Cloudflare Worker + Durable Objects** over WSS (free tier) |
| Reliability/fragmentation | hand-rolled NACK/fragment layer in `connectionUDP.cpp` | SCTP data channels (native reliable/ordered, arbitrary message size — chunk >256 KB messages anyway) |

**Explicitly rejected: Epic Online Services P2P relay.** EOS SDK license is
GPL-incompatible both directions (Epic terms forbid combining with GPL; GPLv3
forbids linking proprietary SDK; upstream OpenXcom copyright can't be
relicensed). Reverse-engineering the EOS service violates the EOS Developer
Agreement (service access only via SDK). Do not revisit unless the user asks.

## Hard requirements

1. **4-player-capable plumbing.** Game logic is 2-player today (see "Current
   2-player assumptions" below) and stays 2-player in this migration, but the
   transport/signaling layers MUST NOT bake in 2: every message carries a
   `peerId`, host holds N peer connections (star topology, host = hub), room
   `maxPlayers` plumbed end-to-end (field already exists in the old protocol).
2. **Keep the plain-TCP path untouched** (`connectionTCP.cpp` hosting/direct
   connect). It is the offline/LAN/port-forward fallback and the safety net
   during migration.
3. **Graceful degradation.** Signaling unreachable ⇒ non-fatal notice, browser
   stays open, Direct Connect (TCP) + LAN still work. Reuse the
   `consumeMasterServerUnavailableWarning()` pattern
   (`connection_rendezvous_glue.cpp` / `ServerList::think` / `CoopState(446)`).
   Do NOT reintroduce the historic teardown bug (see coop-networking.md
   "Historic bug (fixed)").
4. **GPL-clean deps only** (MIT/BSD/Apache). No proprietary SDKs.

## Architecture

```
                 Cloudflare Worker (TS, WSS + JSON)
                /        |                \
   directory DO     per-room DO       (free tier; ~300 lines)
   (public list)    (socket registry, SDP forwarding)
        |                |
   game (browser)   host + joiners  ── after SDP exchange, server out of loop
                         |
                    libdatachannel ICE:
                    direct P2P (STUN punch, ~85-90%)
                    else TURN relay (Open Relay)
                    DTLS-encrypted SCTP data channels
                         |
                 feeds existing g_txQ/g_rxQ seam
```

### Game↔Worker signaling protocol (WSS, JSON messages)

Design fresh; old reference implementation is `rendezvous_server.cpp` (room
semantics) but do not copy its wire format. Suggested messages:

- Host: `{op:"host", name, gamemode, modHash, passwordProtected, maxPlayers, version}`
  → `{op:"hosted", roomId}`; room listed while socket open (presence = liveness,
  fixes old `kRoomTtlMs=0` never-expire bug for free — socket close ⇒ delist)
- Browser: `{op:"list"}` → `{op:"rooms", rooms:[...]}`
- Join: `{op:"join", roomId, playerName}` → Worker forwards
  `{op:"peer-offer", peerId, sdp}` / `{op:"peer-answer", peerId, sdp}` /
  `{op:"ice-candidate", peerId, candidate}` between joiner and host (trickle ICE)
- Password check stays app-level after connect (same as today).
- Version/mod-hash compatibility fields included so the browser can grey out
  incompatible rows (client already has `hasRequiredMods`).

### ICE server config (client side)

```
stun:stun.relay.metered.ca:80        (plus 1-2 public STUN alternates)
turn:global.relay.metered.ca:80?transport=udp
turns:global.relay.metered.ca:443?transport=tcp    (firewall fallback)
```
TURN username/credential from a free metered.ca account. Checked into a config
file is acceptable (risk = quota burn only; expected usage ≪ 100 MB/mo since
relay only carries symmetric-NAT pairs). Put endpoints+creds in a new
`src/CoopMod/connectionP2P/p2p_config.cpp` mirroring the `rendezvous_config.cpp`
pattern so forks can swap providers. Signaling URL (wss://….workers.dev) same file.

## Integration seam (the key to a low-risk migration)

Game logic is decoupled from transport via two SPSC queues of whole-message
strings: `g_txQ` / `g_rxQ` in `connectionTCP.h:136` + `enqueueTx()` +
`sendTCPPacketStaticData()` (name is legacy; it's transport-agnostic).
The new transport is a third backend feeding the same queues, exactly like
`connectionUDP` does today.

**4-player note:** SPSC = single peer by construction. Either (a) tag messages
with peerId inside the string envelope and keep one queue pair (host fan-out
happens in the transport layer), or (b) per-peer queue pairs. Prefer (a) for
minimal churn now; the JSON packet envelope gains a `peerId` field.

## Module plan

New: `src/CoopMod/connectionP2P/`
- `p2p_transport.{h,cpp}` — libdatachannel wrapper: N `rtc::PeerConnection`s
  (host) / 1 (client), data channels (one reliable-ordered channel is enough
  initially), pumps g_txQ/g_rxQ, chunks messages >256 KB (save/map transfers)
- `p2p_signaling.{h,cpp}` — WSS client to the Worker (reuse libdatachannel's
  built-in `rtc::WebSocket` — it ships one; no extra dep)
- `p2p_glue.{h,cpp}` — replaces `connection_rendezvous_glue.cpp` flows:
  host/join/refresh entry points called by ServerList/HostMenu/DirectConnect,
  same non-fatal-warning pattern
- `p2p_config.{h,cpp}` — endpoints/creds (see above)

New repo-side: `signaling-worker/` (TypeScript, wrangler.toml, directory DO +
room DO with WebSocket hibernation). Deployed with `wrangler deploy`.

Deleted at the end (not before cutover verified): `rendezvous_server.cpp`,
`rendezvous_client.{h,cpp}`, `rendezvous_config.{h,cpp}`,
`connection_rendezvous_glue.{h,cpp}` (80 KB), `connectionUDP.{h,cpp}`
(fragmentation/NACK layer), libsodium dependency (verify nothing else uses it).
Keep: `connectionTCP.*` (fallback), `connection_lan_discovery.*` (LAN browsing —
optionally later carries SDP for serverless LAN-ICE, not in scope now).

## Build integration (Windows, MSVC — the fiddly part)

- Solution: `src\OpenXcom.2010.sln`, **Release|x64 only** (Debug unmaintained).
  Build: `MSBuild src\OpenXcom.2010.sln -p:Configuration=Release -p:Platform=x64 -m`
- Add libdatachannel via vcpkg (pulls libjuice, usrsctp, OpenSSL or MbedTLS).
  Wire into `OpenXcom.2010.vcxproj` — note AGENTS.md warning: committed vcxproj
  lags on CoopMod files; user has a local `Directory.Build.props` (not in git)
  that supplies include/link scaffolding. New sources MUST be registered or
  they silently don't link.
- New `.cpp` files under `src/CoopMod/` must be added to the vcxproj (and
  ideally CMakeLists.txt for other platforms).

## Current 2-player assumptions (do NOT fix in this migration — but don't add new ones)

- Host accepts exactly one TCP client: `connectionTCP.cpp:1556`
  (`SDLNet_AllocSocketSet(2)`, single `clientSock`)
- `room.maxPlayers` defaulted/hardcoded 2: `connection_rendezvous_glue.cpp:253`
- Singular peer state throughout `connectionTCP.h` (`peerTimeSpeedId`,
  `other_time_speed_coop`, `lastPeerTimePacketMs`, `_playerTurn`, gamemodes)

## Migration order (each step buildable + testable)

1. **Worker first** — write + deploy signaling worker, test with a throwaway
   WS script. Deliverable: room create/list/join/SDP-forward works via wscat.
2. **vcpkg/libdatachannel into the build** — compile+link a stub that opens a
   loopback PeerConnection. Deliverable: green build with new deps.
3. **p2p_transport + p2p_signaling** — two local game instances connect via
   Worker + ICE on one machine; echo packets over data channel into g_rxQ.
4. **p2p_glue + UI wiring** — ServerList refresh/list rows via Worker; Host
   (public/private) and Join flows create real sessions; password + mod-hash
   checks; non-fatal notice path when Worker unreachable.
5. **Full coop session end-to-end** — campaign + battlescape sync over the new
   transport (biggest packets: save/map transfer at mission start — verify
   chunking). Two machines / two networks ideally.
6. **Cutover + deletion** — remove rendezvous/UDP legacy files from build and
   repo, update `coop-networking.md` + AGENTS.md file maps.

## Testing notes

- Two instances on one PC: ICE host-candidates connect locally (no NAT) —
  validates plumbing, not traversal. Real NAT test needs two networks (e.g.
  one peer on phone hotspot).
- Force-TURN test: libdatachannel `rtc::Configuration` — set ICE transport
  policy to relay-only to verify Open Relay path works before shipping.
- Regression: TCP direct connect + LAN discovery must still work at every step.
- The soak concern from the old system (rooms never expiring) is solved by
  socket-presence = liveness; verify DO evicts rooms on WS close/hibernate
  timeout.

## Open decisions for the implementer (small, decide in-session)

- One reliable channel vs. reliable + unreliable pair (current game protocol
  assumes reliable ordered for everything — start with one reliable channel)
- Exact message envelope: current packets are JSON strings; add `peerId` +
  `seq` fields vs. wrap in a binary header. Prefer staying JSON for
  debuggability; size is irrelevant at this traffic scale.
- workers.dev subdomain vs custom domain (workers.dev fine; config-file swap)

## Account setup (user action, before step 1)

- Cloudflare account (free) + `wrangler` CLI auth — for the Worker
- metered.ca account (free) — for TURN credentials
```
