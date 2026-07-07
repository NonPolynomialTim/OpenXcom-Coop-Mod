# Geoscape time scaling & co-op time sync

How the geoscape clock speed works and how it's synchronized in co-op. All code
is in [`src/Geoscape/GeoscapeState.cpp`](../../src/Geoscape/GeoscapeState.cpp)
(buttons, `timeAdvance()`, the periodic send) and
[`src/CoopMod/connectionTCP.cpp`](../../src/CoopMod/connectionTCP.cpp) (the
`"time"` packet receiver). Relevant fields live in `connectionTCP` /
`getCoopMod()`.

## The six speed buttons

`_btn5Secs, _btn1Min, _btn5Mins, _btn30Mins, _btn1Hour, _btn1Day`
(GeoscapeState.cpp ~174). They form a **radio group** via
`setGroup(&_timeSpeed)` (~328+), so the member `TextButton* _timeSpeed` always
points at the currently selected button. Labels: `STR_5_SECONDS`,
`STR_1_MINUTE`, `STR_5_MINUTES`, `STR_30_MINUTES`, `STR_1_HOUR`, `STR_1_DAY`.
Keyboard shortcuts (`Options::keyGeoSpeed1..6`) route to
`GeoscapeState::btnTimerClick` (~5959), which just synthesizes a left-click on
the sender button — so selecting a speed is pure radio-group behavior that
updates `_timeSpeed`. **No packet is sent on click**; the speed is broadcast by
the periodic send in `think()` (below).

## timeAdvance() — how a speed becomes simulated time

`GeoscapeState::timeAdvance()` (~1775). The clock always advances in **5-second
steps**; `timeSpan` = how many 5s steps to run this tick:

| Speed     | `_timeSpeed` button | `timeSpan` (5s steps) |
|-----------|---------------------|-----------------------|
| 5 Secs    | `_btn5Secs`         | 1                     |
| 1 Min     | `_btn1Min`          | 12                    |
| 5 Mins    | `_btn5Mins`         | 60                    |
| 30 Mins   | `_btn30Mins`        | 360                   |
| 1 Hour    | `_btn1Hour`         | 720                   |
| 1 Day     | `_btn1Day`          | 17280                 |

The advance loop (~1896) runs `getTime()->advance()` `timeSpan` times, firing the
`time5Seconds/10Minutes/30Minutes/1Hour/1Day/1Month` triggers via fallthrough.
(Single-player also honors `Options::oxceGeoSlowdownFactor` on 5 Secs.)

## Co-op time synchronization

Gated everywhere by `connectionTCP::_enable_time_sync` (a static flag) plus
`getCoopMod()->getCoopStatic()` (true only when actually in a co-op session).

### 1. Send — `GeoscapeState::think()` (~1639)
Runs each `think()` while in a co-op geoscape with time sync on. Builds a
`"time"` packet:
- **Host only** (`getServerOwner()==true`): the authoritative date/time
  (`weekday/day/month/year/hour/minute/second`) plus `monthsPassed`/`daysPassed`.
- **Both sides**: `root["time_speed"]` = a string id of the locally selected
  speed (`"_btn5Secs"` … `"_btn1Day"`), mapped from `_timeSpeed`, **and**
  `root["geo_focus"]` = `-1` normally or `0` while a dogfight window is open (for
  the ally indicator — see below).
Then `sendTCPPacketData(...)`.

### 2. Receive — `connectionTCP.cpp` `stateString == "time"` (~2327)
- **Client only** (`getServerOwner()==false`): copies the host's date/time into
  `connectionTCP::_weekday … _second` and `monthsPassed/daysPassed`. These are
  applied to the client's `SavedGame` elsewhere (e.g. GeoscapeState.cpp ~837,
  guarded by `getCoopStatic() && !getServerOwner() && _enable_time_sync`), so the
  host's clock is authoritative.
- **Both sides**: stores the *teammate's* state in `getCoopMod()`:
  - `other_time_speed_coop = obj["time_speed"]` — **transient** (used by the
    match rule below, cleared each `timeAdvance`).
  - `peerTimeSpeedId = obj["time_speed"]` — **persistent** copy for the indicator.
  - `peerFocusScreen = obj.get("geo_focus", -1)` — where the peer is (`-1` = on
    the geoscape, `0` = open dogfight window). A sub-screen stops `"time"`
    packets, so the last dedicated `geo_focus` packet sticks instead.
  - `lastPeerTimePacketMs = SDL_GetTicks()` — heartbeat (both sides); drives the
    freeze gate and the busy/yellow marker.

### 3. The "must match, else fall back to 5 Secs" rule — `timeAdvance()` (~1819)
When `getCoopStatic() && _enable_time_sync`:
- `timeSpan` is forced to **1** (5 Secs) by default.
- It compares the teammate's reported speed (`other_time_speed_coop`) against the
  **local** `_timeSpeed`. Only if they're the **same** speed does `timeSpan` get
  set to that speed's value — i.e. both players advance fast only when both have
  picked the same option; otherwise everyone runs at 5 Secs. This is the
  intended behavior the QOL feature builds on.
- **Host-only mode** (`connectionTCP::_enable_host_only_time_speed`, from
  `Options::EnableHostOnlyTimeSpeed`): when on, a client (`getHost()==false`) has
  its `_timeSpeed` *forced* to the host's reported speed and advances at it — the
  host dictates the clock.
- **Important:** at the end of the block (~1892) `other_time_speed_coop` is reset
  to `""`. So it is a **transient, per-tick** value: set on packet receipt,
  consumed once by the next `timeAdvance()`, then cleared. Between packets it is
  empty.

## Key flags & fields

- `connectionTCP::_enable_time_sync` (static) — master switch for time sync.
- `connectionTCP::_enable_host_only_time_speed` (static, from
  `Options::EnableHostOnlyTimeSpeed`) — host dictates speed when true.
- `getCoopMod()->other_time_speed_coop` (string) — teammate's last-reported
  speed id; **transient (cleared each `timeAdvance`)**; used by the match rule.
- `getCoopMod()->peerTimeSpeedId` (string) — teammate's last-reported speed id,
  **persistent**; used by the indicator.
- `getCoopMod()->peerFocusScreen` (atomic int) — teammate's geoscape location:
  `-1` = on the geoscape, `0..5` = a toolbar sub-screen (Intercept…Funding).
- `getCoopMod()->lastPeerTimePacketMs` (atomic Uint32) — last peer `"time"`
  heartbeat (both sides); drives the freeze gate and the busy/yellow marker.
- `getCoopMod()->getServerOwner()` / `getHost()` — host vs client.
- `getCoopMod()->getCoopStatic()` — true during an active co-op session.
- `connectionTCP::_weekday/_day/_month/_year/_hour/_minute/_second`,
  `monthsPassed`, `daysPassed` — host's clock mirrored to the client.

## Speed id strings (duplicated mapping — watch out)

The `_timeSpeed` button ⇄ `"_btnXxx"` string mapping is hand-written in **three**
places: the send serialization (~1666-1689) and the two arms of the
`timeAdvance` coop branch (~1824-1890). Any change to the speed set must update
all three. A small `buttonToSpeedId()` / `speedIdToButton()` helper (and a
shared label lookup) would remove the duplication — worth doing if we touch this.

## Ally activity indicator (implemented)

Shows, on each player's geoscape, what the *other* player is doing — a `+` in the
top-right corner of a button. Per-button counts (`std::string(n, '+')`), so it
scales to >2 players (one `+` each).

- **Markers:** `GeoscapeState::_peerSpeedMarker[6]` (over the speed buttons) and
  `_peerScreenMarker[6]` (over the toolbar buttons
  Intercept/Bases/Graphs/Ufopaedia/Options/Funding), created in the ctor and
  refreshed every `think()` by `updatePeerSpeedIndicators()`.
- **Where it goes** (from `peerFocusScreen` + `peerTimeSpeedId` + the heartbeat):
  - peer on the geoscape (`peerFocusScreen == -1`, fresh heartbeat) → **red** `+`
    on their selected **speed** button.
  - peer in a toolbar screen / mapped popup / dogfight (`peerFocusScreen` 0..5) →
    **red** `+` on that **toolbar** button.
  - peer away with no known screen (`peerFocusScreen == -1` but **stale**
    heartbeat) → **yellow** `+` ("busy") on their speed button.
- **Reporting focus** — `sendCoopFocus(int)` sends a dedicated `geo_focus` packet
  (received → `peerFocusScreen`). Triggers:
  - the six toolbar handlers (`btnInterceptClick` … `btnFundingClick`) send 0..5
    (at the *top* of the handler, so a rejected click self-corrects on the next
    heartbeat);
  - `globeClick()` → Intercept for a UFO/craft/**your own** base, Bases only for
    the **other** player's base (`Base::_coopBase == true`);
  - event popups mapped centrally at the `_popups` dequeue via
    `coopFocusForPopup()`: UFO/mission/alien-base → Intercept; base defense +
    base/research/production/training/containment/sell/items-arriving → Bases;
    monthly report → Funding; everything else → `-1` (no packet → yellow);
  - dogfights via the `geo_focus` field on the normal `"time"` packet (`0` while a
    dogfight window is open), since dogfights keep time running.
  - returning to the geoscape resets `peerFocusScreen = -1` via the next `"time"`
    packet.
- **Colours** (constants in `updatePeerSpeedIndicators`, tunable):
  - active = `Palette::blockOffset(15) + 5` ("red"/pink), high contrast.
  - busy = `Palette::blockOffset(15) + 10` ("yellow"), **low** contrast.
  - Why low contrast for busy: the text shader maps glyph shade `s` to
    `color + (s-1)*mul`; high contrast sets `mul = 3`, pushing edge pixels to
    `color+3` (a **cyan** palette entry). Low contrast (`mul = 1`) keeps edges at
    `color+1/+2` (still yellow). Block 15 of the geoscape palette is a *mixed*
    block, not a clean ramp, so the "red"/"yellow" names are empirical — tune the
    offsets if the shades drift.

## Two-way time freeze (implemented)

Originally only the **host** leaving the geoscape froze time for both (its
authoritative clock stops → the client, pinned to it, stops). The client leaving
did **not** freeze the host. Now it's symmetric:

- The client emits a `"time"` heartbeat every `think()` on the geoscape; both
  sides record `lastPeerTimePacketMs`.
- In `timeAdvance()` the host sets `timeSpan = 0` (freeze) if it hasn't seen a
  peer heartbeat within ~1 s (`grace`). The host keeps broadcasting the frozen
  time, so the client stays pinned → both frozen.
- **Double-gating:** host-active is implicit (its `GeoscapeState` only `think()`s
  while it's the top state); client-active is the heartbeat. Time advances only
  when **both** are on the geoscape and resumes only once both return.
- **Dogfights are exempt** (they keep time running), so the heartbeat keeps
  flowing during a dogfight — the marker reports Intercept instead.
- The ~1 s grace is shared by the freeze gate and the yellow marker, so the
  marker turns yellow exactly when time stops.

## File map
- Buttons, `timeAdvance()` (incl. the freeze gate), `btnTimerClick()`, periodic
  `"time"` send (incl. `geo_focus`), the markers + `updatePeerSpeedIndicators()`,
  `sendCoopFocus()`, `coopFocusForPopup()`, `globeClick()` focus, and the popup
  dequeue hook: `src/Geoscape/GeoscapeState.{cpp,h}`.
- `"time"` / `geo_focus` packet receive + `peerTimeSpeedId` / `peerFocusScreen` /
  `lastPeerTimePacketMs` + mirrored clock fields: `src/CoopMod/connectionTCP.{cpp,h}`.
- Options: `Options::EnableHostOnlyTimeSpeed` (and the `keyGeoSpeed1..6` binds).

## Status
Implemented on branch `feature/coop-geoscape-ally-indicator` (rebased onto the
upstream base; pushed to the fork, PR pending). The earlier "must match / fall
back to 5 Secs" behavior is unchanged.
