"""Diagnostic: step through host/join/lobby printing state stacks + coop flags."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import GameClient, make_user_dir, REAL_USER

LEGACY_SAVE = os.path.join(REAL_USER, "xcom1", "91008_103 (all soldiers visible).sav")


def dump(gc, label):
    st = gc.cmd({"cmd": "get_state"})["states"]
    co = gc.cmd({"cmd": "get_coop"})
    flags = {k: co.get(k) for k in ("coopStatic", "coopCampaign", "coopSession", "sessionLocked",
                                    "playerReady", "playersReady", "lobbyClosed", "lobbyFileStatus",
                                    "hasSave", "onConnect", "host", "serverOwner")}
    print(f"--- {gc.name} [{label}] states={[s.split('::')[-1] for s in st]} {flags}", flush=True)


def main():
    host = GameClient("host", 47801, make_user_dir("host-user", [LEGACY_SAVE]))
    client = GameClient("client", 47802, make_user_dir("client-user"))
    try:
        host.spawn(); client.spawn()
        host.connect(); client.connect()

        host.ok({"cmd": "set_option", "name": "HostSaveProgress", "value": True})
        client.ok({"cmd": "set_option", "name": "HostSaveProgress", "value": True})

        host.ok({"cmd": "load_save", "file": os.path.basename(LEGACY_SAVE)})
        dump(host, "after load_save")

        host.ok({"cmd": "host_tcp", "server": "TestSrv", "port": "47900", "player": "HostPlayer"})
        dump(host, "after host_tcp")

        client.ok({"cmd": "join_tcp", "ip": "127.0.0.1", "port": "47900", "player": "ClientPlayer"})
        time.sleep(4)
        dump(host, "4s after join"); dump(client, "4s after join")

        # dismiss the join-splash Profile screens; on the client this fires
        # request_load_progress (the save handoff) when host_save_progress on
        for gc in (host, client):
            r = gc.cmd({"cmd": "profile_ok"})
            print(f"{gc.name} profile_ok -> {r}", flush=True)

        time.sleep(3)
        dump(host, "after profile_ok"); dump(client, "after profile_ok")

        # client: confirm difficulty for its linked coop campaign
        r = client.cmd({"cmd": "newgame_ok"})
        print(f"client newgame_ok -> {r}", flush=True)
        time.sleep(3)
        dump(host, "after newgame_ok"); dump(client, "after newgame_ok")

        # client: place + name its first base (sodo's coords = known land)
        r = client.cmd({"cmd": "place_first_base", "lon": 0.7063353365604198, "lat": -0.5070346730015731, "name": "ClientBase"})
        print(f"client place_first_base -> {r}", flush=True)
        time.sleep(3)
        dump(host, "after place_base"); dump(client, "after place_base")

        # try ready on both (whoever has a lobby)
        for gc in (client, host):
            r = gc.cmd({"cmd": "lobby_ready"})
            print(f"{gc.name} lobby_ready -> {r}", flush=True)

        # wait for the 30s countdown to lock the session
        for i in range(12):
            time.sleep(5)
            co = client.cmd({"cmd": "get_coop"})
            if co.get("sessionLocked"):
                print(f"session locked after ~{5*(i+1)}s", flush=True)
                break
        dump(host, "locked"); dump(client, "locked")

        # locked: the lobby button now closes the lobby / starts the game
        for gc in (host, client):
            r = gc.cmd({"cmd": "lobby_ready"})
            print(f"{gc.name} lobby start -> {r}", flush=True)

        for i in range(24):
            time.sleep(5)
            dump(host, f"start-poll {i}"); dump(client, f"start-poll {i}")
            co = client.cmd({"cmd": "get_coop"})
            if co.get("lobbyClosed") and co.get("hasSave"):
                print("client lobby closed, save present!", flush=True)
                soldiers = client.cmd({"cmd": "get_soldiers"})
                print("client bases:", [(b["name"], len(b["soldiers"]), b["coopBaseId"]) for b in soldiers.get("bases", [])], flush=True)
                hsold = host.cmd({"cmd": "get_soldiers"})
                print("host bases:", [(b["name"], len(b["soldiers"]), b["coopBaseId"]) for b in hsold.get("bases", [])], flush=True)
                break
    finally:
        host.shutdown(); client.shutdown()


if __name__ == "__main__":
    main()
