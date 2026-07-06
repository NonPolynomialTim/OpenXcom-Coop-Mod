// DirectoryDO — the singleton public room directory.
//
// Room DOs register/update/delist their room here over internal HTTP (stub
// fetch). Browser clients connect a WebSocket to /browse and send {op:"list"}
// to receive the current public room list. Room metadata is kept in durable
// storage (survives hibernation) with a lastSeen timestamp so a room whose host
// DO died without a clean delist is swept at list time.

import { Env, RoomMetadata, ServerMessage, ROOM_STALE_MS, send } from "./protocol";

interface StoredRoom {
  meta: RoomMetadata;
  lastSeen: number;
}

export class DirectoryDO {
  private state: DurableObjectState;

  constructor(state: DurableObjectState, _env: Env) {
    this.state = state;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    // Browser list socket.
    if (request.headers.get("Upgrade") === "websocket") {
      const pair = new WebSocketPair();
      const [client, server] = [pair[0], pair[1]];
      this.state.acceptWebSocket(server);
      return new Response(null, { status: 101, webSocket: client });
    }

    // Internal HTTP control plane (called by Room DOs) + debug list.
    switch (url.pathname) {
      case "/register":
      case "/update": {
        const meta = (await request.json()) as RoomMetadata;
        await this.state.storage.put(`room:${meta.roomId}`, {
          meta,
          lastSeen: Date.now(),
        } satisfies StoredRoom);
        return new Response("ok");
      }
      case "/delist": {
        const { roomId } = (await request.json()) as { roomId: string };
        await this.state.storage.delete(`room:${roomId}`);
        return new Response("ok");
      }
      case "/list": {
        const rooms = await this.listRooms();
        return Response.json({ rooms });
      }
      default:
        return new Response("not found", { status: 404 });
    }
  }

  async webSocketMessage(ws: WebSocket, raw: string | ArrayBuffer): Promise<void> {
    let msg: { op?: string };
    try {
      msg = JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw));
    } catch {
      send(ws, { op: "error", code: "bad-request" });
      return;
    }
    if (msg.op === "list") {
      const rooms = await this.listRooms();
      send(ws, { op: "rooms", rooms } satisfies ServerMessage);
    } else {
      send(ws, { op: "error", code: "bad-request" });
    }
  }

  webSocketClose(): void {
    /* browser sockets are stateless request/response; nothing to clean up */
  }

  // Returns current rooms, sweeping any that have gone stale (host DO died
  // without a clean delist).
  private async listRooms(): Promise<RoomMetadata[]> {
    const entries = await this.state.storage.list<StoredRoom>({ prefix: "room:" });
    const now = Date.now();
    const rooms: RoomMetadata[] = [];
    const stale: string[] = [];
    for (const [key, value] of entries) {
      if (now - value.lastSeen > ROOM_STALE_MS) {
        stale.push(key);
        continue;
      }
      rooms.push(value.meta);
    }
    if (stale.length) await this.state.storage.delete(stale);
    return rooms;
  }
}
