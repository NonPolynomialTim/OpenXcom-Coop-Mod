// RoomDO — owns the WebSocket sockets for a single room (one host + N joiners)
// and relays SDP offers/answers/ICE candidates between them (trickle ICE).
// The host is peer 0; joiners are assigned peer ids 1..N. The room is listed in
// the DirectoryDO while the host socket is open (presence = liveness): the host
// socket closing delists it, which makes the old "room never expires" bug
// structurally impossible.
//
// Uses the WebSocket Hibernation API, so per-socket state lives in the socket
// attachment (survives hibernation) rather than instance fields.

import {
  Env,
  RoomMetadata,
  SocketAttachment,
  ClientMessage,
  HOST_PEER_ID,
  KEEPALIVE_MS,
  send,
} from "./protocol";

export class RoomDO {
  private state: DurableObjectState;
  private env: Env;

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    if (request.headers.get("Upgrade") !== "websocket") {
      return new Response("expected websocket", { status: 426 });
    }
    const role = request.headers.get("X-Role"); // "host" | "joiner"
    const roomId = request.headers.get("X-Room-Id") || "";
    if ((role !== "host" && role !== "joiner") || !roomId) {
      return new Response("bad request", { status: 400 });
    }

    const pair = new WebSocketPair();
    const [client, server] = [pair[0], pair[1]];
    this.state.acceptWebSocket(server, [role]);
    const attachment: SocketAttachment = {
      role,
      peerId: role === "host" ? HOST_PEER_ID : -1, // joiner id assigned on join
      roomId,
      playerName: "",
    };
    server.serializeAttachment(attachment);
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, raw: string | ArrayBuffer): Promise<void> {
    let msg: ClientMessage;
    try {
      msg = JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw));
    } catch {
      send(ws, { op: "error", code: "bad-request" });
      return;
    }
    const att = ws.deserializeAttachment() as SocketAttachment;

    switch (msg.op) {
      case "host":
        await this.handleHost(ws, att, msg);
        break;
      case "join":
        await this.handleJoin(ws, att, msg);
        break;
      case "offer":
      case "answer":
      case "candidate":
        this.forward(att, msg);
        break;
      default:
        send(ws, { op: "error", code: "bad-request" });
    }
  }

  async webSocketClose(ws: WebSocket): Promise<void> {
    const att = ws.deserializeAttachment() as SocketAttachment | null;
    if (!att) return;
    if (att.role === "host") {
      // Delete meta first so any joiner-close handlers racing behind this one
      // find no room and cannot re-register it in the directory.
      await this.state.storage.delete("meta");
      await this.directory("delist", att.roomId);
      await this.state.storage.deleteAlarm();
      // Tear down remaining joiners; their P2P data path is dead without a host.
      for (const peer of this.joinerSockets()) {
        send(peer, { op: "peer-left", peerId: HOST_PEER_ID });
        try { peer.close(1000, "host-left"); } catch { /* ignore */ }
      }
    } else if (att.peerId >= 1) {
      // Only touch the directory while the host is still present; otherwise this
      // is a host-initiated teardown and re-registering would resurrect the row.
      const host = this.hostSocket();
      if (host) {
        send(host, { op: "peer-left", peerId: att.peerId });
        await this.refreshPlayerCount();
      }
    }
  }

  async alarm(): Promise<void> {
    if (this.hostSocket()) {
      await this.directory("update");            // refresh lastSeen
      await this.state.storage.setAlarm(Date.now() + KEEPALIVE_MS);
    } else {
      const meta = await this.state.storage.get<RoomMetadata>("meta");
      if (meta) await this.directory("delist", meta.roomId);
    }
  }

  // --- handlers -----------------------------------------------------------

  private async handleHost(
    ws: WebSocket,
    att: SocketAttachment,
    msg: Extract<ClientMessage, { op: "host" }>,
  ): Promise<void> {
    const meta: RoomMetadata = {
      roomId: att.roomId,
      name: msg.name,
      gamemode: msg.gamemode,
      modHash: msg.modHash,
      passwordProtected: msg.passwordProtected,
      players: 1,
      maxPlayers: Math.max(2, msg.maxPlayers | 0),
      version: msg.version,
    };
    await this.state.storage.put("meta", meta);
    await this.state.storage.put("nextPeerId", HOST_PEER_ID);
    await this.directory("register");
    await this.state.storage.setAlarm(Date.now() + KEEPALIVE_MS);
    send(ws, { op: "hosted", roomId: att.roomId });
  }

  private async handleJoin(
    ws: WebSocket,
    att: SocketAttachment,
    msg: Extract<ClientMessage, { op: "join" }>,
  ): Promise<void> {
    const host = this.hostSocket();
    const meta = await this.state.storage.get<RoomMetadata>("meta");
    if (!host || !meta) {
      send(ws, { op: "error", code: "room-not-found" });
      try { ws.close(1000, "room-not-found"); } catch { /* ignore */ }
      return;
    }
    const currentJoiners = this.joinerSockets().length;
    if (1 + currentJoiners >= meta.maxPlayers) {
      send(ws, { op: "error", code: "room-full" });
      try { ws.close(1000, "room-full"); } catch { /* ignore */ }
      return;
    }

    const nextPeerId = ((await this.state.storage.get<number>("nextPeerId")) ?? 0) + 1;
    await this.state.storage.put("nextPeerId", nextPeerId);

    att.peerId = nextPeerId;
    att.playerName = msg.playerName || "";
    ws.serializeAttachment(att);

    send(host, { op: "peer-joined", peerId: att.peerId, playerName: att.playerName });
    send(ws, {
      op: "joined",
      peerId: att.peerId,
      hostPeerId: HOST_PEER_ID,
      roomId: att.roomId,
      maxPlayers: meta.maxPlayers,
    });
    await this.refreshPlayerCount();
  }

  // Relay an offer/answer/candidate to its addressed peer, stamping `from`.
  private forward(
    att: SocketAttachment,
    msg: Extract<ClientMessage, { op: "offer" | "answer" | "candidate" }>,
  ): void {
    const target =
      msg.to === HOST_PEER_ID
        ? this.hostSocket()
        : this.joinerSockets().find(
            (s) => (s.deserializeAttachment() as SocketAttachment).peerId === msg.to,
          );
    if (!target) return; // peer gone; drop silently
    if (msg.op === "candidate") {
      send(target, { op: "candidate", from: att.peerId, candidate: msg.candidate });
    } else {
      send(target, { op: msg.op, from: att.peerId, sdp: msg.sdp });
    }
  }

  // --- helpers ------------------------------------------------------------

  private hostSocket(): WebSocket | undefined {
    return this.state.getWebSockets("host")[0];
  }

  private joinerSockets(): WebSocket[] {
    return this.state
      .getWebSockets("joiner")
      .filter((s) => (s.deserializeAttachment() as SocketAttachment).peerId >= 1);
  }

  private async refreshPlayerCount(): Promise<void> {
    const meta = await this.state.storage.get<RoomMetadata>("meta");
    if (!meta) return;
    meta.players = 1 + this.joinerSockets().length;
    await this.state.storage.put("meta", meta);
    await this.directory("update");
  }

  private async directory(
    action: "register" | "update" | "delist",
    roomId?: string,
  ): Promise<void> {
    const stub = this.env.DIRECTORY.get(this.env.DIRECTORY.idFromName("global"));
    if (action === "delist") {
      await stub.fetch("https://directory/delist", {
        method: "POST",
        body: JSON.stringify({ roomId }),
      });
      return;
    }
    const meta = await this.state.storage.get<RoomMetadata>("meta");
    if (!meta) return;
    await stub.fetch(`https://directory/${action}`, {
      method: "POST",
      body: JSON.stringify(meta),
    });
  }
}
