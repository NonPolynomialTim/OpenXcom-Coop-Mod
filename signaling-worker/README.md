# OpenXcom co-op signaling Worker

A tiny Cloudflare Worker that does two jobs for the co-op P2P (WebRTC) transport:

1. **Room directory** — hosts advertise a room; browsers list public rooms.
2. **SDP relay** — forwards WebRTC offers/answers/ICE candidates between the
   host and joiners (trickle ICE) until the data channel is up. After that the
   Worker is out of the loop; game traffic is peer-to-peer.

It holds no passwords and never sees game data. Rooms are listed only while the
host's WebSocket is open (presence = liveness), so a room self-delists the
instant the host disconnects.

## Architecture

- `src/index.ts` — router. WS endpoints `/browse`, `/host`, `/join/<roomId>`;
  HTTP `GET /rooms` (debug list), `GET /` (health).
- `src/directory.ts` — `DirectoryDO`, a singleton public room list.
- `src/room.ts` — `RoomDO`, one per room: owns the sockets, assigns peer ids
  (host = 0, joiners = 1..N), relays SDP, keeps its directory row fresh via an
  alarm, delists on host close.
- `src/protocol.ts` — shared message types + constants.

Durable Objects use the **SQLite** storage backend (`new_sqlite_classes`), which
is available on the **free** Workers plan — no paid subscription required.

## Wire protocol (WSS, one JSON object per message)

| Direction | Message |
|---|---|
| host→W (on `/host`) | `{op:"host", name, gamemode, modHash, passwordProtected, maxPlayers, version}` → `{op:"hosted", roomId}` |
| any→W (on `/browse`) | `{op:"list"}` → `{op:"rooms", rooms:[...]}` |
| joiner→W (on `/join/<id>`) | `{op:"join", roomId, playerName}` → `{op:"joined", peerId, hostPeerId, roomId, maxPlayers}`; host gets `{op:"peer-joined", peerId, playerName}`; errors `{op:"error", code:"room-not-found"|"room-full"}` |
| both→W | `{op:"offer"|"answer"|"candidate", to:peerId, sdp|candidate}` → forwarded to target with `from:peerId` added |
| W→both | `{op:"peer-left", peerId}` on socket close |

The host is always peer id `0`; joiners are `1..N`. Address the host with `to:0`.

## Develop & test locally (no Cloudflare account needed)

```
npm install
npx wrangler dev --port 8787      # terminal 1 (local Miniflare)
npm test                          # terminal 2 (node --test)
```

## Deploy (needs a free Cloudflare account)

```
npx wrangler login                # opens a browser to authorize
npx wrangler deploy               # prints https://<name>.<subdomain>.workers.dev
```

The signaling URL the game uses is the deployed URL with `https`→`wss`, e.g.
`wss://openxcom-coop-signaling.<subdomain>.workers.dev`. Put that in
`src/CoopMod/connectionP2P/p2p_config.cpp` (`kSignalingUrl`).

To rename the deployment, edit `name` in `wrangler.toml` before deploying.
