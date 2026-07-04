"""Autonomous test: save-rollback reconciliation for soldier transfers.

Scenario from the bug report:
  host has A,B; transfers B to client; host quits WITHOUT saving; on the next
  session host's save still contains B while the client's contains B too ->
  duplicate. Symmetrically, a client rollback would make B vanish everywhere.

The fix: transfer receipts persisted inside each save + a receipt exchange on
session start (reconcileTransferLog). This test simulates rollbacks in-session
via load_save (which resets the in-memory receipt log from the file, exactly
like a fresh boot) and forces the exchange with sync_transfer_log.

Run:  python tools/coop_test/test_transfer_rollback.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import GameClient, make_user_dir
from test_bug_fixes import bootstrap_fresh_session, own_base


def soldier_count(gc, name, owner=None):
    """Count soldiers by name; owner filter separates a transferred copy
    (owner==1) from an unrelated same-name soldier the other save may have
    rolled from the same RNG name pool."""
    n = 0
    r = gc.ok({"cmd": "get_soldiers"})
    for b in r["bases"]:
        for s in b["soldiers"]:
            if s["name"] == name and (owner is None or s["owner"] == owner):
                n += 1
    return n


def main():
    host = GameClient("host", 47801, make_user_dir("host-user"))
    client = GameClient("client", 47802, make_user_dir("client-user"))
    try:
        host.spawn(); client.spawn()
        host.connect(); client.connect()
        bootstrap_fresh_session(host, client)

        hbase = own_base(host)
        soldier = hbase["soldiers"][0]["name"]
        host_base_id = hbase["coopBaseId"]

        # pre-transfer snapshots (the states a no-save quit would roll back to)
        host.ok({"cmd": "save_game", "file": "host_pre.sav"})
        client.ok({"cmd": "save_game", "file": "client_pre.sav"})

        # --- transfer B to the client ---
        host.ok({"cmd": "transfer", "name": soldier, "owner": 1})

        def client_has():
            r = client.cmd({"cmd": "get_mirror_soldiers", "coopBaseId": host_base_id})
            return r.get("ok") and any(s["name"] == soldier for s in r["soldiers"]) or None

        client.wait_for("transfer applied", client_has, timeout=30)
        client.ok({"cmd": "dismiss_notice"})
        assert soldier_count(host, soldier) == 0
        baseline_client_own = soldier_count(client, soldier) - soldier_count(client, soldier, owner=1)

        # ============ CASE 1: host rolls back (duplicate) ============
        host.ok({"cmd": "load_save", "file": "host_pre.sav"})
        assert soldier_count(host, soldier) == 1, "rollback should resurrect the soldier on the host"
        log = host.ok({"cmd": "get_transfer_log"})["entries"]
        assert not log, f"rolled-back host save should have no receipts, got {log}"

        # receipt exchange (as a reconnect would do)
        client.ok({"cmd": "sync_transfer_log"})

        host.wait_for("stale duplicate removed on host",
                      lambda: (soldier_count(host, soldier) == 0) or None, timeout=30)
        assert soldier_count(client, soldier, owner=1) == 1
        print(f"PASS case1: host rollback healed - '{soldier}' exists exactly once (client)")

        # ============ CASE 2: client rolls back (vanished) ============
        client.ok({"cmd": "load_save", "file": "client_pre.sav"})
        assert soldier_count(client, soldier, owner=1) == 0, "client rollback should drop the received soldier"

        # the CLIENT announces its (rolled back, receipt-less) state; the host
        # sees its sent-receipt unacknowledged and resends. On a real
        # reconnect both sides announce automatically.
        client.ok({"cmd": "sync_transfer_log"})

        def client_regained():
            return (soldier_count(client, soldier, owner=1) == 1) or None

        client.wait_for("transfer resent to rolled-back client", client_regained, timeout=30)
        client.ok({"cmd": "dismiss_notice"})
        assert soldier_count(host, soldier) == 0
        print(f"PASS case2: client rollback healed - '{soldier}' resent, exists exactly once (client)")

        print("TEST PASSED")
    finally:
        host.shutdown(); client.shutdown()


if __name__ == "__main__":
    main()
