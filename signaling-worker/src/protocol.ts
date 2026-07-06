// Shared wire-protocol types and constants for the OpenXcom co-op signaling
// Worker. One JSON object per WebSocket message; the `op` field discriminates.
//
// Endpoints (WSS):
//   /browse            -> DirectoryDO   (public room list)
//   /host              -> RoomDO        (Worker assigns roomId)
//   /join/<roomId>     -> RoomDO(roomId)
// Debug (HTTP):
//   GET /rooms         -> JSON room list

// The host is always peer 0 in a room; joiners are assigned 1..N.
export const HOST_PEER_ID = 0;

// A room is delisted from the directory this long after its last keepalive if
// the host DO failed to delist cleanly (belt-and-suspenders; normal delist is
// on host socket close).
export const ROOM_STALE_MS = 90_000;

// Host DO refreshes its directory presence on this cadence while listed.
export const KEEPALIVE_MS = 30_000;

export interface RoomMetadata {
  roomId: string;
  name: string;
  gamemode: number;
  modHash: string;
  passwordProtected: boolean;
  players: number;
  maxPlayers: number;
  version: string;
}

// Per-socket data persisted across hibernation via serializeAttachment().
export interface SocketAttachment {
  role: "host" | "joiner";
  peerId: number;
  roomId: string;
  playerName: string;
}

export type ClientMessage =
  | { op: "host"; name: string; gamemode: number; modHash: string; passwordProtected: boolean; maxPlayers: number; version: string }
  | { op: "list" }
  | { op: "join"; roomId: string; playerName: string }
  | { op: "offer"; to: number; sdp: string }
  | { op: "answer"; to: number; sdp: string }
  | { op: "candidate"; to: number; candidate: string };

export type ServerMessage =
  | { op: "hosted"; roomId: string }
  | { op: "rooms"; rooms: RoomMetadata[] }
  | { op: "joined"; peerId: number; hostPeerId: number; roomId: string; maxPlayers: number }
  | { op: "peer-joined"; peerId: number; playerName: string }
  | { op: "peer-left"; peerId: number }
  | { op: "offer"; from: number; sdp: string }
  | { op: "answer"; from: number; sdp: string }
  | { op: "candidate"; from: number; candidate: string }
  | { op: "error"; code: "room-not-found" | "room-full" | "bad-request" };

export interface Env {
  DIRECTORY: DurableObjectNamespace;
  ROOM: DurableObjectNamespace;
}

export function send(ws: WebSocket, msg: ServerMessage): void {
  try {
    ws.send(JSON.stringify(msg));
  } catch {
    /* socket already closing; ignore */
  }
}

// Short, human-friendly, unambiguous room id (Crockford-ish base32, no I/L/O/U).
export function makeRoomId(len = 6): string {
  const alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += alphabet[b % alphabet.length];
  return out;
}
