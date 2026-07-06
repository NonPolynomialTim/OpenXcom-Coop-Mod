// Phase 1 acceptance test for the signaling Worker.
//
// Requires the Worker running locally first, e.g.:
//   npm install
//   npx wrangler dev --port 8787            (in another terminal)
//   npm test
//
// Override the target with SIGNALING_HTTP (default http://127.0.0.1:8787).

import { test } from "node:test";
import assert from "node:assert/strict";
import WebSocket from "ws";

const HTTP_BASE = process.env.SIGNALING_HTTP || "http://127.0.0.1:8787";
const WS_BASE = HTTP_BASE.replace(/^http/, "ws");

// A WebSocket wrapper that buffers incoming JSON messages so a message can be
// awaited even if it arrives before we start listening.
class Client {
  constructor(ws) {
    this.ws = ws;
    this.queue = [];
    this.waiters = [];
    ws.on("message", (data) => {
      const msg = JSON.parse(data.toString());
      const waiter = this.waiters.shift();
      if (waiter) waiter(msg);
      else this.queue.push(msg);
    });
  }
  static connect(path) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(WS_BASE + path);
      ws.on("open", () => resolve(new Client(ws)));
      ws.on("error", reject);
    });
  }
  send(obj) {
    this.ws.send(JSON.stringify(obj));
  }
  next(timeoutMs = 4000) {
    if (this.queue.length) return Promise.resolve(this.queue.shift());
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("timeout waiting for message")), timeoutMs);
      this.waiters.push((msg) => {
        clearTimeout(timer);
        resolve(msg);
      });
    });
  }
  close() {
    this.ws.close();
  }
}

async function listRooms() {
  const res = await fetch(`${HTTP_BASE}/rooms`);
  assert.equal(res.status, 200);
  const body = await res.json();
  return body.rooms;
}

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

test("host -> list -> join -> SDP relay -> host-close delists", async () => {
  // 1. Host a room.
  const host = await Client.connect("/host");
  host.send({
    op: "host",
    name: "Test Room",
    gamemode: 1,
    modHash: "abc123",
    passwordProtected: false,
    maxPlayers: 2,
    version: "1.8.3",
  });
  const hosted = await host.next();
  assert.equal(hosted.op, "hosted");
  assert.ok(hosted.roomId, "roomId assigned");
  const roomId = hosted.roomId;

  // 2. Room appears in the public list with correct metadata.
  await delay(200);
  let rooms = await listRooms();
  const row = rooms.find((r) => r.roomId === roomId);
  assert.ok(row, "hosted room is listed");
  assert.equal(row.name, "Test Room");
  assert.equal(row.gamemode, 1);
  assert.equal(row.modHash, "abc123");
  assert.equal(row.passwordProtected, false);
  assert.equal(row.maxPlayers, 2);
  assert.equal(row.players, 1);
  assert.equal(row.version, "1.8.3");

  // 3. A joiner joins; host is notified and joiner learns its peerId.
  const joiner = await Client.connect(`/join/${roomId}`);
  joiner.send({ op: "join", roomId, playerName: "Bob" });
  const peerJoined = await host.next();
  assert.equal(peerJoined.op, "peer-joined");
  assert.equal(peerJoined.playerName, "Bob");
  const peerId = peerJoined.peerId;
  assert.ok(peerId >= 1);

  const joined = await joiner.next();
  assert.equal(joined.op, "joined");
  assert.equal(joined.peerId, peerId);
  assert.equal(joined.hostPeerId, 0);

  // Player count reflects the join.
  await delay(200);
  rooms = await listRooms();
  assert.equal(rooms.find((r) => r.roomId === roomId).players, 2);

  // 4. offer joiner -> host (from stamped).
  joiner.send({ op: "offer", to: 0, sdp: "OFFER_SDP" });
  const offer = await host.next();
  assert.equal(offer.op, "offer");
  assert.equal(offer.from, peerId);
  assert.equal(offer.sdp, "OFFER_SDP");

  // 5. answer host -> joiner.
  host.send({ op: "answer", to: peerId, sdp: "ANSWER_SDP" });
  const answer = await joiner.next();
  assert.equal(answer.op, "answer");
  assert.equal(answer.from, 0);
  assert.equal(answer.sdp, "ANSWER_SDP");

  // 6. candidates both ways.
  joiner.send({ op: "candidate", to: 0, candidate: "CAND_A" });
  const candA = await host.next();
  assert.equal(candA.op, "candidate");
  assert.equal(candA.from, peerId);
  assert.equal(candA.candidate, "CAND_A");

  host.send({ op: "candidate", to: peerId, candidate: "CAND_B" });
  const candB = await joiner.next();
  assert.equal(candB.op, "candidate");
  assert.equal(candB.from, 0);
  assert.equal(candB.candidate, "CAND_B");

  // 7. Joiner also gets peer-left, and closing the host delists the room.
  host.close();
  const left = await joiner.next();
  assert.equal(left.op, "peer-left");
  assert.equal(left.peerId, 0);

  await delay(400);
  rooms = await listRooms();
  assert.ok(!rooms.find((r) => r.roomId === roomId), "room delisted after host close");

  joiner.close();
});

test("room-not-found for unknown room", async () => {
  const joiner = await Client.connect("/join/ZZZZZZ");
  joiner.send({ op: "join", roomId: "ZZZZZZ", playerName: "Nobody" });
  const err = await joiner.next();
  assert.equal(err.op, "error");
  assert.equal(err.code, "room-not-found");
  joiner.close();
});

test("room-full rejects the extra joiner", async () => {
  const host = await Client.connect("/host");
  host.send({
    op: "host",
    name: "Duo",
    gamemode: 0,
    modHash: "",
    passwordProtected: false,
    maxPlayers: 2,
    version: "1.8.3",
  });
  const { roomId } = await host.next();

  const j1 = await Client.connect(`/join/${roomId}`);
  j1.send({ op: "join", roomId, playerName: "First" });
  await j1.next(); // joined
  await host.next(); // peer-joined

  const j2 = await Client.connect(`/join/${roomId}`);
  j2.send({ op: "join", roomId, playerName: "Second" });
  const err = await j2.next();
  assert.equal(err.op, "error");
  assert.equal(err.code, "room-full");

  host.close();
  j1.close();
  j2.close();
});

test("list over the /browse websocket", async () => {
  const host = await Client.connect("/host");
  host.send({
    op: "host",
    name: "Browsable",
    gamemode: 2,
    modHash: "h",
    passwordProtected: true,
    maxPlayers: 4,
    version: "1.8.3",
  });
  const { roomId } = await host.next();
  await delay(200);

  const browser = await Client.connect("/browse");
  browser.send({ op: "list" });
  const rooms = await browser.next();
  assert.equal(rooms.op, "rooms");
  assert.ok(rooms.rooms.find((r) => r.roomId === roomId && r.passwordProtected === true));

  host.close();
  browser.close();
});
