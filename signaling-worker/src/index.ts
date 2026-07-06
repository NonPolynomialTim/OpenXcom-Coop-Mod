// Worker entry point / router for the OpenXcom co-op signaling server.
//
// WebSocket endpoints:
//   /browse            -> DirectoryDO singleton (public room list)
//   /host              -> a fresh RoomDO; the Worker assigns the roomId
//   /join/<roomId>     -> the RoomDO for that room
// HTTP:
//   GET /              -> health/info
//   GET /rooms         -> JSON room list (debug / tests)

import { Env, makeRoomId } from "./protocol";
import { DirectoryDO } from "./directory";
import { RoomDO } from "./room";

export { DirectoryDO, RoomDO };

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    const isWebSocket = request.headers.get("Upgrade") === "websocket";

    // Public room list (debug / non-WS clients).
    if (path === "/rooms" && !isWebSocket) {
      const stub = env.DIRECTORY.get(env.DIRECTORY.idFromName("global"));
      return stub.fetch("https://directory/list");
    }

    if (path === "/" && !isWebSocket) {
      return new Response("OpenXcom co-op signaling server. OK.\n", {
        headers: { "content-type": "text/plain" },
      });
    }

    if (!isWebSocket) {
      return new Response("expected websocket upgrade", { status: 426 });
    }

    // /browse -> directory socket
    if (path === "/browse") {
      const stub = env.DIRECTORY.get(env.DIRECTORY.idFromName("global"));
      return stub.fetch(request);
    }

    // /host -> new room, Worker assigns roomId
    if (path === "/host") {
      const roomId = makeRoomId();
      const stub = env.ROOM.get(env.ROOM.idFromName(roomId));
      return stub.fetch(withRoomHeaders(request, "host", roomId));
    }

    // /join/<roomId> -> that room
    if (path.startsWith("/join/")) {
      const roomId = decodeURIComponent(path.slice("/join/".length)).toUpperCase();
      if (!roomId) return new Response("missing roomId", { status: 400 });
      const stub = env.ROOM.get(env.ROOM.idFromName(roomId));
      return stub.fetch(withRoomHeaders(request, "joiner", roomId));
    }

    return new Response("not found", { status: 404 });
  },
};

// Forwards the upgrade request to a Room DO with role + roomId headers, since a
// DO cannot otherwise learn the name it was addressed by.
function withRoomHeaders(request: Request, role: string, roomId: string): Request {
  const headers = new Headers(request.headers);
  headers.set("X-Role", role);
  headers.set("X-Room-Id", roomId);
  return new Request(request.url, { method: request.method, headers });
}
