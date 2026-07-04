"""Autonomous test: save-rollback semantics for soldier transfers.

Spec: a save rollback UNDOES the trade on both sides - the giver keeps or
regains the soldier, the receiver's copy is revoked. Tested in BOTH transfer
directions (host->client and client->host), plus the stacked-notice font fix
(3 notices in a row over the geoscape must all use geoscape popup colors).

Run:  python tools/coop_test/test_transfer_rollback.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import GameClient, make_user_dir
from test_bug_fixes import bootstrap_fresh_session, own_base


def soldier_count(gc, name, owner=None):
    n = 0
    r = gc.ok({"cmd": "get_soldiers"})
    for b in r["bases"]:
        for s in b["soldiers"]:
            if s["name"] == name and (owner is None or s["owner"] == owner):
                n += 1
    return n


def dismiss_all_notices(gc):
    while True:
        r = gc.cmd({"cmd": "dismiss_notice"})
        if not r.get("ok"):
            return


def run_direction(giver, receiver, receiver_owner_id, tag):
    """Both rollback cases for one transfer direction."""
    # Fresh saves can roll IDENTICAL rosters (same RNG seed), so give the
    # test subject a globally unique name for unambiguous counting.
    soldier = f"XferTest {tag}"
    original = own_base(giver)["soldiers"][0]["name"]
    giver.ok({"cmd": "rename_soldier", "name": original, "newName": soldier})

    giver.ok({"cmd": "save_game", "file": f"{tag}_giver_pre.sav"})
    receiver.ok({"cmd": "save_game", "file": f"{tag}_recv_pre.sav"})

    # ---- transfer ----
    giver.ok({"cmd": "transfer", "name": soldier, "owner": receiver_owner_id})
    receiver.wait_for("transfer applied",
                      lambda: (soldier_count(receiver, soldier, owner=receiver_owner_id) == 1) or None, timeout=30)
    dismiss_all_notices(receiver)
    assert soldier_count(giver, soldier) == 0

    # ==== CASE 1: giver rolls back -> trade undone, receiver copy revoked ====
    giver.ok({"cmd": "load_save", "file": f"{tag}_giver_pre.sav"})
    assert soldier_count(giver, soldier) == 1

    receiver.ok({"cmd": "sync_transfer_log"})  # receiver announces its receipts

    receiver.wait_for(f"{tag} case1: receiver copy revoked",
                      lambda: (soldier_count(receiver, soldier, owner=receiver_owner_id) == 0) or None, timeout=30)
    assert soldier_count(giver, soldier) == 1, "giver must keep the resurrected soldier"
    dismiss_all_notices(receiver)
    print(f"PASS {tag} case1: giver rollback undoes trade (giver keeps, receiver revoked)")

    # ---- transfer again for case 2 ----
    giver.ok({"cmd": "transfer", "name": soldier, "owner": receiver_owner_id})
    receiver.wait_for("re-transfer applied",
                      lambda: (soldier_count(receiver, soldier, owner=receiver_owner_id) == 1) or None, timeout=30)
    dismiss_all_notices(receiver)
    giver.ok({"cmd": "save_game", "file": f"{tag}_giver_post.sav"})  # giver saved AFTER

    # ==== CASE 2: receiver rolls back -> trade undone, giver restored ====
    receiver.ok({"cmd": "load_save", "file": f"{tag}_recv_pre.sav"})
    assert soldier_count(receiver, soldier, owner=receiver_owner_id) == 0

    receiver.ok({"cmd": "sync_transfer_log"})  # receiver announces (no receipts)

    giver.wait_for(f"{tag} case2: soldier restored to giver",
                   lambda: (soldier_count(giver, soldier) == 1) or None, timeout=30)
    assert soldier_count(receiver, soldier, owner=receiver_owner_id) == 0
    dismiss_all_notices(giver)
    print(f"PASS {tag} case2: receiver rollback undoes trade (soldier restored to giver)")


def test_stacked_notices(gc):
    """3 notices in a row over the geoscape: every one must use geoscape
    popup colors, not just the first."""
    for i in range(3):
        gc.ok({"cmd": "show_notice", "message": f"stacked notice {i + 1}"})
    cats = gc.ok({"cmd": "get_notices"})["categories"]
    assert len(cats) == 3, f"expected 3 notices, got {cats}"
    assert all(c == "geoManufactureComplete" for c in cats), f"wrong categories: {cats}"
    for _ in range(3):
        gc.ok({"cmd": "dismiss_notice"})
    print("PASS stacked notices: all 3 use geoscape popup colors")


def main():
    host = GameClient("host", 47801, make_user_dir("host-user"))
    client = GameClient("client", 47802, make_user_dir("client-user"))
    try:
        host.spawn(); client.spawn()
        host.connect(); client.connect()
        bootstrap_fresh_session(host, client)

        test_stacked_notices(host)

        run_direction(host, client, receiver_owner_id=1, tag="h2c")
        run_direction(client, host, receiver_owner_id=0, tag="c2h")

        print("TEST PASSED")
    finally:
        host.shutdown(); client.shutdown()


if __name__ == "__main__":
    main()
