"""Autonomous test: load the legacy save on a host instance, connect a second
client instance, transfer Jerzy to the client, and verify the client can see
him in the soldier list of the same (host's) base.

Run:  python tools/coop_test/test_transfer_legacy.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import GameClient, make_user_dir, find_soldier, REAL_USER

LEGACY_SAVE = os.path.join(REAL_USER, "xcom1", "91008_103 (all soldiers visible).sav")
SOLDIER = "Jerzy"
LAND_LON, LAND_LAT = 0.7063353365604198, -0.5070346730015731


def establish_session(host, client, host_save):
    """Boot both instances into a locked, lobby-closed coop campaign."""
    host.ok({"cmd": "set_option", "name": "HostSaveProgress", "value": True})
    client.ok({"cmd": "set_option", "name": "HostSaveProgress", "value": True})

    host.ok({"cmd": "load_save", "file": host_save})
    r = host.ok({"cmd": "host_tcp", "server": "TestSrv", "port": "47900", "player": "HostPlayer"})
    assert r["campaign"], "expected campaign save"

    client.ok({"cmd": "join_tcp", "ip": "127.0.0.1", "port": "47900", "player": "ClientPlayer"})
    host.wait_for("client joined", lambda: host.cmd({"cmd": "get_coop"}).get("coopStatic") or None)
    client.wait_for("joined host", lambda: client.cmd({"cmd": "get_coop"}).get("coopStatic") or None)

    # dismiss join-splash profiles (client's OK triggers its campaign setup)
    host.wait_for("host profile", lambda: any("Profile" in s for s in host.cmd({"cmd": "get_state"})["states"]) or None)
    host.ok({"cmd": "profile_ok"})
    client.wait_for("client profile", lambda: any("Profile" in s for s in client.cmd({"cmd": "get_state"})["states"]) or None)
    client.ok({"cmd": "profile_ok"})

    # client: difficulty + first base for its linked campaign
    client.wait_for("difficulty screen", lambda: any("NewGameState" in s for s in client.cmd({"cmd": "get_state"})["states"]) or None)
    client.ok({"cmd": "newgame_ok"})
    client.wait_for("base placement", lambda: any("BuildNewBaseState" in s for s in client.cmd({"cmd": "get_state"})["states"]) or None)
    client.ok({"cmd": "place_first_base", "lon": LAND_LON, "lat": LAND_LAT, "name": "ClientBase"})

    # both in lobby now: ready up, wait for lock, then start
    client.wait_for("client lobby", lambda: any("LobbyMenu" in s for s in client.cmd({"cmd": "get_state"})["states"]) or None)
    client.ok({"cmd": "lobby_ready"})
    host.ok({"cmd": "lobby_ready"})
    client.wait_for("session locked", lambda: client.cmd({"cmd": "get_coop"}).get("sessionLocked") or None, timeout=60)
    host.ok({"cmd": "lobby_ready"})
    client.ok({"cmd": "lobby_ready"})
    client.wait_for(
        "lobby closed + save synced",
        lambda: (lambda c: (c.get("lobbyClosed") and c.get("hasSave")) or None)(client.cmd({"cmd": "get_coop"})),
        timeout=120,
    )
    print("coop session established")


def main():
    host = GameClient("host", 47801, make_user_dir("host-user", [LEGACY_SAVE]))
    client = GameClient("client", 47802, make_user_dir("client-user"))
    try:
        host.spawn(); client.spawn()
        host.connect(); client.connect()

        establish_session(host, client, os.path.basename(LEGACY_SAVE))

        # locate Jerzy + host base id
        hs = host.ok({"cmd": "get_soldiers"})
        hbase, jerzy = find_soldier(hs, SOLDIER)
        assert jerzy, f"{SOLDIER} not found on host"
        host_base_id = hbase["coopBaseId"]
        print(f"host has {jerzy['name']} in '{hbase['name']}' (coopBaseId={host_base_id})")

        # transfer to client (owner id 1)
        host.ok({"cmd": "transfer", "name": SOLDIER, "owner": 1})
        print("transfer sent")

        # verify: client sees Jerzy stationed at the host's base
        def client_sees():
            r = client.cmd({"cmd": "get_mirror_soldiers", "coopBaseId": host_base_id})
            if r.get("ok"):
                for s in r["soldiers"]:
                    if SOLDIER in s["name"] and s["owner"] == 1:
                        return s
            return None

        s = client.wait_for("Jerzy at host base on client", client_sees, timeout=60)
        print(f"PASS: client sees {s['name']} owner={s['owner']} coopBase={s['coopBase']}")

        # verify: host roster no longer contains him
        hs2 = host.ok({"cmd": "get_soldiers"})
        _, still = find_soldier(hs2, SOLDIER)
        assert not still, f"host still has {SOLDIER}"
        print("PASS: gone from host rosters")
        print("TEST PASSED")
    finally:
        host.shutdown(); client.shutdown()


if __name__ == "__main__":
    main()
