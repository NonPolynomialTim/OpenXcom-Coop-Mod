# Co-op networking (TCP, UDP, rendezvous)

How the co-op mod's networking is wired, and — importantly — **what works
without the maintainers' master ("rendezvous") server**. Code lives in
`src/CoopMod/` (TCP in `connectionTCP.*`) and `src/CoopMod/connectionUDP/`
(UDP transport, rendezvous client/server, LAN discovery, glue).

## Two transports

| Transport | Code entry points | Encryption | Needs rendezvous server? |
|-----------|-------------------|-----------|--------------------------|
| **TCP**   | `connectTCPServer()` / `hostTCPServer()` | none (plain SDL_net) | **No** — fully offline/LAN |
| **UDP**   | `host/joinListedViaRendezvous`, `joinLanRoomViaRendezvous`, `joinLanRoomByAddressViaRendezvous`, `startViaRendezvous` | libsodium (per-session key) | **Yes** — always |

## The rendezvous server

"Rendezvous" is the mod's **matchmaking + key-exchange** server. It does two
jobs: (1) lets two peers find each other (server list / room ids / NAT), and
(2) brokers the libsodium **session key** for the encrypted UDP channel.

- Its address and pinned public keys are compiled in via
  `connectionUDP/rendezvous_config.cpp`. **In the open-source build these are
  intentionally blank** (`kRendezvousHost = ""`, ports `0`, placeholder keys),
  so there is no master server.
- The mod can also run as its own standalone server:
  `connectionUDP/rendezvous_server.cpp` has `main()` plus
  `--generate-keys <dir>` (creates box + sign keypairs) and
  `--tcp 39000 --udp 39001 --keys <dir>`. Put the generated **public** keys +
  `127.0.0.1` into `rendezvous_config.cpp` to develop the UDP path locally.

## Why UDP cannot work without rendezvous — even "Direct Connect (UDP)" or LAN

Every UDP path routes connection setup through the rendezvous handshake, which
requires both the pinned keys and a reachable rendezvous host:

- **Direct Connect → NETWORK: UDP** → `joinLanRoomByAddressViaRendezvous`
  (`connection_rendezvous_glue.cpp`). It does a LAN UDP query to discover the
  host's room id, then hands off to `joinLanRoomViaRendezvous`, which:
  - requires built-in keys: `if (!loadBuiltInKeysOrFail(keys)) return false;`
    (blank in this build → sets `onConnect = -3`), and
  - calls `RendezvousClient::joinRoomAndWait(cfg, ...)` against the (empty)
    rendezvous host to complete the handshake.
- **Host → VISIBILITY: PRIVATE (UDP) / PUBLIC (UDP)** → `hostListedViaRendezvous`
  → `loadBuiltInKeysOrFail` + `createRoomAndWait` against the rendezvous host.
- **Server-browser rows / Add Server (UDP)** → `joinListedViaRendezvous` /
  `joinLanRoomViaRendezvous` → same key + handshake requirement.

The deeper reason is keying, not just discovery: the UDP session is encrypted
with libsodium and the **per-session key is produced by the rendezvous
handshake**. `joinRoomAndWait` / `createRoomAndWait` return a
`RendezvousClient::Result` containing that key, and the UDP transport is only
ever started from it (`startUdpFromRendezvousResult`). LAN discovery merely
swaps in the local IP/port for the *data path* after the handshake — the
authentication and keying still go through the rendezvous server first. No
rendezvous server ⇒ no session key ⇒ no UDP.

TCP is the exception precisely because it is plain SDL_net with no libsodium and
no rendezvous (`connectionTCP.cpp`).

## What this means in practice (open-source build)

- **Works offline:** TCP only. Host → `VISIBILITY: PRIVATE (TCP)`; the other
  player joins via Direct Connect → `NETWORK: TCP` with the host IP + port.
  (Over the internet the host must port-forward that TCP port.)
- **Does not work:** the public Server Browser list, and all UDP modes
  (private/public/LAN/direct), until a rendezvous server is configured.
- To enable UDP/browser for development, either run your own rendezvous server
  (above) or obtain the production endpoint/keys from the maintainers.

## Server Browser behavior & the "master server unavailable" notice

Opening New Battle → COOP pushes the `ServerList` browser, which auto-refreshes
via `refreshServerListViaRendezvous`. With no rendezvous config the refresh
fails. To keep this non-fatal (an earlier bug let it tear the browser down — see
below), the refresh path uses **`loadBuiltInKeysOrWarn`** (not
`loadBuiltInKeysOrFail`): it logs, sets a thread-safe flag, and returns false
**without** touching `onConnect`. `ServerList::think` consumes the flag once on
the main thread and shows an informational **`CoopState(446)`** dialog that does
not disconnect or pop the browser — so the player can still use Direct Connect
(TCP). The active host/join paths still use `loadBuiltInKeysOrFail` (a hard
error is appropriate when actually connecting).

### Historic bug (fixed) — don't reintroduce
The old refresh path set the global `onConnect = -3`, which the main loop
escalated into `CoopState(440)` ("Server error. Connection closed."), and that
state's constructor calls `_game->popState()` — which, because of push/construct
ordering, popped the `ServerList` *underneath* it. Net effect: the browser was
destroyed before the user could reach Host / Direct Connect. The fix above keeps
list-refresh failures out of the global connection state.

## File map

- TCP: `src/CoopMod/connectionTCP.{h,cpp}` (also the global `onConnect` state
  machine and the dispatch that turns `onConnect` values into `CoopState` dialogs).
- UDP transport: `src/CoopMod/connectionUDP/connectionUDP.{h,cpp}`.
- Rendezvous client glue: `connectionUDP/connection_rendezvous_glue.{h,cpp}`.
- Rendezvous server (standalone): `connectionUDP/rendezvous_server.cpp`.
- Built-in endpoint/keys: `connectionUDP/rendezvous_config.cpp` (blank in OSS).
- LAN discovery: `connectionUDP/connection_lan_discovery.{h,cpp}`.
- UI: `ServerList`, `DirectConnect`, `HostMenu`, `CoopState` (dialogs) — see
  [`ui-dialog-windows.md`](ui-dialog-windows.md).
- Soldier ownership transfer + coop save persistence (host-save authority):
  `TransferSoldierMenu.*`, `TransferNoticeState.*`, save embedding in
  `Savegame/SavedGame.cpp` — see
  [`coop-soldier-transfer.md`](coop-soldier-transfer.md).
- Autonomous test harness: `TestServer.*` + `tools/coop_test/` — see
  [`coop-test-harness.md`](coop-test-harness.md).
