# Coop test harness (autonomous two-client testing)

Run live end-to-end coop tests without a human: an in-game command server
drives real game instances (menus, saves, sessions, transfers) and reads game
state. Built 2026-07-04; used to validate soldier ownership transfers (see
[`coop-soldier-transfer.md`](coop-soldier-transfer.md)).

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

Add commands by extending `TestServer::execute`.

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
