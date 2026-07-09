# Co-op soldier ownership transfer

How permanent soldier ownership transfer works in co-op, and — critically —
the **host-save-is-the-single-authority** persistence model that makes it
rollback-safe. Built 2026-07-03/04. Code: `src/CoopMod/TransferSoldierMenu.*`,
`src/CoopMod/TransferNoticeState.*`, transfer logic in `connectionTCP.cpp`,
persistence in `Savegame/SavedGame.cpp`.

## User-facing behaviour

The **Give Unit to Teammate** keybind (`Options::giveUnit`, default `SDLK_KP2`)
opens a "Transfer [SOLDIER NAME] to another player?" dialog
(`TransferSoldierMenu`) with a button per **other** player plus Cancel. Available
from four contexts:
- Bases > Soldiers list (hovered row) — `SoldiersState::lstSoldiersGiveUnitPress`
- Craft > Crew list (hovered row) — `CraftSoldiersState::lstSoldiersGiveUnitPress`
- Soldier stat screen — `SoldierInfoState::btnGiveUnitPress`
- Battlescape (selected unit) — the campaign-soldier branch in `Game.cpp`'s
  giveUnit handler (skirmish / non-geoscape units keep the legacy temporary
  "loan" that only flips `BattleUnit::_coop` for the current battle).

Transfers are **permanent, survive saves, and are re-tradeable** any number of
times. The receiving player gets a notification popup (`TransferNoticeState`):
"[PLAYER] transferred ownership of [SOLDIER] to you at base [BASE]".

## The guest-soldier model (where a transferred soldier lives)

This is the key concept, and it predates this feature — transfers reuse it:

- A `Soldier` object lives in exactly one player's `SavedGame`.
- `Soldier::getCoopBase() == -1` → owned by **this machine's** player.
- `Soldier::getCoopBase() == <coop base id>` → **peer-owned, stationed at that
  base** (a "guest"). Guests are hidden from the owner-machine's own soldier
  lists (`SoldiersState::initList` filter), shown in the peer's mirror-base
  view, and have their craft cleared.
- Ownership ids follow the co-op convention: **0 = host, 1 = client**
  (`Soldier::getOwnerPlayerId()`, also mirrored into `getCoop()`). `999` =
  never explicitly assigned = belongs to whichever machine's save holds it.

**Transferring = physically moving the object between the two saves**, not just
flag-flipping (a flags-only version was tried and failed — the giver's list
hides its own peer-owned soldiers *and* the peer-base visit rebuilds rosters
from deep copies, so a flag-only soldier was invisible on both sides). The
giver serializes the soldier (`Soldier::save` YAML) + its `station_base_id`,
removes it from every roster **and** the `base_oldsoldiers`/`base_oldsoldiers2`
snapshots (open list screens restore from those and would resurrect it), and
sends the `transferSoldier` packet. The receiver re-instantiates it: if the
station base is one of the receiver's own real bases the soldier "comes home"
(`coopBase = -1`); otherwise it becomes a guest (`coopBase = station id`).
Fresh id assigned on collision (the two saves number soldiers independently and
can even roll identical names — never dedup by name/roster).

### Name getters are machine-relative

`connectionTCP::getHostName()` = the LOCAL player's own name (every machine
writes its own name box there); `getCurrentClientName()` = the PEER's name.
The transfer dialog resolves target names relative to `getHost()`
(`localPlayerId = getHost() ? 0 : 1`) — a role-fixed assumption renders the
client's own name as a transfer target. See [[coop-name-getters-machine-relative]].

## Peer-base-visit timing (the swapped-world hazard)

When a player views the peer's base, their `SavedGame` is swapped out for a
throwaway copy of the peer's world (`CoopState` state 55, populated from
`playerInsideCoopBase`). A transfer arriving during that window must NOT be
applied into the throwaway world (it would be discarded on exit — soldier lost
on both sides). Incoming physical transfers are therefore **deferred while
`playerInsideCoopBase`** and replayed by `processPendingSoldierTransfers()`
once the real world is back (guarded on an own non-mirror base actually
existing — the flag clears a few frames before `LoadGameState` restores the
save). During the visit a **display-only copy** is dropped into the visited
base so the soldier appears immediately; the durable copy lands via the replay,
deduped so there is never a double.

## Persistence: the host save is the single source of truth

**The most important architectural fact.** With "Only Host Can Save Each
Player's Progress" (`Options::HostSaveProgress`, the normal coop-campaign mode):

- Clients **cannot save locally**. Both players' worlds live host-side.
- The client's world is a blob keyed `client_<saveID>_<hostName>.data` /
  `host_<saveID>_<clientName>.data`, held in the static `coopFilesHost` /
  `coopFilesClient` maps and a sidecar `.data` file in the host's user folder.
- **The host's `.sav` embeds the latest client-world blob** (base64) —
  `SavedGame::save` writes `coopClientSaveKey` + `coopClientSaveBlob`; skipped
  when writing a `.data` sidecar to avoid recursive embedding. So one `.sav`
  captures BOTH players' rosters atomically.
- `SavedGame::load` extracts the blob, overwrites the in-RAM cache AND rewrites
  the sidecar file, so the existing reconnect flow (`request_load_progress`
  streams that file to the client) serves exactly the world the loaded save
  knew about. **Loading a host save rolls BOTH rosters back together.**
- The client **silently pushes its world to the host after every transfer**
  (`pushProgressToHostSilently()` — the existing progress-push handshake minus
  the blocking dialog), keeping the embedded blob fresh at save time.
- `LoadGameState` calls `resetTransferSessionState()` on load, clearing pending
  queues / dedup ids / away-ids — stale RAM state must never outlive the save
  that is now the authority. (This was the root of the "abandon + reload =
  duplicated soldier" bug: "abandon game" keeps the process alive, so the
  static coop blobs survived a reload of an older save.)

### Why not receipts / reconciliation?

An earlier version logged transfer "receipts" inside each save and reconciled
divergence on reconnect. It was **deleted** — fundamentally wrong layer:
clients never persist receipts (they don't save), and the split between the
`.sav` and the `.data` sidecar meant the two could roll back independently.
Embedding the blob makes the `.sav` self-contained; there is nothing left to
reconcile. Do not reintroduce receipts.

### Mod compatibility (X-Com Files etc.)

Verified safe. OpenXcom mods are rulesets + Y-scripts with **no file I/O**;
they only influence which content names the engine serializes. The two new
save-root keys (`coopClientSaveKey`, `coopClientSaveBlob`) are ignored by any
reader that doesn't `tryRead` them (vanilla OXCE included). The `.data` sidecar
still exists and is kept in lock-step with the embedded blob. Transferred
soldiers carry all modded content (types, commendations, transformations,
diary) through `Soldier::save` with `getScriptGlobal()`. Only caveat: an
embedded blob is a full second save base64'd (+33%), so a coop XCF host save
can be ~2.3× a solo one — compress with miniz (already in the build) if it ever
matters. Solo saves are byte-identical to before.

## UI palette / flicker

Dialogs must adopt their parent screen's palette or the hardware palette swap
flashes the screen. `TransferSoldierMenu` uses the `sackSoldier` interface
(base palette; battle-game param switches to battlescape palette in battle).
`TransferNoticeState` adopts the palette of the state it opens over, and picks
its **color element category by context**: `geoManufactureComplete` (standard
geoscape popup colors) over the geoscape, else `sackSoldier` — over the
geoscape the sackSoldier colors are illegible dark blue. When notices stack,
the context scan **skips other `TransferNoticeState`s** so every notice themes
off the real screen underneath, not the notice above it.

## Post-battle cleanup exemption

`GeoscapeState`'s post-battle cleanup deletes `coop != 0` soldiers from host
bases (the merged copies of the peer's own soldiers). Transferred guests
(`ownerPlayerId != 999 && coopBase != -1`) are **exempted** — the giver's save
holds the only copy and deleting it would destroy the soldier.

## Validation

All of the above is covered by the autonomous harness — see
[`coop-test-harness.md`](coop-test-harness.md). Key suites:
`test_transfer_rollback.py` runs the exact save-authority repro in both
directions; `test_bug_fixes.py` covers the visit-window loss, flicker, owner
resolution, notice, and stacked-notice color. Related: [[p2p-rewrite-plan]]
(the transport under all this is slated for replacement).
