"""Autonomous 2-player X-Com Files playthrough harness.

Builds on the coop test harness (harness.py, TestServer). Brings up a live
host+client coop session with X-Com Files ACTIVE (Veteran, XCF default), reaches
the geoscape, advances time in lockstep, detects event popups, and backs up.

The isolated -user dirs seeded by make_user_dir carry the real options.cfg where
x-com-files is active:false (vanilla testing). activate_xcf() flips it per-dir so
the playthrough runs the mod without touching the user's real cfg.

Usage:
    python tools/coop_test/play_harness.py bringup       # start + snapshot + backup
    python tools/coop_test/play_harness.py advance [hrs] # start then advance & log events
"""
import ctypes
import os
import re
import shutil
import sys
import time

# XCF soldier/agent names contain non-Latin1 chars (e.g. Polish 'ś'); when stdout
# is redirected to a file it defaults to cp1252 and crashes. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Keep the display awake for the whole run: a screen lock/sleep kills the SDL
# video context in the background game windows -> std::terminate crash. This was
# the cause of intermittent mid-run crashes during long geoscape/flight waits.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    except Exception as e:
        print(f"(keep_awake failed: {e})")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import GameClient, make_user_dir, REPO
from test_bug_fixes import bootstrap_fresh_session, own_base

BACKUP_DIR = os.path.join(REPO, "tools", "coop_test", "playthrough_backups")
GEO = "class OpenXcom::GeoscapeState"
SPEED = {"5s": 0, "1min": 1, "5min": 2, "30min": 3, "1hr": 4, "1day": 5}

# Playbook: dangerous cryptids to AVOID with rookies (skip these sites).
AVOID_RACE = {"STR_FENRIR", "STR_CHUPACABRA", "STR_WERECAT", "STR_WEREWOLF",
              "STR_REAPER", "STR_SPINEBOAR", "STR_ZOMBIE", "STR_GIANT_RAT_TERROR"}
# Engage-worthy: cult apprehensions (weak humans, capture alive).
ENGAGE_HINTS = ("CULT", "APPREHENSION", "DAGON", "RED_DAWN", "BLACK_LOTUS",
                "EXALT", "OSIRON")


def classify_site(site):
    race, typ = site.get("race", ""), site.get("type", "")
    if race in AVOID_RACE:
        return "AVOID"
    if any(h in typ or h in race for h in ENGAGE_HINTS):
        return "ENGAGE"
    return "UNKNOWN"


def activate_xcf(user_dir):
    cfg_path = os.path.join(user_dir, "options.cfg")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = f.read()
    new = re.sub(r"- active: false\n(\s*id: x-com-files)", r"- active: true\n\1", cfg)
    if new == cfg:
        raise RuntimeError(f"could not activate XCF in {cfg_path}")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(new)


def make_xcf_user_dir(name):
    d = make_user_dir(name)
    activate_xcf(d)
    return d


def find_newest_sav(user_dir):
    newest, newest_m = None, -1.0
    for root, _dirs, files in os.walk(user_dir):
        for fn in files:
            if fn.endswith(".sav"):
                p = os.path.join(root, fn)
                m = os.path.getmtime(p)
                if m > newest_m:
                    newest, newest_m = p, m
    return newest


def top_state(gc):
    return gc.cmd({"cmd": "get_state"})["states"][-1]


def fmt_date(t):
    return f"{t['year']}-{t['month']:02d}-{t['day']:02d} {t['hour']:02d}:{t['minute']:02d}"


class Session:
    """A live host+client coop XCF geoscape, kept running for driving."""

    def __init__(self):
        self.host = GameClient("host", 47801, make_xcf_user_dir("host-user"))
        self.client = GameClient("client", 47802, make_xcf_user_dir("client-user"))
        self.both = (self.host, self.client)
        self._logged_sites = set()
        self.missions = []   # (side, site, classification)
        self.bugs = []       # (kind, detail) cross-validation / crash findings

    def _sites(self, gc):
        return {(s["id"], s["type"], s["race"]) for s in self.geo(gc)["missionSites"]}

    def cross_validate(self):
        """Bug-bash: host and client must agree on the shared geoscape world
        (mission sites). A brand-new site can appear on one machine a tick before
        the sync reaches the other -> re-sample after a short delay and only flag
        a mismatch that PERSISTS (a real desync, not detection-timing skew)."""
        try:
            hs, cs = self._sites(self.host), self._sites(self.client)
            if hs == cs:
                return
            time.sleep(2.5)  # let any in-flight site-sync packet land
            hs, cs = self._sites(self.host), self._sites(self.client)
            if hs == cs:
                return  # transient — resolved, not a bug
        except Exception as e:
            self.bugs.append(("geo_state_error", repr(e)))
            return
        self.bugs.append(("site_mismatch",
                          {"host_only": list(hs - cs), "client_only": list(cs - hs)}))
        print(f"  !! BUG site mismatch (persistent) host={hs} client={cs}")

    def start(self):
        keep_awake()
        self.host.spawn(); self.client.spawn()
        self.host.connect(timeout=240); self.client.connect(timeout=240)
        print("both connected; parsing XCF + bootstrapping coop...")
        bootstrap_fresh_session(self.host, self.client)
        for gc in self.both:
            st = top_state(gc)
            assert st == GEO, f"{gc.name} not on geoscape: {st}"
        print("session live on geoscape")

    def geo(self, gc):
        return gc.ok({"cmd": "geo_state"})

    def snapshot(self, tag=""):
        for gc in self.both:
            gs = self.geo(gc)
            own = [b for b in gs["bases"] if b["soldiers"] > 0]
            line = (f"[{gc.name}] {fmt_date(gs['time'])} funds={gs['funds']} "
                    f"mo={gs['monthsPassed']} ufos={len(gs['ufos'])} "
                    f"sites={len(gs['missionSites'])}")
            print(line + (f"  {tag}" if tag else ""))
            for b in own:
                print(f"    {b['name']}: sol={b['soldiers']} "
                      f"craft={[c['type'] for c in b['crafts']]} "
                      f"research={[r['name'] for r in b['research']]}")
            if gs["missionSites"]:
                print(f"    SITES {gc.name}: {gs['missionSites']}")
        return {gc.name: self.geo(gc) for gc in self.both}

    def set_speed_both(self, idx):
        for gc in self.both:
            if top_state(gc) == GEO:
                gc.cmd({"cmd": "geo_set_speed", "idx": idx})

    def backup_both(self, label):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        out = []
        for gc in self.both:
            fname = f"{gc.name}_{label}.sav"
            gc.ok({"cmd": "save_game", "file": fname})
            time.sleep(0.3)
            src = find_newest_sav(gc.user_dir)
            dst = os.path.join(BACKUP_DIR, fname)
            shutil.copy(src, dst)
            out.append(dst)
        print(f"[backup] {label}: {[os.path.basename(o) for o in out]}")
        return out

    @staticmethod
    def auto_dismissable(st):
        return ("GeoscapeEventState" in st or "ArticleState" in st
                or "MonthlyReportState" in st
                or "MissionDetectedState" in st)  # build-phase: skip all sites

    @staticmethod
    def is_wait_state(st):
        # coop sync "WAIT" dialogs (month save-progress sync, map download, etc.)
        # animate and auto-close once both sides finish; poll, don't decide.
        return "CoopState" in st

    def handle_popups(self, events):
        """Drain known info popups (events + Ufopaedia articles) on both sides.
        Returns (unhandled, waiting): unhandled = popups needing a decision;
        waiting = coop WAIT dialogs to poll through."""
        unhandled, waiting = [], []
        for gc in self.both:
            for _ in range(20):  # drain chained popups (event -> article -> ...)
                st = top_state(gc)
                if st == GEO:
                    break
                if self.auto_dismissable(st):
                    if "MissionDetectedState" in st:
                        # log the site + classification before skipping it
                        for s in self.geo(gc)["missionSites"]:
                            cls = classify_site(s)
                            msg = (f"MISSION {s['type']}/{s['race']} id={s['id']} "
                                   f"-> {cls} (skipped, build phase)")
                            if (gc.name, s["id"]) not in self._logged_sites:
                                self._logged_sites.add((gc.name, s["id"]))
                                self.missions.append((gc.name, s, cls))
                                print(f"  [{gc.name}] {msg}")
                    gc.cmd({"cmd": "dismiss_popup"})
                    events.append((gc.name, st))
                    time.sleep(0.15)
                elif self.is_wait_state(st):
                    waiting.append((gc.name, st))
                    break
                else:
                    unhandled.append((gc.name, st))
                    break
        return unhandled, waiting

    def advance(self, speed_idx=4, real_budget=180, poll=1.0, day_backups=True,
                stop_on_target=False, until_month=None):
        """Advance time in lockstep at speed_idx, auto-dismissing known info
        popups, until an unhandled popup/mission appears or real_budget elapses.
        Backs up on each in-game day roll. Returns why it stopped + event log."""
        self.set_speed_both(speed_idx)
        start = time.time()
        last_day = self.geo(self.host)["time"]["day"]
        events = []
        wait_since = None
        while time.time() - start < real_budget:
            unhandled, waiting = self.handle_popups(events)
            if unhandled:
                return {"stop": "decision", "popups": unhandled,
                        "events": events,
                        "date": fmt_date(self.geo(self.host)["time"])
                        if top_state(self.host) == GEO else "?"}
            if waiting:
                # coop WAIT dialog(s) up — poll for auto-close, bail if stuck
                if wait_since is None:
                    wait_since = time.time()
                    print(f"  waiting on coop sync: {waiting}")
                elif time.time() - wait_since > 45:
                    return {"stop": "stuck_wait", "waiting": waiting,
                            "events": events}
                time.sleep(0.5)
                continue
            wait_since = None
            if any(top_state(gc) != GEO for gc in self.both):
                continue  # a fresh popup appeared; re-loop to handle it
            self.set_speed_both(speed_idx)  # keep clock running after dismissals
            gs = self.geo(self.host)
            if stop_on_target:
                for gc in self.both:
                    g = self.geo(gc)
                    engage = [s for s in g["missionSites"] if classify_site(s) == "ENGAGE"]
                    if engage or g["ufos"]:
                        return {"stop": "target", "side": gc.name,
                                "missionSites": g["missionSites"], "ufos": g["ufos"],
                                "events": events, "date": fmt_date(g["time"])}
            if until_month is not None and gs["monthsPassed"] >= until_month:
                return {"stop": "month", "monthsPassed": gs["monthsPassed"],
                        "events": events, "date": fmt_date(gs["time"])}
            day = gs["time"]["day"]
            if day != last_day:
                print(f"  -- day roll -> {fmt_date(gs['time'])} "
                      f"funds={gs['funds']} sites={len(gs['missionSites'])}")
                self.cross_validate()
                if day_backups:
                    self.backup_both(f"d{gs['time']['month']:02d}{day:02d}")
                last_day = day
            time.sleep(poll)
        return {"stop": "timeout", "events": events,
                "date": fmt_date(self.geo(self.host)["time"])}

    def shutdown(self):
        self.host.shutdown(); self.client.shutdown()


def bringup():
    s = Session()
    try:
        s.start()
        s.snapshot("start")
        s.backup_both("month0_start")
        print("BRINGUP OK")
    finally:
        s.shutdown()


def advance():
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    s = Session()
    try:
        s.start()
        s.snapshot("start")
        s.backup_both("month0_start")
        print(f"advancing (budget {budget}s)...")
        r = s.advance(speed_idx=SPEED["1hr"], real_budget=budget)
        print("STOPPED:", r)
        s.snapshot("after-advance")
    finally:
        s.shutdown()


def states(gc):
    return gc.cmd({"cmd": "get_state"})["states"]


def dispatch():
    """Fly host+client crafts to the first ENGAGE (cult) site, confirm landing,
    and observe the coop battle init. Heavy logging to map the handshake."""
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    s = Session()
    try:
        s.start()
        s.snapshot("start")
        s.backup_both("predispatch")
        # advance (skipping nothing engage-worthy) until a cult site appears
        print("waiting for an ENGAGE cult site...")
        target = None
        while target is None:
            r = s.advance(speed_idx=SPEED["1hr"], real_budget=budget,
                          stop_on_target=True, day_backups=True)
            if r["stop"] != "target":
                print("no target before budget:", {k: r[k] for k in r if k != "events"})
                return
            for site in r["missionSites"]:
                if classify_site(site) == "ENGAGE":
                    target = site
                    break
            if target is None:
                # only AVOID/UNKNOWN sites up — skip them and keep going
                for gc in s.both:
                    while top_state(gc) != GEO:
                        gc.cmd({"cmd": "dismiss_popup"}); time.sleep(0.1)
        sid = target["id"]
        print(f"ENGAGE target: {target}")
        # dismiss the detection popups, then dispatch both crafts
        for gc in s.both:
            while top_state(gc) != GEO:
                gc.cmd({"cmd": "dismiss_popup"}); time.sleep(0.1)
        for gc in s.both:
            d = gc.cmd({"cmd": "craft_dispatch", "site_id": sid, "soldiers": 2})
            print(f"  [{gc.name}] dispatch -> {d}")
        # fly: advance until ConfirmLandingState appears
        print("flying to site...")
        s.set_speed_both(SPEED["1hr"])
        deadline = time.time() + budget
        while time.time() < deadline:
            if any("ConfirmLandingState" in st for gc in s.both for st in states(gc)):
                break
            # keep clock moving; auto-dismiss stray info popups (not the site)
            for gc in s.both:
                st = top_state(gc)
                if st != GEO and Session.auto_dismissable(st) and "MissionDetected" not in st:
                    gc.cmd({"cmd": "dismiss_popup"})
            s.set_speed_both(SPEED["1hr"])
            time.sleep(1.0)
        print("STATES after flight:")
        for gc in s.both:
            print(f"  [{gc.name}] {states(gc)}")
        # confirm landing on host (coop init)
        print("confirming landing (host)...")
        h = s.host.cmd({"cmd": "confirm_landing"})
        print("  host confirm:", h)
        # observe coop handshake for a while
        for i in range(40):
            time.sleep(1.0)
            hs, cs = states(s.host), states(s.client)
            hb = s.host.cmd({"cmd": "battle_state"})
            cb = s.client.cmd({"cmd": "battle_state"})
            print(f"  t+{i}s host_top={hs[-1]} client_top={cs[-1]} "
                  f"hostBattle={hb.get('inBattle')} clientBattle={cb.get('inBattle')}")
            if hb.get("inBattle") and cb.get("inBattle"):
                print("  BOTH IN BATTLE")
                break
            for gc in (s.client, s.host):
                st = top_state(gc)
                if "ConfirmLandingState" in st:
                    print(f"  [{gc.name}] has ConfirmLanding, confirming")
                    gc.cmd({"cmd": "confirm_landing"})
        # close briefings on both to enter the tactical map
        for _ in range(20):
            done = True
            for gc in s.both:
                st = top_state(gc)
                if "BriefingState" in st:
                    gc.cmd({"cmd": "close_briefing"}); done = False
                elif "ArticleState" in st or "GeoscapeEventState" in st:
                    gc.cmd({"cmd": "dismiss_popup"}); done = False
            if done:
                break
            time.sleep(0.5)
        time.sleep(1.0)
        dump_battle(s)
        s.backup_both("battle_start")
    finally:
        s.shutdown()


def dump_battle(s):
    hb = s.host.cmd({"cmd": "battle_state"})
    cb = s.client.cmd({"cmd": "battle_state"})
    print(f"\n=== BATTLE STATE ===  host_top={top_state(s.host)} client_top={top_state(s.client)}")
    for name, b in (("host", hb), ("client", cb)):
        if not b.get("inBattle"):
            print(f"  [{name}] not in battle"); continue
        fac = {0: "PLAYER", 1: "HOSTILE", 2: "NEUTRAL", -1: "NONE"}
        print(f"  [{name}] turn={b['turn']} side={b['side']} mission={b['missionType']} "
              f"selected={b['selectedId']} units={len(b['units'])}")
        for u in b["units"]:
            print(f"     id={u['id']} {fac.get(u['faction'])} '{u['name']}' "
                  f"hp={u['health']} tu={u['tu']} pos=({u['x']},{u['y']},{u['z']}) "
                  f"wpn={u.get('weapon','?')} {'PC' if u['isPlayerSoldier'] else ''}")
    # cross-validate: same unit ids + factions on both machines
    hu = {u["id"]: u["faction"] for u in hb.get("units", [])}
    cu = {u["id"]: u["faction"] for u in cb.get("units", [])}
    if hu != cu:
        s.bugs.append(("battle_unit_mismatch", {"host": hu, "client": cu}))
        print(f"  !! BUG battle unit mismatch host={hu} client={cu}")
    else:
        print(f"  OK: both machines agree on {len(hu)} units + factions")


def reach_battle(s, budget=900, until_month=None):
    """Advance to the next ENGAGE cult site, dispatch both crafts, confirm the
    coop landing, handle briefing+inventory. Returns "battle" once both are in
    the tactical map, "month" if the month boundary (until_month) is reached
    first, or "fail" on timeout."""
    target = None
    while target is None:
        r = s.advance(speed_idx=SPEED["1hr"], real_budget=budget,
                      stop_on_target=True, day_backups=True, until_month=until_month)
        if r["stop"] == "month":
            return "month"
        if r["stop"] != "target":
            print("no ENGAGE target before budget"); return "fail"
        for site in r["missionSites"]:
            if classify_site(site) == "ENGAGE":
                target = site; break
        if target is None:
            for gc in s.both:
                while top_state(gc) != GEO:
                    gc.cmd({"cmd": "dismiss_popup"}); time.sleep(0.1)
    sid = target["id"]
    print(f"ENGAGE target: {target}")
    for gc in s.both:
        while top_state(gc) != GEO:
            gc.cmd({"cmd": "dismiss_popup"}); time.sleep(0.1)
    for gc in s.both:
        print(f"  [{gc.name}] dispatch -> "
              f"{gc.cmd({'cmd': 'craft_dispatch', 'site_id': sid, 'soldiers': 2})}")
    print("flying to site...")
    s.set_speed_both(SPEED["1hr"])
    deadline = time.time() + budget
    while time.time() < deadline:
        if any("ConfirmLandingState" in st for gc in s.both for st in states(gc)):
            break
        # dismiss ANY stray popup during flight (incl. new MissionDetected site
        # alerts we skip) so nothing blocks arrival; keep the clock running.
        for gc in s.both:
            st = top_state(gc)
            if st != GEO and Session.auto_dismissable(st):
                gc.cmd({"cmd": "dismiss_popup"})
        s.set_speed_both(SPEED["1hr"])
        time.sleep(1.0)
    # arrival: confirm landing ONCE per side, only while ConfirmLandingState is
    # the TOP state. Confirming twice re-fires btnYesClick -> double coop-init ->
    # std::terminate race. Track who we've confirmed.
    confirmed = set()
    for i in range(60):
        for gc in s.both:
            if gc.name not in confirmed and "ConfirmLandingState" in top_state(gc):
                print(f"  [{gc.name}] confirm_landing -> {gc.cmd({'cmd': 'confirm_landing'})}")
                confirmed.add(gc.name)
        time.sleep(1.0)
        hb = s.host.cmd({"cmd": "battle_state"}); cb = s.client.cmd({"cmd": "battle_state"})
        if hb.get("inBattle") and cb.get("inBattle"):
            break
        # clear stray popups but NEVER touch ConfirmLanding / Coop / Briefing
        for gc in s.both:
            st = top_state(gc)
            if st != GEO and Session.auto_dismissable(st):
                gc.cmd({"cmd": "dismiss_popup"})
    # close briefings, then handle the coop pre-battle inventory (soldiers spawn
    # unarmed -> auto-equip from the ground pile), then start the tactical turn.
    for _ in range(40):
        done = True
        for gc in s.both:
            st = top_state(gc)
            if "BriefingState" in st:
                gc.cmd({"cmd": "close_briefing"}); done = False
            elif "ArticleState" in st or "GeoscapeEventState" in st:
                gc.cmd({"cmd": "dismiss_popup"}); done = False
            elif "InventoryState" in st:
                gc.cmd({"cmd": "battle_inventory", "action": "autoequip_all"})
                r = gc.cmd({"cmd": "battle_inventory", "action": "ok"})
                print(f"  [{gc.name}] equipped + closed inventory -> {r}")
                done = False
        if done:
            break
        time.sleep(0.5)
    time.sleep(1.5)
    dump_battle(s)
    s.backup_both("battle_start")
    return "battle"


def dist(a, b):
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"]) + abs(a["z"] - b["z"]) * 4


def cross_validate_battle(s, hb, cb):
    """Both machines must agree on the STABLE per-unit outcome: faction + alive/
    dead (isOut). `status` (STANDING/AIMING/WALKING) and mid-combat health are
    transient animation/apply-order state that legitimately differ for a moment
    during the sync window, so they are NOT compared. On an isOut divergence,
    re-sample after a short delay and only flag if it PERSISTS (a real desync)."""
    def key(b):
        return {u["id"]: (u["faction"], u["isOut"]) for u in b.get("units", [])}
    hk, ck = key(hb), key(cb)
    if hk == ck:
        return True
    time.sleep(1.5)  # let the combat-event sync packets land on the peer
    hk = key(s.host.cmd({"cmd": "battle_state"}))
    ck = key(s.client.cmd({"cmd": "battle_state"}))
    if hk == ck:
        return True
    diff = {i: (hk.get(i), ck.get(i)) for i in set(hk) | set(ck) if hk.get(i) != ck.get(i)}
    s.bugs.append(("battle_state_mismatch", diff))
    print(f"  !! BUG battle_state mismatch (persistent): {diff}")
    return False


BATTLE_POPUPS = ("NextTurnState", "ArticleState", "AbortMissionState",
                 "GeoscapeEventState", "DebriefingState")


def clear_battle_popups(s):
    for gc in s.both:
        for _ in range(8):
            st = top_state(gc)
            if any(p in st for p in BATTLE_POPUPS):
                gc.cmd({"cmd": "dismiss_popup"}); time.sleep(0.2)
            else:
                break


def extract(s):
    """Abort the mission to extract. Soldiers hold in the craft/exit zone, so a
    living unit is recovered. Confirms the AbortMission + Debriefing popups on
    both machines until both are back on the geoscape."""
    print("  extracting (abort)...")
    for gc in s.both:
        if gc.cmd({"cmd": "battle_state"}).get("coopTurn") == 2:
            gc.cmd({"cmd": "battle_action", "action": "abort"})
    for _ in range(30):
        clear_battle_popups(s)
        hb = s.host.cmd({"cmd": "battle_state"}); cb = s.client.cmd({"cmd": "battle_state"})
        if not hb.get("inBattle") and not cb.get("inBattle"):
            print("  extracted (both back on geoscape)"); return True
        # if a side is stuck in battle with an active turn, re-issue abort
        for gc in s.both:
            if gc.cmd({"cmd": "battle_state"}).get("coopTurn") == 2:
                gc.cmd({"cmd": "battle_action", "action": "abort"})
        time.sleep(1)
    print("  WARN extract incomplete"); return False


def validate_kill(s, enemy_id):
    """After combat, if an enemy is down, its dead-state + killer attribution must
    match on BOTH machines (the goal's kill cross-validation)."""
    h = {u["id"]: u for u in s.host.cmd({"cmd": "battle_state"}).get("units", [])}
    c = {u["id"]: u for u in s.client.cmd({"cmd": "battle_state"}).get("units", [])}
    hu, cu = h.get(enemy_id), c.get(enemy_id)
    if not hu or not cu:
        return
    if hu["isOut"] != cu["isOut"]:
        s.bugs.append(("kill_isout_mismatch", {"id": enemy_id, "host": hu["isOut"], "client": cu["isOut"]}))
        print(f"  !! BUG kill isOut mismatch id={enemy_id} host={hu['isOut']} client={cu['isOut']}")
    elif hu["isOut"]:
        if hu.get("murdererId") != cu.get("murdererId") or hu.get("killedBy") != cu.get("killedBy"):
            s.bugs.append(("kill_attrib_mismatch",
                           {"id": enemy_id, "host": (hu.get("murdererId"), hu.get("killedBy")),
                            "client": (cu.get("murdererId"), cu.get("killedBy"))}))
            print(f"  !! BUG kill attribution mismatch id={enemy_id}")
        else:
            print(f"  OK kill of {enemy_id} mirrored (murderer={hu.get('murdererId')}, killedBy={hu.get('killedBy')})")


def fight(s, max_engage_turns=2, advance_to_contact=False):
    """Automated coop tactical loop tuned for the <=2-loss budget: hold in the
    craft/exit zone, fire ranged soldiers at spotted hostiles (to validate combat
    outcomes replicate), then EXTRACT (abort) after a couple of turns or the
    moment a soldier goes down. Cross-validates state + kills every step."""
    guard = 0
    engaged_turns = 0
    fired = False
    for _ in range(240):
        clear_battle_popups(s)
        hb = s.host.cmd({"cmd": "battle_state"}); cb = s.client.cmd({"cmd": "battle_state"})
        if not hb.get("inBattle") or not cb.get("inBattle"):
            print("  battle ended"); break
        cross_validate_battle(s, hb, cb)
        allu = {u["id"]: u for u in hb["units"]}; allu.update({u["id"]: u for u in cb["units"]})
        downed = [u for u in allu.values() if u["isPlayerSoldier"] and u["isOut"]]
        if downed:
            print(f"  !! {len(downed)} soldier(s) DOWN -> extract now")
            extract(s); break
        actor, ab = (None, None)
        if hb.get("coopTurn") == 2: actor, ab = s.host, hb
        elif cb.get("coopTurn") == 2: actor, ab = s.client, cb
        if actor is None:
            guard += 1
            if guard % 15 == 1:
                print(f"  waiting turn: hostCoop={hb.get('coopTurn')} clientCoop={cb.get('coopTurn')}")
            if guard > 120:
                print("  no actionable turn 120t -> extract"); extract(s); break
            time.sleep(1); continue
        guard = 0
        if engaged_turns >= max_engage_turns:
            print(f"  engaged {engaged_turns} turns (fired={fired}) -> extract"); extract(s); break
        units = [u for u in ab["units"] if u["faction"] == 0 and not u["isOut"] and u["isPlayerSoldier"]]
        spotted = set(ab.get("spotted", []))
        enemies = [u for u in ab["units"] if u["faction"] == 1 and not u["isOut"] and u["id"] in spotted]
        all_foes = [u for u in ab["units"] if u["faction"] == 1 and not u["isOut"]]
        print(f"  [{actor.name}] turn={ab['turn']} myUnits={len(units)} spotted={len(enemies)}")
        # advance-to-contact: if nothing spotted but we want a kill, step the
        # ranged soldier toward the nearest known enemy to open a line of sight.
        if advance_to_contact and not enemies and all_foes:
            for mu in units:
                if mu["tu"] <= 0 or "KNIFE" in (mu["weapon"] or ""):
                    continue
                foe = min(all_foes, key=lambda e: dist(mu, e))
                step = 6
                tx = mu["x"] + max(-step, min(step, foe["x"] - mu["x"]))
                ty = mu["y"] + max(-step, min(step, foe["y"] - mu["y"]))
                r = actor.cmd({"cmd": "battle_action", "action": "move",
                               "unit": mu["id"], "x": tx, "y": ty, "z": mu["z"]})
                print(f"    {mu['name']} advance -> ({tx},{ty}) {r}")
                time.sleep(2.5)
            # re-read spotting after moving
            ab = actor.cmd({"cmd": "battle_state"})
            spotted = set(ab.get("spotted", []))
            enemies = [u for u in ab["units"] if u["faction"] == 1 and not u["isOut"] and u["id"] in spotted]
        for mu in [u for u in ab["units"] if u["faction"] == 0 and not u["isOut"] and u["isPlayerSoldier"]]:
            if mu["tu"] <= 0 or not mu["weapon"] or not enemies:
                continue
            # focus-fire: finish the weakest spotted enemy (secure kills), then nearest
            tgt = min(enemies, key=lambda e: (e["health"], dist(mu, e)))
            r = actor.cmd({"cmd": "battle_action", "action": "shoot",
                           "unit": mu["id"], "target": tgt["id"], "mode": "snap"})
            if r.get("ok") and r.get("tuCost", 0) > 0:
                fired = True
                print(f"    {mu['name']} shoot {tgt['id']} tuCost={r['tuCost']}")
                time.sleep(1.5)
                cross_validate_battle(s, s.host.cmd({"cmd": "battle_state"}), s.client.cmd({"cmd": "battle_state"}))
                validate_kill(s, tgt["id"])
                enemies = [u for u in s.host.cmd({"cmd": "battle_state"})["units"]
                           if u["faction"] == 1 and not u["isOut"] and u["id"] in set(spotted)]
        engaged_turns += 1
        actor.cmd({"cmd": "battle_action", "action": "end_turn"})
        time.sleep(2)
    hb = s.host.cmd({"cmd": "battle_state"}); cb = s.client.cmd({"cmd": "battle_state"})
    print(f"  fight done. fired={fired} hostBattle={hb.get('inBattle')} clientBattle={cb.get('inBattle')}")
    return not hb.get("inBattle")


def battle():
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    s = Session()
    try:
        s.start()
        s.backup_both("predispatch")
        if reach_battle(s, budget):
            fight(s)
        print(f"\n=== BUGS ({len(s.bugs)}) ===")
        for k, d in s.bugs:
            print(f"  {k}: {d}")
    finally:
        s.shutdown()


def roster_count(s):
    """Living soldiers per side's own base (host, client). Detects losses across
    a mission (dispatched soldiers return to base after extraction)."""
    out = {}
    for gc in s.both:
        r = gc.cmd({"cmd": "get_soldiers"})
        n = 0
        for b in r.get("bases", []):
            if not b.get("coopBaseFlag") and not b.get("coopIcon"):
                n += len(b.get("soldiers", []))
        out[gc.name] = n
    return out


def campaign():
    """FULL month-1 coop playthrough: advance the geoscape, fight every cult
    apprehension (dispatch -> coop battle -> validated engagement -> extract),
    skip dangerous monster hunts, back up after each battle + each day, and stop
    when Feb 1 is reached or >2 soldiers are lost. Reports missions, losses, bugs."""
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
    s = Session()
    missions = 0
    try:
        s.start()
        s.snapshot("campaign-start")
        s.backup_both("campaign_start")
        base0 = roster_count(s)
        print(f"starting roster: {base0}")
        while True:
            status = reach_battle(s, budget=budget, until_month=2)
            if status == "month":
                print("\n*** MONTH 1 COMPLETE (reached Feb 1) ***"); break
            if status == "fail":
                print("\n*** stopped: no target / timeout ***"); break
            # in a battle
            missions += 1
            pre = roster_count(s)
            print(f"--- MISSION {missions} (roster before: {pre}) ---")
            fight(s)
            clear_battle_popups(s)
            time.sleep(2)
            post = roster_count(s)
            lost = {k: pre[k] - post.get(k, 0) for k in pre}
            total_lost = base0["host"] + base0["client"] - post["host"] - post["client"]
            print(f"--- MISSION {missions} done. roster after: {post}  lost this mission: {lost}  "
                  f"cumulative lost: {total_lost} ---")
            s.backup_both(f"after_mission{missions}")
            if total_lost > 2:
                print(f"\n*** FAILED: lost {total_lost} soldiers (>2). Stopping. ***"); break
        # final report
        post = roster_count(s)
        gs = s.geo(s.host)
        print("\n================ CAMPAIGN REPORT ================")
        print(f" date={fmt_date(gs['time'])} monthsPassed={gs['monthsPassed']}")
        print(f" missions fought: {missions}")
        print(f" starting roster: {base0}   final roster: {post}")
        print(f" total soldiers lost: {base0['host']+base0['client'] - post['host']-post['client']}")
        print(f" bugs found: {len(s.bugs)}")
        for k, d in s.bugs:
            print(f"   - {k}: {d}")
        s.backup_both("campaign_end")
    finally:
        s.shutdown()


def killtest():
    """Focused cross-validation: reach a cult battle and fight harder (up to ~6
    turns, focus-firing) to secure at least one confirmed enemy KILL, verifying
    the death + kill attribution (murdererId/killedBy) mirror on BOTH machines,
    then extract. Directly exercises the goal's 'client killed enemy -> dead on
    host too' requirement in both directions."""
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    s = Session()
    try:
        s.start()
        s.backup_both("killtest_start")
        if reach_battle(s, budget) == "battle":
            fight(s, max_engage_turns=10, advance_to_contact=True)
        print(f"\n=== KILLTEST BUGS ({len(s.bugs)}) ===")
        for k, d in s.bugs:
            print(f"  {k}: {d}")
    finally:
        s.shutdown()


def play_month():
    """Build-phase run: advance through end of January (monthsPassed>=2),
    skipping all mission sites, enumerating them, and cross-validating coop
    world state each day. Reports the month-1 mission census + any bugs."""
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 1800
    s = Session()
    try:
        s.start()
        s.snapshot("start")
        s.backup_both("month0_start")
        print(f"playing to Feb 1 (budget {budget}s)...")
        r = s.advance(speed_idx=SPEED["1hr"], real_budget=budget, until_month=2)
        print("STOPPED:", {k: v for k, v in r.items() if k != "events"})
        s.snapshot("end")
        print("\n=== MONTH-1 MISSION CENSUS ===")
        for side, site, cls in s.missions:
            print(f"  [{side}] {site['type']} / {site['race']} id={site['id']} -> {cls}")
        print(f"\n=== BUGS ({len(s.bugs)}) ===")
        for kind, detail in s.bugs:
            print(f"  {kind}: {detail}")
        if r.get("stop") == "month":
            print("\nMONTH 1 COMPLETE (geoscape).")
    finally:
        s.shutdown()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "bringup"
    {"bringup": bringup, "advance": advance, "play_month": play_month,
     "dispatch": dispatch, "battle": battle, "campaign": campaign,
     "killtest": killtest}[action]()
