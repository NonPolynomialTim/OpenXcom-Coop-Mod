# Multi-Rendezvous-Server + Offline Detection — Implementation Plan

## Goal
Config defines **multiple named rendezvous servers**. The Server Browser gets a
combobox in the **top-right** to pick which rendezvous server to use. On entry the
game **probes** each configured server; unreachable ones are shown **disabled** with
`" (offline)"` appended to their name. Defaults to the **last-selected** server
(persisted in a new JSON file). If the selected server is offline, show a graceful
warning and an empty list until the user picks an online server.

## Locked decisions
- Config format: **JSON** (`rendezvous.json`, jsoncpp — no new deps). From prior plan.
- New widget: **`DisableableComboBox : public ComboBox`** (subclass; no impact on
  existing comboboxes).
- Health check: **`LIST_ROOMS` over TCP** with a short timeout — punch-free, already
  implemented, and validates the server sign key. No new server opcode.
- Persistence: **`<masterUserFolder>/rendezvous_selection.json`** (mirrors
  `player_name.json`). **Supersedes** the earlier `Options::coopRendezvousServer`
  idea — that is dropped.
- Probe method: full `LIST_ROOMS`, short timeout (default **2500 ms**), run in
  parallel background threads.
- Combobox position: top-right, e.g. `ComboBox(this, 90, 16, 222, 6)` (title is
  center-aligned; nudge if visual overlap).

---

## Part A — Config: multiple servers (from prior plan, JSON)

`rendezvous.json`:
```json
{
  "servers": [
    { "name": "Official", "host": "77.42.120.251", "tcpPort": 39000, "udpPort": 39001,
      "gameVersion": "1.8.4 [v2026-06-28]",
      "serverBoxPublicKey": "vwGJ...", "serverSignPublicKey": "Cg7Q..." },
    { "name": "Local Test", "host": "127.0.0.1", "tcpPort": 39000, "udpPort": 39001,
      "gameVersion": "1.8.4 [v2026-06-28]",
      "serverBoxPublicKey": "...", "serverSignPublicKey": "..." }
  ]
}
```

### `rendezvous_config.h/.cpp`
- `struct RendezvousServerEntry { std::string name; BuiltInRendezvousConfig cfg;
  std::string serverBoxPublicKeyB64, serverSignPublicKeyB64; };`
- New API:
  - `std::vector<std::string> getRendezvousServerNames();`
  - `size_t getRendezvousServerCount();`
  - `void setActiveRendezvousServer(size_t index);`
  - `void setActiveRendezvousServerByName(const std::string& name);`
  - `size_t getActiveRendezvousServer();`
  - `std::string getActiveRendezvousServerName();`
  - `bool getRendezvousServerConfig(size_t index, BuiltInRendezvousConfig& cfg,
     RendezvousClient::ServerKeys& keys, std::string* error);`  ← per-index accessor
     for probing an arbitrary server without changing the active one.
- **Unchanged** (return the active server → glue + MainMenu untouched):
  `getBuiltInRendezvousConfig()`, `loadBuiltInRendezvousKeys()`.
- `.cpp`: parse `servers[]` with jsoncpp under `std::call_once`; active index is
  `std::atomic<size_t>`; vector immutable after load (safe for worker-thread reads).
- Persistence handled in ServerList (see Part D), not here — but
  `setActiveRendezvousServerByName` is the entry point used to apply the restored name.

### Assets / hygiene
- `rendezvous.json.example` (replace `rendezvous.cfg.example`).
- `.gitignore`: `rendezvous.cfg` → `rendezvous.json` and `rendezvous_selection.json`.
- Regenerate real `bin/x64/Release/rendezvous.json` with the two current keys.

---

## Part B — New widget: `DisableableComboBox : public ComboBox`

### Minimal, behavior-neutral base changes to `ComboBox.h`
- `private:` → `protected:` (subclass reaches `_list`, `_sel`, `_lang`, `_color`).
- Make `virtual`: `setSelected(size_t)`.
- (No `.cpp` behavior change; existing comboboxes unaffected — none subclass.)

### `src/Interface/DisableableComboBox.h/.cpp` (new)
Members:
- `std::vector<bool> _enabled;`
- `Uint8 _disabledColor = <grey>;`

Methods:
- `void setOptions(const std::vector<std::string>& options,
                   const std::vector<bool>& enabled, bool translate=false);`
  → calls `ComboBox::setOptions(options, translate)`, stores `_enabled`, then greys
  disabled rows via `_list->setRowColor(i, _disabledColor)`.
- `void setDisabledColor(Uint8 c);`
- `bool isEnabled(size_t idx) const;`
- `void forceSelect(size_t idx);` → `ComboBox::setSelected(idx)` (base, bypasses the
  disabled guard — used for programmatic/init selection, incl. an offline server).
- `void setSelected(size_t sel) override;` → if `sel` in range && `!_enabled[sel]`,
  **ignore** (return without changing state); else `ComboBox::setSelected(sel)`.

### Why this enforces disable with no TextList edit
TextList commits a click via `_comboBox->setSelected(row); _comboBox->toggle(false,true)`
([TextList.cpp:1241](../../src/Interface/TextList.cpp)). `setSelected` is now virtual →
dispatches to the override → disabled row rejected → `_sel` unchanged → `toggle` fires
`onChange`, but `getSelected()` is unchanged → handler no-ops. Programmatic `forceSelect`
calls the base directly, so an offline server can still be shown as the current
selection. **Limitation:** TextList still hover-highlights a disabled row (cosmetic;
it's greyed and unselectable).

### Build registration
Add `DisableableComboBox.cpp/.h` to `src/CMakeLists.txt` (near line 263),
`src/OpenXcom.2010.vcxproj` (ClCompile + ClInclude), and `*.vcxproj.filters`.

---

## Part C — Health probing (offline detection)

### New glue helper (`connection_rendezvous_glue.*`)
```cpp
// Punch-free: LIST_ROOMS over TCP with a short timeout. Returns true if the server
// answered a signed ROOM_LIST. Optionally returns the rooms (used for the active
// server so we don't query twice).
bool probeRendezvousServer(size_t serverIndex, uint32_t timeoutMs,
                           std::vector<RendezvousClient::RoomInfo>* outRooms /*nullable*/,
                           std::string* error);
```
- Implementation: fetch that index's config+keys via `getRendezvousServerConfig(...)`,
  build a `RendezvousClient::ListConfig` with `timeoutMs = timeoutMs`, call
  `RendezvousClient::listRooms(...)`. Success ⇒ online. Does **not** touch the active
  server or global flow state.
- Async wrapper `probeAllRendezvousServersAsync(timeoutMs, callback)` spawns one
  detached thread per server (parallel), collects `{index, online, rooms?}`.

### Timeout
Default 2500 ms (tunable constant). Offline servers appear after ~one timeout.

---

## Part D — Server Browser wiring (`ServerList.h/.cpp`)

### Members
- `DisableableComboBox* _cbxServer;`
- `Text* _txtOfflineWarning;` (hidden unless the selected server is offline)
- Probe state (module-scope, next to existing `_pendingRooms`):
  `std::mutex`, `std::vector<ServerProbeResult> _pendingProbes`, `bool _hasPendingProbes`.

### Construction / `init()`
1. Load configured server names.
2. `_cbxServer = new DisableableComboBox(this, 90, 16, 222, 6);`
   `add(_cbxServer, "button", "saveMenus")` **LAST** (dropdown z-order).
3. Restore last selection: read `rendezvous_selection.json` → name;
   `setActiveRendezvousServerByName(name)`. **Graceful fallback**: if the file is
   missing/corrupt, or the saved name matches no entry in `rendezvous.json`, silently
   select **index 0** (first configured server). `setActiveRendezvousServerByName`
   itself defaults to index 0 on any unknown name.
4. Build combobox in **waiting state**: label each option `name + " (Wait...)"`, all
   **disabled** (probe status unknown, so no switching until results arrive):
   `_cbxServer->setOptions(waitLabels, allFalse, false);`
   `_cbxServer->forceSelect(getActiveRendezvousServer());`  // bypasses disabled guard
   `_cbxServer->onChange(&ServerList::cbxServerChange);`
   `_cbxServer->setVisible(names.size() > 1);`
5. **Defer** room query until the window is entered (matches your requirement):
   kick `probeAllRendezvousServersAsync(2500, cb)` from `init()` (after widgets built).
   Do **not** query rooms synchronously here.

### `think()` — apply probe results on the UI thread (mirror the rooms pattern)
- When `_hasPendingProbes`: rebuild combobox options — replace the `(Wait...)` labels.
  For each server, label = `name` (online) or `name + " (offline)"`; `enabled[i] =
  online`. Call `_cbxServer->setOptions(labels, enabledMask)` then
  `_cbxServer->forceSelect(activeIndex)` (keep current selection even if offline).
  (Partial results are applied as each probe returns; a server still in-flight keeps
  its `(Wait...)` label/disabled until its own result arrives.)
- If the **active** server is online: populate the browser from its probe rooms
  (reuse existing `_pendingRooms`/list-fill path) and hide `_txtOfflineWarning`.
- If the **active** server is offline: `_lstServers->clearList()`, show
  `_txtOfflineWarning` = e.g. `"Selected rendezvous server is offline. Pick another
  from the list."`.

### `cbxServerChange(Action*)`
- `size_t sel = _cbxServer->getSelected();`
- Guard: if `!_cbxServer->isEnabled(sel)` → return (defensive; click path already
  rejects, but onChange can fire).
- `setActiveRendezvousServer(sel);`
- Persist: write `{ "name": <selected name> }` to `rendezvous_selection.json`.
- Clear list + hide warning, then trigger a fresh `updateServerList()` (queries the
  newly-selected server; also re-confirms it online for the browser).

### Persistence file helpers
- `std::string selectionFilePath()` = `Options::getMasterUserFolder() +
  "rendezvous_selection.json"`.
- Load/save with jsoncpp exactly like the existing `player_name.json` block
  ([ServerList.cpp:244-269](../../src/CoopMod/ServerList.cpp)).

### Existing refresh path
- The 30s auto-refresh and `_btnRefresh` continue to work; they query the **active**
  server (unchanged, via `getBuiltInRendezvousConfig()`), and can also re-run the
  probe to refresh "(offline)" markers.

---

## Concurrency
- Configured-server vector is immutable post-load; active index is atomic.
- Probe threads only read config and call `listRooms` (self-contained TCP op) — no
  shared UI/game state. Results marshalled to UI via the pending-flag+mutex+`think()`
  pattern already used for rooms.
- Changing selection mid-probe is harmless: a stale in-flight probe just updates its
  own row; `cbxServerChange` re-queries the new active server.

## Test / verify
1. Build x64 Release (register new widget files first).
2. `rendezvous.json` w/ 2 servers, one bogus host → enter browser:
   - Combobox top-right lists both; bogus one shows `... (offline)` and is greyed/
     unselectable after ~2.5 s.
   - Online server selected → rooms populate.
3. Set last-selected to the bogus server → enter browser → warning text shown, list
   empty, combobox shows `Name (offline)` as current; switch to online server →
   list populates, warning clears.
4. Restart → previously-selected name restored from `rendezvous_selection.json`.
5. Single server → combobox hidden; behaves as today.
6. Missing `rendezvous.json` → not-configured path, no crash.

## Out of scope (later)
- Editing servers from the UI.
- Suppressing TextList hover-highlight on disabled rows (needs a TextList change).
- A dedicated lightweight server `PING` opcode (LIST_ROOMS suffices today).
