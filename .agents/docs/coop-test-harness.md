# Coop test harness (autonomous two-client testing)

Run live end-to-end coop tests without a human: an in-game command server
drives real game instances (menus, saves, sessions, transfers, geoscape play,
and full battlescape combat) and reads game state. Built 2026-07-04; used to
validate soldier ownership transfers (see
[`coop-soldier-transfer.md`](coop-soldier-transfer.md)). Extended 2026-07-05
into a full autonomous-play driver (geoscape time driving, craft dispatch,
coop battle entry, tactical combat, extraction) with per-tick host/client
cross-validation — proven on a complete month-long 2-player campaign.

## Architecture

- **In-game server**: `src/CoopMod/TestServer.{h,cpp}` — active only when the
  `OXC_TEST_PORT` env var is set. Listens on that port (localhost only),
  newline-delimited JSON commands, executed on the main thread via a per-frame
  pump in `Game::run()` (so all state access is race-free).
- **Python driver**: `tools/coop_test/harness.py` — `GameClient` (spawn +
  socket + `wait_for` polling), `make_user_dir()` (isolated `-user` folders
  seeded from the real `options.cfg`, with intro cutscene, audio, mouse capture
  and fullscreen all disabled; small windows tucked into corners so the user
  can keep working while tests run).
- **Tests** (run `python tools/coop_test/<file>` — spawns two real game windows):
  - `test_transfer_legacy.py` — Jerzy transfer on the legacy `.sav`.
  - `test_transfer_fresh.py` — transfer on a brand-new campaign, both sides.
  - `test_bug_fixes.py` — visit-window loss, dialog flicker, owner resolution,
    notice display, stacked-notice colors. Exposes `bootstrap_fresh_session()`
    and `own_base()` reused by other tests.
  - `test_transfer_rollback.py` — the host-save-is-authority repro (transfer A,
    save, transfer B, abandon, reload) in BOTH directions + stacked notices.
  - `boot_check.py` — single-instance smoke test for install validation.
  - `play_harness.py` — autonomous 2-player playthrough driver (geoscape +
    battlescape + cross-validation); see its own section below.
- Debug scratchpad: `tools/coop_test/debug_lobby.py` — step-through with full
  state/flag dumps. `[coop-transfer]`/`[testserver]` lines land in each
  instance's `openxcom.log` (`%TEMP%\oxc-coop-test\<host|client>-user\`).

## Commands (TestServer::execute)

Session/introspection: `ping`, `quit`, `get_state` (state-stack typeids),
`get_coop` (all session flags + `insideCoopBase`, `saveID`), `get_soldiers`
(bases + rosters + coop fields), `get_mirror_soldiers {coopBaseId}` (what the
peer-base visit view would list), `has_coop_file {key}`, `set_option
{name,value}` (HostSaveProgress only so far).

Flow drivers (invoke real State handlers, made public where needed —
`Profile::buttonOK`, `NewGameState::btnOkClick`, `BuildNewBaseState::placeAt`,
`BaseNameState::setNameAndConfirm`, `SoldiersState::btnOkClick`,
`BasescapeState::btnGeoscapeClick`, `TransferNoticeState::btnOkClick` — rather
than faking SDL input): `load_save {file}`, `save_game {file}`, `open_new_game`,
`newgame_ok`, `place_first_base {lon,lat,name}`, `profile_ok`, `host_tcp
{server,port,player}`, `join_tcp {ip,port,player}`, `lobby_ready`,
`client_reload_progress` (reconnect: ask host for our world).

Transfer/UI: `transfer {name,owner}`, `transfer_targets {name}` (what the
dialog would offer — validates owner resolution without UI), `rename_soldier
{name,newName}`, `open_soldiers {base}` / `soldiers_ok`, `visit_coop_base
{base}` / `leave_base`, `open_transfer_dialog {name}` / `cancel_dialog`,
`show_notice {message}` / `get_notices` (returns each notice's interface
category) / `dismiss_notice`, `get_palettes` (top states' first colors, for the
flicker check).

Geoscape play (added 2026-07-05):

- `geo_state` — read-only snapshot: `time{year,month,day,hour,minute}`,
  `funds`, `monthsPassed`, per-base `{name, crafts[{type,status}],
  research[{name,spent,cost}], soldiers}`, `ufos[{id,type,detected,status}]`,
  `missionSites[{id,type,race,city}]`. No coop side effects.
- `geo_set_speed {idx}` — select a time-speed button, 0=5s … 5=1day, via the
  new public `GeoscapeState::setTimeSpeedIndex(int)` (synthesizes a real
  radio-group click so the coop speed broadcast behaves as a user click).
  Coop rule: time advances fast only when BOTH players pick the SAME speed
  (see [`geoscape-timescaling.md`](geoscape-timescaling.md)) — the driver sets
  it on host+client together and lets the real timers run.
- `dismiss_popup` — confirm/close the TOP popup; reports the typeid and errors
  on unknown types so new popups surface instead of hanging. Handled:
  `GeoscapeEventState` (btnOkClick), any `ArticleState` subclass (popState —
  btnOkClick is protected), `MonthlyReportState` (btnOkClick),
  `MissionDetectedState` (btnCancelClick = skip; site stays on the globe),
  `NextTurnState` (popState), `AbortMissionState` (btnOkClick = confirm abort),
  `DebriefingState` (btnOkClick).
- `craft_dispatch {site_id, soldiers}` — assign up to N unassigned soldiers to
  the own base's first craft (`Soldier::setCraft`) and
  `Craft::setDestination(site)`; the geoscape flies it as time advances.
- `confirm_landing` — fire `ConfirmLandingState::btnYesClick` (host path pushes
  the CoopState(88) coop-battle init). **Never fire twice** — see gotchas.

Battlescape (added 2026-07-05):

- `close_briefing` — `BriefingState::btnOkClick`.
- `battle_inventory {action}` — pre-battle coop inventory: `autoequip_all`
  (cycles units through `InventoryState::onAutoequip` + `btnNextClick`; uses
  the engine's `BattlescapeGenerator::autoEquip` from the ground pile) and
  `ok` (btnOkClick → starts the tactical turn). Soldiers spawn UNARMED — craft
  items land on the ground tile (`BattlescapeGenerator` `_craftInventoryTile`).
- `battle_state` — `inBattle`, `turn`, `side`, `missionType`,
  `coopTurn` (= `BattlescapeGame::isYourTurn`; **2 = my active turn**, see
  memory battlescape-coop-turn-states), `selectedId`, `spotted[]` (union of
  hostile ids visible to my units via `getVisibleUnits()` — drive targeting
  from this, not omniscience), and `units[]` with `id, faction (0=PLAYER,
  1=HOSTILE, 2=NEUTRAL), status, isOut, health, tu, stun, name, weapon
  (getMainHandWeapon), isPlayerSoldier, murdererId, killedBy, x, y, z`.
- `battle_action {action, ...}` — all main-thread, race-free:
  - `select {unit}` — `setSelectedUnit`.
  - `move {unit,x,y,z}` — BA_WALK: sets `_currentAction`, runs
    `Pathfinding::calculate`, pushes `UnitWalkBState`; errors `no path to target`.
  - `shoot {unit,target,mode}` — mode snap|aimed|auto; sets actor/weapon/type/
    targeting on `getCurrentAction()`, `updateTU()` (returns `tuCost`/`tuHave`),
    pushes `UnitTurnBState` + `ProjectileFlyBState`. Replicates to the peer
    because coop syncs at combat-event level (`unit_fire`/`hit_tile`/
    `hasHitUnit`/`hit_unit` packets fire from inside the BStates).
  - `end_turn` — `requestEndTurn(false)`.
  - `abort` — `BattlescapeState::btnAbortClick` (opens AbortMissionState; the
    driver confirms it via `dismiss_popup`). This is the ONLY abort that ends
    the mission — `setAborted(true)+requestEndTurn` alone never calls
    `finishBattle` and loops forever.

Add commands by extending `TestServer::execute`.

## Autonomous play driver — `tools/coop_test/play_harness.py`

Layers on harness.py; actions: `python tools/coop_test/play_harness.py <action> [budget_s]`

- `bringup` — 2-player XCF coop session to live geoscape + snapshot + backup.
- `advance [s]` — lockstep time advance, auto-dismissing known popups.
- `play_month [s]` — geoscape-only month traversal, skipping all sites,
  enumerating the mission census + daily coop cross-validation.
- `dispatch` / `battle [s]` — advance to the first ENGAGE cult site, dispatch
  both crafts, coop landing, equip, fight, extract.
- `killtest [s]` — aggressive fight (advance-to-contact, up to 10 turns,
  focus-fire) to force kills and stress kill/death replication.
- `campaign [s]` — the full month-1 goal loop: fight every ENGAGE site, skip
  AVOID monsters, backup after each battle + each day, stop at Feb 1 or >2
  losses; prints a final report (missions, roster deltas, bugs).

Key helpers: `activate_xcf(dir)` (flips x-com-files active:true in the isolated
dir only — the real options.cfg stays vanilla), `Session.cross_validate`
(host/client mission-site set equality with a 2.5s re-sample to filter
detection-timing skew), `cross_validate_battle` (per-unit faction+isOut
equality, 1.5s re-sample; `status`/mid-combat HP are transient animation state
— do NOT compare), `validate_kill` (murdererId/killedBy mirror check),
`classify_site` (`AVOID_RACE`/`ENGAGE_HINTS` constants — which mission-site
races/types are safe to engage vs skip), `roster_count` (loss tracking),
`backup_both(label)` → `tools/coop_test/playthrough_backups/`,
`keep_awake()` (SetThreadExecutionState — a screen lock kills the SDL context
of background instances → std::terminate).

## The session bootstrap dance (what the tests replicate)

1. host: `load_save` (or `open_new_game` → `newgame_ok` → `place_first_base`) →
   `host_tcp` (pushes LobbyMenu; campaign = countries non-empty)
2. client: `join_tcp` → Profile splash on both → `profile_ok` both
3. client: `newgame_ok` (difficulty) → `place_first_base` (its own linked
   campaign; the name-confirm sends `close_load_progress`) → LobbyMenu
4. both `lobby_ready` (ready toggle) → wait `sessionLocked` → both
   `lobby_ready` again (start) → wait `lobbyClosed && hasSave` on client
5. session live: `coopStatic && coopCampaign && sessionLocked && lobbyClosed`

## Gotchas learned the hard way

- `SDLNet_Init()` must be called in the server thread (nothing else has).
- `SDLNet_ResolveHost(NULL, port)` = listen; a hostname = outbound connect.
- **Fresh saves number soldiers from 1 and can roll fully identical rosters**
  (same RNG seed → same names). Never dedup transfers by name/roster; in tests,
  `rename_soldier` a subject to a unique name before asserting by name/count.
- 30s lobby countdown auto-locks when both ready; a second lobby click starts.
- `client_reload_progress` is how a test simulates the "host reloads a save,
  client reconnects" flow — the client re-fetches its world from the host, so
  after a host `load_save` the client reflects that save's rosters.
- Verifying a *fresh* client-world push landed on the host: `has_coop_file`
  only proves the key exists (a stale blob passes too) — pair it with a short
  sleep, or assert on the resulting roster after a reload.

## Gotchas — geoscape/battle driving (2026-07-05)

- **confirm_landing exactly once per side, only while ConfirmLandingState is the
  TOP state.** After the first confirm, CoopState(88) sits on top but
  ConfirmLandingState is still in the stack — a stack-wide check re-fires
  btnYesClick, coop battle init runs twice, `std::terminate`.
- **CoopState is a WAIT dialog, not a decision** (month save-progress sync, map
  download; int-keyed). Poll for auto-close; never dismiss.
- **Sites despawn in hours-days and IDs recycle** — dispatch promptly; don't
  key logs by id alone.
- Coop battle model: ONE shared squad from the initiating side's craft
  (Civilian Car = 2 soldiers), identical on both machines; enemy ids start at
  1000000. Whichever machine has `coopTurn==2` acts; turns alternate via the
  endTurn packet.
- **Extraction strategy** (kept the month-1 campaign at 0 losses): hold the
  craft/exit zone, fire at spotted hostiles, abort within ~2 turns — living
  units in the exit zone are recovered. Holding longer gets rookies charged
  (battle6 lost one to melee by turn 7); XCF "cult apprehension" can spawn
  hp-150 Shambler Raiders that 2 rookies cannot kill.
- **Screen lock kills background instances** (`std::terminate`, empty
  crashlog) — call `keep_awake()`; shutdown-time terminate pairs in
  `bin/x64/Release/crashlogs/` are benign (unjoined net thread on exit).
- **Windows cp1252 stdout**: XCF agent names contain non-Latin1 chars (`ś`) —
  `sys.stdout.reconfigure(encoding="utf-8")` or redirected runs crash.
- Run long drivers with `python -u` + `tail -f` the log; MSBuild link fails
  with LNK1104 while any OpenXcom.exe instance is alive.
- **Replication gaps found by killtest**: (FIXED, verified) kill attribution
  desynced to the client — `hit_unit` fires mid-hit, before
  `checkForCasualties` assigns `killedBy`, so the client credited murderer 0 /
  faction HOSTILE. Fix: `hit_unit` now carries `murdererId`+`killedBy`
  (`TileEngine.cpp` ~3325) and an authoritative post-death `kill_attrib`
  packet re-applies both from `checkForCasualties`
  (`BattlescapeGame.cpp` ~1679 → receiver `connectionTCP.cpp` ~4340).
  (OPEN) under rapid multi-hit fire the `unit_death` packet can be dropped —
  it is only consumed while `_coop_task_completed` or `_coopInitDeath`
  (a live client `ProjectileFlyBState`) holds, so a death can sit in the hold
  queue forever, leaving a 0-HP-but-alive unit on the client until debrief.
  Doesn't reproduce in cautious play; host save stays authoritative.
