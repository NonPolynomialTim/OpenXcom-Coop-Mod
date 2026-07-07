# OpenXcom Co-op Mod — Multiplayer Bug Report

Date: 2026-07-02
Scope: all multiplayer code — `src/CoopMod/` (TCP, UDP/rendezvous/LAN, lobby/UI) plus co-op hooks in `src/Battlescape/`, `src/Geoscape/`, `src/Savegame/`, `src/Basescape/`, `src/Menu/`, `src/Engine/`.
Focus: gameplay bugs for honest players (crashes, desyncs, hangs, lost state). Security explicitly out of scope.
Findings verified against both send and receive sides where applicable. Ordered by severity.

---

## CRITICAL

### C1. Client-side `detonate()` stub returns `true` — every explosion tile counts as a destroyed mission objective
**Location:** `src/Battlescape/TileEngine.cpp:3899-3903`
The client-side early-out `if (getCoopStatic() && !getHost()) return true;` sits in a function whose return value means "an objective tile was destroyed". The caller (`TileEngine::explode()`, ~3847) does `if (detonate(...)) _save->addDestroyedObjective();` per blast tile, and `addDestroyedObjective()` triggers mission auto-completion. In any destroy-objective mission (alien base consoles, Cydonia brain), one grenade on the client's machine increments the counter by dozens and instantly "completes" the mission on the client while the host keeps playing. Stub should return `false`.

### C2. `|| 1==1` in `checkForProximityGrenadesCoop` — client detonates every nearby grenade and deletes all other ground items
**Location:** `src/Battlescape/BattlescapeGame.cpp:4281-4314` (trigger sends at 4387/4409/4451)
`if (item->fuseProximityEvent() || 1==1)` is always true for every item in inventories of all tiles within 1 tile of the unit: every grenade-type item explodes on the client (even unprimed) and every non-grenade item (weapons, ammo, corpses, flares) hits the `else` branch and is destroyed via `_save->removeItem(item)` — on the client only. One proximity trigger near dropped equipment = guaranteed hard desync. Host's vanilla path (4443) is correct.

### C3. `AlienMission` constructor poisons `_interrupted=true` on the non-host — alien activity permanently stops (residual root cause behind commit a26ef38fe)
**Location:** `src/Savegame/AlienMission.cpp:51-58` (ctor), `:80` (`tryRead("interrupted", ...)`), `:150-151`
The ctor sets `_interrupted = true` whenever `getCoopStatic() && !getHost()`. `SavedGame::load`/`loadCoopSaveFromMemory` construct missions then `tryRead` leaves the poisoned value (vanilla saves omit the key when false). `GeoscapeState::init` (GeoscapeState.cpp:923-1117) on the non-host loads then **re-saves** the geoscape save after every mission, persisting `interrupted: true`. `AlienMission::think` early-returns for interrupted missions → UFO waves, terror, retaliation, supply all silently stop forever. Commit a26ef38fe fixed the spawn guards but not this constructor path.

### C4. Switching bases inside Basescape then exiting wipes the entire base list — instant game over
**Location:** `src/Basescape/BasescapeState.cpp:82-100` (ctor stashes full list in `_base->old_bases`, filters live vector), `:621-646` (`btnGeoscapeClick` restores `*bases = _base->old_bases`), `:898-906`/`:933-958` (`_base` reassigned via minimap/hotkeys)
If the player switches to another base (whose `old_bases` is empty) and clicks Geoscape, an EMPTY vector is assigned: every base is leaked and removed from the SavedGame. `time5Seconds` (GeoscapeState.cpp:1976-1988) sees no bases → `END_LOSE`. Unrecoverable if autosaved. Same wipe via `TransferBaseState` ctor (TransferBaseState.cpp:49-54) after a base switch.

### C5. Medikit sync packet never identifies the medikit item — peer spends charges on stale/random weapon or null-derefs
**Location:** `src/Battlescape/MedikitState.cpp:259-270` (also 308-318, 350-360); receiver `src/Battlescape/BattlescapeState.cpp:2109-2156` (`coopHealing`)
The "medkit" packet has no weapon/item id. Receiver sets `actor/type/Time` on the current action but never `->weapon`, then `TileEngine::medikitUse` does `action->weapon->getRules()` and `spendHealingItemUse(...)` (TileEngine.cpp:5292, 5306) on whatever weapon pointer was left over — null → crash; other item → its charges are spent. Medikit quantities permanently desync after any heal.

### C6. `melee_attack` handler dereferences null unit when actor id not found
**Location:** `src/Battlescape/BattlescapeGame.cpp:656-672`
`found_unit` is computed but never tested — unlike `movePlayerTarget` (229), `turnPlayerTarget` (363), `psi_attack` (547), which all return when not found. A melee packet for a unit that no longer exists locally (killed/converted, id drift) hits `unit->setPosition(*startpos)` with `unit = 0` → instant crash on the receiving machine.

### C7. Phantom `new BattleItem(..., getCurrentItemId())` in replay paths advances the item-id counter on one peer only — cascading permanent item-id desync
**Location:** `src/Battlescape/BattlescapeState.cpp:2296-2312` (`coopActionClick`), `src/Battlescape/BattlescapeGame.cpp:567-595` (psi) and `689-717` (melee), `BattlescapeState.cpp:4552-4568` (`shootPlayerTarget`), also `BattlescapeGame.cpp:3051-3067` (`psiButtonAction`)
When the actor's weapon isn't matched, replay fabricates a fresh `BattleItem`; `BattleItem::BattleItem` does `(*id)++` (BattleItem.cpp:49) — receiver's counter increments, sender's doesn't. All inventory/action sync is keyed on `item_id`, so every later item (thrown grenades, spawned items) gets different ids on host vs client and every subsequent id lookup can mis-pair. Aggravators: the hand-weapon lookup before the `new` is a dead store (always overwritten); the lookup at BattlescapeState.cpp:2285 compares `getRules()->getName()` while the sender transmits `getType()` (ActionMenuState.cpp:569), so modded items with name≠type take the phantom path every shot; phantom items are never registered with the save (leak, phantom ammo state); `psiButtonAction`'s `getItem("STR_PSI_AMP")` can return null under total-conversion mods → null rule → crash.

### C8. `SPSCQueue` used with multiple producers and multiple consumers — silently lost/corrupted packets
**Location:** `src/CoopMod/connectionTCP.h:89-129` (queue); producers of `g_txQ`: main thread (`sendTCPPacketData` 7773, `sendTCPPacketStaticData` 335), network thread PING/PONG (341-352, 1244, 1258-1269, 307), loopData file-transfer thread (447, 464, 555…); dual consumers: `clearNetworkSessionQueues` (280-300, main) vs socket thread; `g_rxQ`: `updateCoopTask` (main) vs `clearAllReceivedPackets` (network thread, 1290-1319); also invoked from the UDP monitor thread via `connection_rendezvous_glue.cpp:597`
Two racing pushes can read the same head index, overwrite each other's slot, and advance head once — a packet silently vanishes or a torn `std::string` is produced. Most likely during map/save transfer overlapping 1 Hz pings. Symptoms: a lost `map_result_data` chunk hangs the transfer on "Please wait" forever; a lost action packet drops a move/shot → desync or stuck turn; rare crash at disconnect.

---

## HIGH

### H1. `intToUfostatus` can't decode DESTROYED — duplicated `status == 4` check
**Location:** `src/CoopMod/connectionTCP.cpp:7177-7190`
`ufostatusToInt` encodes DESTROYED=3, IGNORE_ME=4, but decoder tests `status == 4` twice — 3 falls through to IGNORE_ME, 4 decodes as DESTROYED. Every `ufo_damage` (3855) / `target_positions` (4488) packet with a destroyed UFO applies IGNORE_ME on the peer: the UFO your friend destroyed never registers as destroyed — stale geoscape state, wrong scoring/cleanup.

### H2. Grenade fuse timer truncated to `bool` before applying remotely
**Location:** `src/CoopMod/connectionTCP.cpp:2949` (`active_grenade`), `:2976` (`action_click`)
`bool fusetimer = obj["fusetimer"].asInt();` collapses multi-turn fuses to 0/1; `coopActiveGranade`/`coopActionClick` take int and call `setFuseTimer`. A grenade primed for 3 turns detonates on different turns on the two machines — units alive on one screen, dead on the other.

### H3. `hit_tile` pairing by queue position — dropped hits on timing skew, dangling pointers across missions
**Location:** `src/CoopMod/connectionTCP.cpp:3337-3345` (handler); producer `src/Battlescape/TileEngine.cpp:3466-3472`; `_battleActions` never cleared (`disconnectTCP` 7804-7908 doesn't reset it; no `.clear()` anywhere)
Client queues its local `BattleActionAttack` (raw unit/item pointers) and waits for the host's `hit_tile` to pop `front()`. (a) Packet arrives while queue empty (autoshot/reaction interleave, slower client animation) → silently discarded, that hit never applies → permanent HP/terrain desync. (b) Entry never matched (mission ends on that shot) survives the battle; next mission's first `hit_tile` pops pointers into the deleted previous battle → use-after-free or corrupt damage, and everything after is off-by-one mis-paired. Also leaks a `new RuleDamageType()` per packet (3298).

### H4. Melee cost charged twice on the replaying client (same family as fixed turn-TU bug 76d057644)
**Location:** `src/Battlescape/MeleeAttackBState.cpp:198` + `src/Battlescape/BattlescapeGame.cpp:675-679, 723`
Sender transmits post-spend TU; receiver applies it then pushes `MeleeAttackBState`, whose `init()` runs `if (!_action.spendTU(&_action.result) && !coop_action)` — `spendTU` always executes; `!coop_action` only suppresses the early return. Remote deducts the cost a second time → wrong TU/energy on the off-turn machine, divergent reaction fire and multi-hit melee outcomes (a later `spendTU` in `think()` 349 can fail remotely while succeeding locally → HP desync).

### H5. Terrain melee attacks never synced
**Location:** `src/Battlescape/MeleeAttackBState.cpp:204-210`
The terrain-melee branch returns before the coop send block (265). Attacking a wall/door executes only on the acting client — tile damaged/destroyed locally, peer (including the host, tile authority) never sees it. Permanent map desync: one player walks through a hole the other doesn't have.

### H6. Visible-units sync broken on BOTH ends; berserk units can target themselves
**Location:** senders `src/Battlescape/UnitTurnBState.cpp:94-100`, `src/Battlescape/UnitWalkBState.cpp:157-164`; receivers `src/Battlescape/BattlescapeGame.cpp:250-270, 370-388`
Sender writes `turn["visible_units"][j]["unit_id"] = _unit->getId()` (acting unit's own id, should be `bu->getId()`). Receiver's loop variable shadows the acting unit and does `unit->addToVisibleUnits(unit)` — each unit "sees itself". Intended sync never happens; the acting unit gains a self-entry at distance 0 on the remote. `UnitPanicBState::think()` (123-135) picks the closest visible unit as berserk target → unit shoots its own tile; LOS-dependent checks (`isLOSRequired` psi/mind-probe, spotting) diverge between machines.

### H7. Remote psi replay crashes when target id not found
**Location:** `src/Battlescape/PsiAttackBState.cpp:61-90` (fed by BattlescapeGame.cpp psi_attack ~506)
In the replay branch `_target` is only assigned on lookup success; no null check before `_target->getFloatHeight()` (83). Target died/converted before the packet replays → crash. On failure the state neither pushes explosion nor pops itself → state machine wedged even without the crash. Same shape in `MeleeAttackBState::init` (67-82): failed lookup leaves `_melee_target_id` stale and the attack lands at voxel (0,0,0).

### H8. `btnEndTurnClick` runs the entire coop handoff before `allowButtons()`
**Location:** `src/Battlescape/BattlescapeState.cpp:2792-3041` (coop block 2898-3027, vanilla guard only at 3030)
End Turn pressed while a soldier walks / explosion animates: the `PlayerTurnYour` packet is sent immediately with a mid-action snapshot of every unit, and local `isYourTurn` flips to 1 — the in-flight action then finishes locally only (off-turn sends gated on `isYourTurn == 2`). Clients disagree on positions/HP for the rest of the mission. `allowButtons()` (5978-5994) has no coop check to compensate.

### H9. `playableUnitSelected()` returns true in state 1; inventory button never hidden off-turn
**Location:** `src/Battlescape/BattlescapeState.cpp:3638-3654` (guard), `:2560-2581` (`btnInventoryClick`), off-turn hide list 1372-1456 (no `_btnInventory`)
State 1 = "NOT my turn" returns true, inverting the off-turn protection for every handler relying on it. During player A's turn, player B can click Inventory: `cancelAllActions()` cancels A's replay mid-walk on B's machine, and a TU-charging `InventoryState` opens on A's soldier — B can rearrange/drop A's items during A's turn. Spectator (state 4) leaks through too (`_isActivePlayerSync` left true from the pre-spectator handshake, 1754/1778→1583).

### H10. `btnSkillsClick` has no coop guard; `_btnSkills` stays visible off-turn
**Location:** `src/Battlescape/BattlescapeState.cpp:3523-3530`; off-turn block 4097-4101 hides only `_btnPsi`
Siblings are guarded (`btnLaunchClick` 3448 `==1||3||4`, `btnPsiClick` 3465 `!=2`, `btnSpecialClick` 3482 `!=2`); Skills is not. Combined with H9, the off-turn player can open the skill menu on the remote player's soldier and fire a skill — mutation happens locally only → desync/wrong-player control.

### H11. `Inventory::moveItem` ownership guard precedence bug — asymmetric enforcement, one-sided item moves
**Location:** `src/Battlescape/Inventory.cpp:539` (gates real moves); same expression at Inventory.cpp:920, 1436 and InventoryState.cpp:375/381/587/597/761/789 (cosmetic "Owner" label)
`(hostClause) || (clientClause) && coopInventory` parses as `A || (B && C)`: host is blocked from touching client-owned units in ALL contexts (even base screens where `coopInventory == false`), client CAN move host-owned items whenever `coopInventory == false` — and the move broadcasts (685) and applies on the host. One-sided moves desync craft/base inventory.

### H12. Off-turn inventory guard misses spectator state 4
**Location:** `src/Battlescape/InventoryState.cpp:369-372`
Checks `getCurrentTurn() == 1 || == 3` only. Spectator (4) never gets `show_inactive_player_inventory`, so `BattleUnit::isSelectable` (BattleUnit.cpp:5332-5337) lets them select and manipulate units mid-battle; moves broadcast and apply on the active player's game.

### H13. Grenade-prime broadcast guard is `getCurrentTurn() != 1` — waiting (3) and spectator (4) players can prime grenades into the live game
**Location:** `src/Battlescape/PrimeGrenadeState.cpp:180`
Every non-1 state sends "active_grenade"; receiver (`coopActiveGranade`, BattlescapeState.cpp:2158) applies `setFuseTimer` unconditionally and calls `_save->setSelectedUnit(unit)` — also yanks the active player's selection mid-turn. Guard should be `== 2` (plus the pre-battle equip-phase case).

### H14. Medikit `actor_id` filled with the heal TARGET's id; healer's TU never spent on the peer
**Location:** `src/Battlescape/MedikitState.cpp:263` (also 312, 354); receiver `coopHealing`
`obj["actor_id"] = _targetUnit->getId();` — receiver attributes the action to the wounded unit: `medikitUse` awards XP/statistics to `action->actor` (TileEngine.cpp:5350, 5362-5386), i.e. to the wrong unit on the peer. `coopHealing` also never spends TU, so the healer's TU differs between the two games for the rest of the turn (reaction-fire math).

### H15. Mirror UFO deleted while a dogfight references it — use-after-free
**Location:** `src/CoopMod/connectionTCP.cpp:4796-4820` ("remove ufos" in `target_positions`); same pattern for mission sites 4822-4846
Mirror UFOs absent from the latest packet are `delete`d immediately, guarded only by `openMultipleTargetsMenu`, not by active dogfights. Client intercepts host's UFO; UFO despawns on the host → next packet omits it → client deletes the `Ufo` while `DogfightState::_ufo` still points at it → crash.

### H16. Buying items for the teammate's base delivers 0 items — money spent, goods lost
**Location:** `src/Basescape/PurchaseState.cpp:890, 910-915, 920` (sender); receiver `src/Savegame/Base.cpp:131, 181-184` (`syncTrade`)
`item_amount` initialized to 0; for `TRANSFER_ITEM` the code does `trade->getQuantity();` discarding the return (compare correct `TransferItemsState.cpp:746`). Receiver applies `setItems(rule_item, 0)` while `coopFunds` was already debited (877).

### H17. Soldiers/crafts transferred to the teammate's base are silently destroyed
**Location:** `src/Basescape/TransferItemsState.cpp:741-785` (sender); receiver `src/Savegame/Base.cpp:155-168`
Sender never writes `"soldier_rule"` for `TRANSFER_SOLDIER`; receiver reads `""` and `continue`s — soldier never created remotely, while locally `_baseTo->getTransfers()->clear()` (785) discards him (stranded/leaked). Craft transfer: craft removed from source (632), soldiers aboard erased into transfers (623), then cleared — craft, weapons, cargo, soldiers permanently lost on both sides; receiver spawns only a brand-new empty craft of the same type.

### H18. `cutscene` handler corrupts `monthsPassed` (`setMonthsPassed(daysPassed)`)
**Location:** `src/CoopMod/connectionTCP.cpp:2005-2006`
Second call should be `setDaysPassed`. Receiver's `monthsPassed` becomes the days count (e.g. 250 instead of 8) and daysPassed is never applied. Mission-script month weights, race weights, funding projections, `getCurrentScore` indexing all key off `monthsPassed` → wrong/absent mission generation, potential out-of-range access. Host receiving a client-forwarded cutscene keeps the corrupt value permanently.

### H19. Client Geoscape clock snapped to host time every frame — hourly/daily triggers double-fire or are skipped
**Location:** `src/CoopMod/connectionTCP.cpp:674-689` (per-frame overwrite in `updateCoopTask`); client still advances locally via `GeoscapeState.cpp:1819-1894`
A snap across a midnight/hour boundary means the client never executes `time1Day`/`time1Hour` for it — construction, research, healing, manufacture on the client's own bases silently skip days; a backwards snap fires the same boundary twice (double progress). While the host pauses in a menu, the client's `time5Seconds` keeps running against a frozen clock — crafts move and burn fuel in zero elapsed game time. Structural drift.

### H20. Client never runs `time1Month`; depends entirely on one `monthly_report` packet
**Location:** `src/Geoscape/GeoscapeState.cpp:3966-3974` (early return), `:1296-1303`; sender `src/Geoscape/MonthlyReportState.cpp:511-614`
If the packet is lost, host disconnected at the boundary, or host's popup deferred, the client permanently misses a month: funds/income/score lists fall one entry behind while `monthsPassed` is force-synced forward — graphs and score indexing desync for the rest of the campaign, no recovery.

### H21. PVE2 soldier-split loop assigns before checking — client gets one extra unit; with 2 soldiers the host controls nothing
**Location:** `src/Battlescape/BriefingState.cpp:638-656`
`setCoop(1)` executes before the `soldier_used <= 0` check, so the first `n/2 + 1` soldiers become client-owned. With 2 soldiers both go to the client — host can't act in gamemode 4. Check-and-decrement must precede assignment.

### H22. `if (_serverinfo->isLanDiscovery = true)` — assignment; password-protected internet joins always fail
**Location:** `src/CoopMod/PasswordCheckMenu.cpp:154`
Always takes the LAN join path with empty/zero `lanHost`/`lanPort` for internet-listed rooms → join fails (`onConnect = -3`); `joinListedViaRendezvousAsync` at 169 unreachable. Also corrupts the `ServerInfo` record.

### H23. Unguarded `std::stoi(_port->getText())` crashes the game on Start Host (UDP)
**Location:** `src/CoopMod/HostMenu.cpp:786`
Empty field → `std::invalid_argument`; 11+ digits → `std::out_of_range`; no try/catch in the click handler → `terminateHandler` → `abort()`. `valid_port()` (connectionTCP.cpp:7640-7677) used by the TCP path has the same overflow hole at 7646 (all-digits check then `stoi`); `getPortFromAddress` (1149-1173) too. Safe pattern exists: `DirectConnect::parseUdpPort` (DirectConnect.cpp:412).

### H24. `AddServerMenu` OK button bound to `onKeyboardPress` with no key — fires on ANY keystroke
**Location:** `src/CoopMod/AddServerMenu.cpp:151`
`onKeyboardPress` defaults to `SDLK_ANY` (InteractiveSurface.h:85); first character typed into the IP/port/name field immediately adds a junk entry (`"Server"/"IP-ADDRESS"/"PORT"`) to servers.json and closes the menu. Manually adding a server via keyboard effectively impossible; each attempt appends a garbage row.

### H25. Async join callbacks run on detached worker threads, capture `this`, and call `_game->pushState()`
**Location:** `src/CoopMod/DirectConnect.cpp:540-555`, `src/CoopMod/ServerList.cpp:1020-1034`; glue `connection_rendezvous_glue.cpp:915-1139`
`Game`'s state stack is main-thread-only (Game.cpp:464, 613-626). (a) `pushState(new PasswordCheckMenu(...))` from the worker mutates `_states` concurrently with the main loop → corruption/crash; constructing a `State` off-thread is also unsafe. (b) Lambda reads `_ipAddress->getText()` after the user may have backed out → use-after-free. Correct pattern exists in `ServerList::updateServerList` (448-456): mutex-guarded globals consumed in `think()`.

### H26. Rendezvous client: no EOF detection — host waits forever at 100% CPU when the master-server connection dies
**Location:** `src/CoopMod/connectionUDP/rendezvous_client.cpp:110-141` (`pollFrame`), 432-500, 757-827
`pollFrame` returns false for both "no data" and "connection closed"; all wait loops keep waiting. With `cfg.timeoutMs = 0` (host lobby path, glue 1331/1745) the loop is infinite, and `SDLNet_CheckSockets` on a closed socket reports ready immediately → busy-spin. Host sits at "waiting for player" forever with a core pegged; the room is gone from the server so nobody can join.

### H27. No timeout when NAT hole punching fails — both players hang in lobby indefinitely
**Location:** `src/CoopMod/connectionUDP/connectionUDP.cpp:1023` (watchdog gated on `_hasPeer`); `connection_udp_glue.cpp:168-201, 331-352`
The 15 s peer-loss watchdog only runs after `_hasPeer`. If punching never succeeds (symmetric NAT beyond the ±32 port-guess radius), the worker punches forever, the client's INIT_SERVER thread gives up after 30 s silently, no error is set anywhere. Join "succeeds" (`onConnect = 1`), both players sit in the lobby forever.

### H28. LAN room list only shown when the internet master server is reachable
**Location:** `src/CoopMod/connectionUDP/connection_rendezvous_glue.cpp:1188-1214`
`refreshServerListViaRendezvous` returns false at 1193 (keys missing — current state of `rendezvous_config.cpp`) or 1204 (`listRooms` failed) before `refreshLanServerList` (1213) ever runs. Two friends on the same LAN with no internet see an empty browser even though the host broadcasts on UDP 39002; only Direct Connect works.

### H29. F_PUNCH echo loop — punch packets ping-pong forever for the whole session
**Location:** `src/CoopMod/connectionUDP/connectionUDP.cpp:949-953`
`handleIncoming` replies to every received `F_PUNCH` with another `F_PUNCH|F_ACK` even after `_peerReady`; peer replies again — infinite echo, one packet per one-way latency, ending only on packet loss. On a loss-free LAN: thousands of useless 68-byte packets/second each way all session. Gate the reply on `!_peerReady`.

### H30. loopData file-transfer wait loops ignore `_stop` — hang on exit; stale chunks corrupt the next session
**Location:** `src/CoopMod/connectionTCP.cpp:371-622` (waits at 433-434, 453-457, 477-478, 541-542, 561-565, 585-586); destructor 222-238
Every chunk blocks in `while (!isWaitMap) SDL_Delay(20);` with no `_stop` check. Peer disconnects mid-transfer, player quits → `~connectionTCP` joins `_loopThread` which never wakes → game hangs forever on exit. Transfer state machine is never aborted on disconnect: after reconnect, `resetCoopState` sets `isWaitMap=true` and the half-finished transfer resumes, pushing old-session `map_result_data` chunks into the new session → corrupted battlehost/basehost file, failed mission load.

---

## MEDIUM

### M1. `syncCoopInventory` reads `slot_y` from `"slot_x"`
**Location:** `src/CoopMod/connectionTCP.cpp:986`
Deferred inventory moves replay with y = x (immediate handler at 2604 is correct). Item lands in the wrong grid cell on the remote → inventory desync.

### M2. `jsonAddedCoopItems` retry loop clears the wrong array
**Location:** `src/CoopMod/connectionTCP.cpp:1130` (loop from 1070)
On success it nulls `_jsonInventory[i]` instead of `jsonAddedCoopItems[i]`: a pending inventory move at index i is silently wiped (dropped transfer), and processed add_coop_item entries are rescanned forever (saved from duplication only by the `item_exists` guard).

### M3. `TU_COOP`/`kneel_reserved`/`kneel` (and Inventory/endTurn paths) deref SavedBattle/BattleState without null checks
**Location:** `src/CoopMod/connectionTCP.cpp:2738-2758`; also 2643/2657 (Inventory → `getBattleState()` unchecked), 1025, 4911-4912 (`endTurn` — in the always-consume list at 853) and 4942
Packets in flight or parked in `g_rxHold` can be consumed after the battle ended, or before `BattlescapeState` exists (client on the Briefing screen when the host rolls into the alien turn) → null-deref crash right at mission boundaries.

### M4. `set_smoke_tile`/`set_fire_tile`/`destroy_tile` don't null-check `getTile()`
**Location:** `src/CoopMod/connectionTCP.cpp:3471-3473, 3494-3496, 3523-3527`
In the always-consume list (853); `g_rxHold` retains battle packets across battle transitions. Stale packet against a next battle with a smaller map → `getTile` returns nullptr → crash. `current_seed` handler (3693) shows the intended guard pattern.

### M5. Stale `endPlayerTurn` held across battles ends the next mission's first turn instantly
**Location:** `src/CoopMod/connectionTCP.cpp:852-854` + 4926-4948; `g_rxHold` cleared only on disconnect
An `endPlayerTurn` arriving just after the receiver's battle ended rotates in the hold queue until the next mission starts, then fires `EndCoopTurn()` immediately — your turn ends by itself as the battle loads.

### M6. `clearAllReceivedPackets` drains raw socket bytes at arbitrary stream positions — framing lost, both players kicked at mission end
**Location:** `src/CoopMod/connectionTCP.cpp:1290-1319`; trigger `DebriefingState.cpp:338` via 624-629
Discarding an arbitrary byte count from a framed TCP stream can stop mid-frame; the next 4 bytes are read as a length prefix → "invalid message size" → `onConnect = -3` — both honest players get "Server error" right at mission end.

### M7. All cross-thread connection flags are plain non-atomic globals; `sendProgressLoadFileToClient` string raced between threads
**Location:** `src/CoopMod/connectionTCP.cpp:62-120`, h:166-169; concrete race 2072-2073 vs 381-425
Main thread sets `sendFileClient=true` BEFORE assigning the `std::string` path; loopData thread can wake and read the string mid-assignment — UB/crash or wrong (empty) path. The bool/int flags have no ordering guarantees for the isWaitMap handshake.

### M8. `ExplosionBState`: file-scope global wait flag + `explode()` re-executed every tick while the client waits for host confirmation
**Location:** `src/Battlescape/ExplosionBState.cpp:38-39, 386-392, 484-492`; fed by ProjectileFlyBState.cpp:1213-1226, 819-868
`bool coopTaskCompleted` is a global shared by every ExplosionBState (nested/chained explosions consume it for the wrong instance). While waiting for the host's `hasHitUnit`/`unit_death` packet, `think()` falls through to `explode()` each cycle — repeating hit sounds, `checkForCasualties`, `reviveUnconsciousUnits`, `convertInfected`, and duplicating `spawnNewUnit`/`spawnNewItem` for spawn-ammo weapons for the whole network round-trip.

### M9. Kneel-after-turn happens after the TU snapshot is sent and is never itself synced
**Location:** `src/Battlescape/UnitTurnBState.cpp:207-224`; `BattlescapeGame.cpp:2068-2070`
`popState()` sends `turnBattlescapeUnit` with pre-kneel TU; the kneel block then runs with no packet (only the kneel button sends one, BattlescapeState.cpp:2541-2549). Peer keeps the unit standing with undeducted TU — stance desync affects accuracy and exposure.

### M10. Trajectory replay discards the synced origin voxel — by-value parameter dead store
**Location:** `src/Battlescape/Projectile.cpp:425, 462-473, 508-517`
`applyAccuracy(Position origin, ...)` takes origin by value; the coop replay writes the sender's `origin_x/y/z` into the copy just before `return`. `calculateTrajectory` (256) traces from the locally computed origin. If shooter origin differs (kneel state — see M9 — float height, facing), the shot hits on one client and clips a wall on the other. The synced fields exist to prevent exactly this and are inert.

### M11. Precedence bug drops replayed melee when the actor looks "out" remotely
**Location:** `src/Battlescape/MeleeAttackBState.cpp:173` (same shape at 185)
`if (_unit->isOut() || _unit->isOutThresholdExceed() && !coop_action)` — `isOut()` unguarded (correct parenthesization exists at ProjectileFlyBState.cpp:228). Stun/health divergence (see H4, M13) makes the remote discard an attack whose damage landed on the acting side → permanent desync.

### M12. `toggeCoopKneel` falls back to the locally selected unit when the id isn't found
**Location:** `src/Battlescape/BattlescapeState.cpp:2493-2519`
Pointer seeded with `getSelectedUnit()` instead of null (all sibling handlers null-init and return). A kneel packet for a missing unit kneels whatever the receiving player has selected — TU charged, stance changed, compounding desync.

### M13. Client-side `hitUnit()` skips `damage()` but the `hit_unit` correction packet omits armor (and mid-turn morale/energy/fire)
**Location:** `src/Battlescape/TileEngine.cpp:3242-3247, 3318-3332`; receiver `connectionTCP.cpp:3571-3608`
Packet syncs only health/stun/fatalWounds; turn-rollover `current_seed` re-syncs morale/energy/mana/fire but armor is in neither. Client units keep full armor values all battle — wrong Unit Info, wrong client-side evaluations; mid-turn morale/fire stale until rollover.

### M14. Per-unit `motionpoints` read from wrong JSON path — motion scanner zeroed every turn handoff
**Location:** `src/CoopMod/connectionTCP.cpp:3720` (`current_seed`), `:5324` (`PlayerTurnYour`); senders write `root["units"][index]["motionpoints"]` (`BattlescapeState.cpp:2983`, `NextTurnState.cpp:659`)
Receivers read top-level `obj["motionpoints"]` inside the per-unit loop → 0 → `setMotionPointsCoop(0)` for every unit on every turn change. Motion scanner shows no blips for the receiving player. (`unit_death` handlers at 3152/3383 genuinely carry top-level motionpoints — confirming the other two are path bugs.)

### M15. `DebriefingState` missionStatistics gated behind `getServerOwner()==false && getHost()==true` — never true
**Location:** `src/Battlescape/DebriefingState.cpp:1272`
`server_owner` and `onTcpHost` are set together in every connection path (connectionTCP.cpp:1188-1189/1564, connection_udp_glue.cpp:312-313, connection_rendezvous_glue.cpp:342-343/568-569). Client never receives mission statistics — its soldiers' diary kills reference a mission entry that doesn't exist. Almost certainly meant `getServerOwner() == true`.

### M16. Host can open/confirm mission abort during the client's active turn
**Location:** `src/Battlescape/BattlescapeState.cpp:3048-3089`; `src/Battlescape/AbortMissionState.cpp:185-191, 207-229`
`btnAbortClick` has no `isYourTurn` guard; `AbortMissionState` hides OK only for the client. Host confirms abort mid-client-action → `finishBattle` from a mid-action snapshot. Side effect: clicking Abort calls `setPauseOn()` (3062) and only the Cancel path unpauses.

### M17. PVP psi: panic permanently steals the victim; mind control never reverts
**Location:** `src/Battlescape/BattlescapeGame.cpp:3110-3168` (`psiAttackMessage`)
In PVP modes every psi attack is rewritten to `BA_MINDCONTROL` (3111-3116) and the conversion block runs `convertToFaction(FACTION_PLAYER); setOriginalFaction(FACTION_PLAYER);` regardless of original action type — a mere panic transfers control, and overwriting the original faction defeats end-of-turn MC reversion. Permanent unit theft in gamemodes 2/3.

### M18. `moveCoopInventory` name-only fallback moves an arbitrary same-named item
**Location:** `src/Battlescape/BattlescapeState.cpp:4659-4672` (state overwrite 4686-4693)
Third-tier lookup grabs the first item on the whole map matching the name. With item-id drift (see C7), a remote "move rifle to hand" can teleport a different soldier's rifle — or one across the map — into the slot, overwriting its ammo/fuse.

### M19. Inventory-primed grenade lookup misses ground-slot grenades
**Location:** `src/Battlescape/BattlescapeState.cpp:2158-2196`
When `item_id != 0`, receiver scans only `unit->getInventory()`; a grenade primed in the GROUND slot lives in the tile inventory. Fuse set on sender only — one client's grenade explodes, the other's never does.

### M20. Instant-use (typed) medikit items never replicated
**Location:** `src/Battlescape/ActionMenuState.cpp:410-465, 547`; receiver `BattlescapeState.cpp:2316`
BMT_HEAL/STIMULANT/PAINKILLER items apply `medikitUse` locally with no "medkit" packet (only MedikitState sends one, only for BMT_NORMAL); the trailing "action_click" is ignored for BA_USE. Mod-defined instant medikits heal/revive on one client only.

### M21. `delete_base` can delete the wrong (real) base; no cleanup of dangling references
**Location:** `src/CoopMod/connectionTCP.cpp:1961-1988`; `src/Savegame/Base.cpp:64-82` (`_coop_base_id` = random 1..100000, no uniqueness check); sender `BaseDestroyedState.cpp:58-63`
Deletes the FIRST base matching `_coop_base_id` without checking `_coopIcon`/`_coopBase`; own real bases carry ids in the same range → collision deletes the receiver's own base. If the teammate's base is open in `BasescapeState`, deletion dangles `_base` → crash on next `think()`. If the receiver is inside its own Basescape (vector filtered per C4), the mirror isn't in the live vector and survives as a ghost marker.

### M22. Uninitialized `radar_range_coop`/`tr_coop` serialized to the peer every frame
**Location:** `src/Geoscape/GeoscapeState.cpp:1407-1437`
For a base with no completed radar the value is stack garbage; peer stores it into `Base::_radar_range_coop` and draws the mirror base's radar circle (Globe.cpp:1252-1264) — garbage-sized rings / UB.

### M23. `changeHost` token race — both players can end up non-host, mission start softlocks
**Location:** `src/Geoscape/ConfirmLandingState.cpp:246-283`, `GeoscapeState.cpp:4768-4788`, `connectionTCP.cpp:2361-2378`
Sender does `setHost(true)` immediately with no ack; receiver unconditionally `setHost(false)`. Both trigger landings in the same window → both process the other's packet → both `getHost()==false` → both wait in `CoopState(88/77)` forever. No sequencing/rejection.

### M24. `coopBase` handler reads `obj["crafts"]` but sender writes `craft_count`; missing duplicate guard
**Location:** `src/CoopMod/connectionTCP.cpp:6193` vs 6123-6124; 6210-6260 (no `alreadyExists` check, unlike `coopBase2`/`coopBase3` at 6462-6475/6547-6560)
`playersCrafts` always 0 (wrong limit checks); replayed `coopBase` on reconnect creates duplicate mirror bases on the host's globe (`deleteAllCoopBases` only runs when `_coopCampaign == true`, 7471).

### M25. Research-complete sync: `base_lot` vs `base_lon` typo; bonus (getOneFree) research never granted on the peer
**Location:** sender `src/Geoscape/ResearchCompleteState.cpp:114`; receiver `GeoscapeState.cpp:1244-1245, 1278`
Longitude sent as `base_lot`, read as `base_lon` (always 0.0) — base match never succeeds, popup/credit falls back to `getSelectedBase()`. Receiver registers only the core topic; `bonus_name` is looked up but never passed to `addFinishedResearch` — tech sets diverge until the next full save exchange.

### M26. Silent save failure when coop active
**Location:** `src/Menu/SaveGameState.cpp:212-216`
`if (!moveFile(...) && getCoopStatic() == false) throw ...` — in coop a failed rename (file locked by cloud sync/AV, disk full) is swallowed; player believes the save succeeded but only the `.bak` exists.

### M27. Per-frame multi-KB JSON flood from Geoscape think()
**Location:** `src/Geoscape/GeoscapeState.cpp:1306-1630` (`target_positions`), 1639-1692 (`time`), `src/Geoscape/DogfightState.cpp:960-997` (`ufo_damage` per tick)
Built with `toStyledString` and sent every frame (~60/s), not on change, not throttled. Late-game saves: constant CPU/bandwidth drain; floods the receiver's single hold-queue, delaying important packets behind stale position updates — Geoscape stutter, multi-second sync lag.

### M28. Relist worker wipes a user cancel and resurrects host state after the host quit
**Location:** `src/CoopMod/connectionUDP/connection_rendezvous_glue.cpp:332-345` (also 392)
Worker sleeps 1000 ms then `g_cancelRendezvous.store(false)` and unconditionally sets `onConnect = 2; server_owner = true; onTcpHost = true`. Host pressing disconnect inside that window is erased and re-listed on the master server; failure branch (392) can stomp `onConnect = -3` over main-thread state later.

### M29. Detached INIT_SERVER thread dereferences `s_connectionUDP` while it can be destroyed
**Location:** `src/CoopMod/connectionUDP/connection_udp_glue.cpp:331-352`; `s_connectionUDP.reset()` at 262, 300, 412
Detached thread polls `s_connectionUDP->isPeerReady()` for 30 s unsynchronized; user cancels join → main thread resets the unique_ptr → next poll is a use-after-free. Same TOCTOU between relist-worker `startUdpPeer()` and main-thread `stopUdpPeer()`.

### M30. Rendezvous server: blocking TCP send while holding the global rooms mutex
**Location:** `src/CoopMod/connectionUDP/rendezvous_server.cpp:1085` (inside `g_roomsMutex` scope 1056-1086) via `sendFrame` 293-302
One player with a wedged connection at PEER_READY time blocks the mutex — all registrations, joins, creates, list requests server-wide stall until the OS send fails.

### M31. Rendezvous server: socket close race — server crash
**Location:** `src/CoopMod/connectionUDP/rendezvous_server.cpp:919-920` (closes `player->tcp` without `tcpMutex`)
`maybeFinishRoomLocked` (udpThread) may be mid-`sendServerMsg` on the same socket; closing it mid-send is a use-after-free of the SDL_net socket — master server crashes, all matchmaking down.

### M32. Rendezvous server: zombie rooms, duplicate playerIds, invite-code roomIds burned forever
**Location:** `src/CoopMod/connectionUDP/rendezvous_server.cpp:908-917, 752, 1105-1113` (`kRoomTtlMs = 0`)
(a) Host TCP drop with a client already in the room leaves `players = [client(id=2)]`; next joiner also gets id 2; both compute `isHost = (localPlayerId == 1)` false and set `localPlayerId=2/remotePlayerId=1`, so every UDP packet fails the `senderId != remotePlayerId` check (connectionUDP.cpp:350) — punch never authenticates, both hang (compounded by H27). (b) Rooms with `sessionKeyReady=true` never CLOSE_ROOM'd are never GC'd; legacy JOIN flow never calls `rememberHostRoomForClose` — a user-chosen invite-code roomId is "full or already locked" until server restart.

### M33. UDP transport rebind has no retry on the port just released by the rendezvous socket
**Location:** `src/CoopMod/connectionUDP/connectionUDP.cpp:188` vs `rendezvous_client.cpp:231-251`
Windows can be slow releasing the socket; `openClientUdp` retries with 250 ms delay, `start()` does not — one failed `SDLNet_UDP_Open` aborts with `onConnect = -3` while the remote peer proceeds and hangs waiting.

### M34. `handleUdpRemotePeerLost` drains SPSC queues from the ping thread
**Location:** `connection_udp_glue.cpp:185` → `connection_rendezvous_glue.cpp:597`; queue `connectionTCP.h:90-115`
Second concurrent consumer of `g_rxQ` against main-thread `updateCoopTask` (connectionTCP.cpp:786) and of `g_txQ` against game-thread producers — duplicated/moved-from strings or lost packets exactly at peer-disconnect. (Same root design flaw as C8.)

### M35. ModCheckMenu renames xcom1/xcom2 in the required-mods vector before comparing — contradictory false positive every time
**Location:** `src/CoopMod/ModCheckMenu.cpp:252-297`
First loop rewrites `"xcom1"` → `"xcom1 (UFO Defense)"` in place; second loop's `std::find` with `local_mod == "xcom1"` no longer matches. INCOMPATIBLE MODS screen always shows "xcom1 (UFO Defense) — This mod is enabled." plus "— Disable extra mod." Misleads players fixing a genuine mismatch (the actual join gate at connectionTCP.cpp:936 is unaffected).

### M36. `LobbyMenu` `setColumns(188, 188, 60, 40)` — first argument is the column COUNT
**Location:** `src/CoopMod/LobbyMenu.cpp:234`; `TextList::setColumns` (TextList.cpp:467)
Reads 185 nonexistent varargs — UB that currently "works" because `addRow(3, ...)` touches only `_columns[0..2]`. Should be `setColumns(3, 188, 60, 40)`.

### M37. (Uncommitted) `consumeMasterServerUnavailableWarning()` has zero callers — the new non-fatal notice is never shown
**Location:** `src/CoopMod/connectionUDP/connection_rendezvous_glue.cpp:93`, `.h:24`
In a keyless build, refresh silently returns an empty list and the flag stays latched — the feature the diff describes is unwired. (The part that stops passive refreshes from setting `onConnect = -3` is correct.)

### M38. FilterMenu never loads `filters.json`; OK (or any keypress) silently resets saved filters
**Location:** `src/CoopMod/FilterMenu.cpp:31-154, 294-331` (never loads), `:152` (`onKeyboardPress` with no key = SDLK_ANY — any keystroke applies-and-closes)
Combos always start at defaults; `btnOKClick` writes those wholesale — saved "Compatible mods only"/region filters wiped by opening the dialog and pressing any key.

### M39. CoopState "Disconnect" button on states 52 and 77 does not disconnect
**Location:** `src/CoopMod/CoopState.cpp:728-756` (vs labels at 125, 207)
`previous()` calls `disconnectTCP()` only for states 50/1/88/3/4/15/53; for 52 (client load-progress) and 77 (base-defense wait) it just pops — connection and pending transfer stay live, client left half-synchronized while the host thinks it's still loading.

### M40. Error-state CoopState constructors pop whatever state is on top
**Location:** `src/CoopMod/CoopState.cpp:401, 413, 422, 506, 521` (states 440/444/16/1000/3000)
`onConnect == -3` can be set mid-session by the packet exception handler (connectionTCP.cpp:886); `updateCoopTask` then pushes `CoopState(440)` (751) whose ctor pops the live Geoscape/Battlescape/Lobby — after OK the UI stack is in the wrong place.

### M41. CrashHandler VEH logs every first-chance C++ exception; `log()` opens a new file per message
**Location:** `src/CoopMod/CrashHandler.cpp:262-282, 404-417`; `connectionTCP.h:77-80` (DebugLog)
MSVC C++ throws (0xE06D7363) that the game catches in normal flow each create a crashlog file and run `SymFromAddr` — frame hitches and a `crashlogs/` directory full of spurious files during healthy play. With `logInfoToFile && debugMode` (+ `logPacketMessages`, connectionTCP.cpp:837-846) every network packet creates one file — thousands per session, severe I/O stalls (the cause of the CoopState(942) latency warning). POSIX `log()` additionally runs a full `backtrace()` per message.

---

## LOW

### L1. Host leaks a full deep `SavedGame` copy every turn cycle (+ other per-event leaks)
**Location:** `src/CoopMod/connectionTCP.cpp:5440-5444` (`PlayerTurnYour`); `src/Battlescape/NextTurnState.cpp:622-626`; also `writeHostMapFile` 7959, `writeHostMapSaveProgressFile` 7992, `changeBaseName` 2274; `GeoscapeState.cpp:937, 983` (per-mission `SavedGame`/`Base` copies — note the shallow `Base` copy at 983 must NOT simply be deleted: double-free); `GeoscapeState.cpp:549-551` + `ConfirmLandingState.cpp:424-425` (`BriefingState` allocated, configured, never pushed or freed)
The `NextTurnState` copy uses the compiler-generated copy ctor of a raw-pointer-owning class — leaked today, one refactor away from a double-free. Multi-hour sessions steadily bloat memory (late-campaign saves are large).

### L2. `DebriefingState` soldier match: `A || B && C && D && E` precedence
**Location:** `src/CoopMod/connectionTCP.cpp:5112`
Coop-name match skips the nationality/init-TU/base disambiguators. Two soldiers sharing a coop name → post-mission rank/stat updates land on the wrong soldier.

### L3. `stunlevel` parsed but never applied in four packet handlers
**Location:** `src/Battlescape/BattlescapeGame.cpp:192, 332, 512, 627`
`setStunlevelCoop` exists (used by `shootPlayerTarget`, BattlescapeState.cpp:4526, and death/hit handlers) but move/turn/psi/melee never call it — stun divergence persists, arming M11's `isOutThresholdExceed` mismatch and one-sided unconsciousness.

### L4. `handleStateCoop` lacks the null end-turn-marker guard `handleState` has
**Location:** `src/Battlescape/BattlescapeGame.cpp:1976-1985` (vs 1958-1966); callers connectionTCP.cpp:663, MiniMapState.cpp:174
`_states.front()` can be the nullptr end-turn marker between frames; `handleStateCoop` calls `->think()` directly — crash window while the minimap or another screen is open, and the pending end turn is swallowed.

### L5. `Camera::isOnScreen()` unconditionally true in coop — camera never auto-centers
**Location:** `src/Battlescape/Camera.cpp:576-580`
Blanket `getCoopStatic()` guard disables auto-centering for walking units, projectiles, explosions, reaction fire — in all coop contexts, including your own turn.

### L6. `generateCraftSoldiers` uses `front()` on bases/crafts without empty checks
**Location:** `src/CoopMod/connectionTCP.cpp:7414-7430`
Empty vector `front()` is UB — crash for a client joining new-battle flow with a save lacking a base or craft.

### L7. `hit_tile` leaks a `RuleDamageType` per packet
**Location:** `src/CoopMod/connectionTCP.cpp:3298-3345`
`hitCoop` takes `const RuleDamageType*` and doesn't own it; never deleted (also leaked outright when `_battleActions` is empty). Unbounded small leak.

### L8. Recv drain loop can block when data is an exact 16 KB multiple
**Location:** `src/CoopMod/connectionTCP.cpp:1410-1425` (client), 1635-1652 (host)
Loops `recv until bytes < sizeof(buf)` without re-checking readiness; exact N*16384 bytes → next recv blocks (~1 s until the next PING) — intermittent hitches during large map transfers.

### L9. `std::stoi` overflow holes on port parsing (TCP path)
**Location:** `src/CoopMod/connectionTCP.cpp:7635-7648` (`valid_port`), 1149-1173 (`getPortFromAddress`)
All-digits check then `stoi`; 11+ digit input throws uncaught `std::out_of_range` on the UI path → process death. (UDP host path is H23.)

### L10. `coopBase` handler (host) lacks the duplicate-base guard `coopBase2`/`coopBase3` have
**Location:** `src/CoopMod/connectionTCP.cpp:6210-6260` (compare 6462-6475, 6547-6560)
Redelivered markers append duplicate coop base icons (see also M24).

### L11. `LoadGameState` hard-resets `_coopGamemode = 0` on every load
**Location:** `src/Menu/LoadGameState.cpp:53, 69`
Including coop sync loads mid-session; PVP-gamemode guards (`getCoopGamemode()==2/3`) evaluate wrong until the next `coopBase2` packet restores it.

### L12. LAN responder permanently disabled if UDP 39002 busy at first start
**Location:** `src/CoopMod/connectionUDP/connection_lan_discovery.cpp:127-133, 225-226`
Failed `SDLNet_UDP_Open` leaves the thread joinable; `startLanDiscoveryHost` early-returns on `joinable()` forever — LAN hosting silently invisible for the rest of the process (e.g. second instance on the same PC). Minor `SDLNet_Init` refcount leaks on some paths.

### L13. `refreshLanServerList` sends the discovery broadcast exactly once
**Location:** `connection_lan_discovery.cpp:278-282` (contrast 361-366: resend every 100 ms)
One lost datagram = empty LAN list for that 500 ms refresh window — hosts intermittently missing until manual refresh.

### L14. Background server-list refresh clears the shared `g_cancelRendezvous` flag
**Location:** `connection_rendezvous_glue.cpp:1188, 1647`
Can erase a just-issued cancel of a concurrent host/join wait — cancel button that doesn't take effect.

### L15. Cross-thread writes to plain globals `onConnect`/`server_owner`/`coopSession`/`s_udpEnabled`
**Location:** `connection_rendezvous_glue.cpp:344, 392, 603-621`; `connection_udp_glue.cpp:178-196, 310-314`
Genuine data races underlying M28/M34 symptoms.

### L16. Displayed ping inflated after retransmits
**Location:** `connectionUDP.cpp:711-713`
`_rttMs = now − firstSentMs` includes retransmit backoff; after a loss burst the ping display shows hundreds of ms on a healthy link.

### L17. `asCString()` on `session_id` throws on non-string JSON — `std::terminate` in a worker thread
**Location:** `rendezvous_client.cpp:461, 786`
Only reachable with a mismatched/newer server build, but the escape path is process exit.

### L18. Medikit packet sends the ADDRESS of `_action->result` (bool `true`), stim/painkiller packets omit `"time"`
**Location:** `src/Battlescape/MedikitState.cpp:267` (also 316, 358); receiver `connectionTCP.cpp:3036`
`obj["action_result"] = &_action->result;` picks jsoncpp's bool overload. Latent (receiver currently ignores it), but any future failure-result handling silently never works.

### L19. (Uncommitted) Lobby "Waiting for players on port N" shows wrong/stale port for UDP sessions and on the client
**Location:** `src/CoopMod/LobbyMenu.cpp:253, 530, 540`; `tcp_port` only assigned in `hostTCPServer`/`connectTCPServer` (connectionTCP.cpp:7672, 7715)
UDP-hosted lobby shows 3000 or a leftover TCP port; client's label claims to be "waiting for players" on the port it dialed. Misleading for the exact "which port do I open" use case. Also shown when the lobby is full/locked.

### L20. Empty-port fallbacks resurrect "3000", contradicting the new 61008 default
**Location:** `src/CoopMod/HostMenu.cpp:306`, `src/CoopMod/CoopMenu.cpp:326` (vs DirectConnect.cpp:214)
A `host_address.json`/`client_address.json` saved with a cleared port field silently diverges host and client defaults.

### L21. Invalid UDP port bails out after the "Connecting..." dialog was pushed
**Location:** `src/CoopMod/DirectConnect.cpp:501, 528-539`
`parseUdpPort` failure just returns, leaving a Connecting dialog that never progresses (Cancel works, but the flow looks hung). Validate before pushing.

### L22. Direct-connect "PORT" field is passed as the client's LOCAL UDP bind port
**Location:** `src/CoopMod/DirectConnect.cpp:535-539`, `src/CoopMod/ServerList.cpp:1012-1019`; glue 840-843
Users enter the host's port; it instead binds the client socket to that number — a same-machine test (default 127.0.0.1) collides with the host's own bound port and the join fails with no visible reason.

### L23. `PasswordCheckMenu::_serverinfo` left uninitialized by the (ip, player, port, ...) constructor
**Location:** `src/CoopMod/PasswordCheckMenu.h:96`, `.cpp:109`
`if (_serverinfo && ...)` on an indeterminate pointer — currently saved by short-circuit only. Initialize to `nullptr`.

### L24. Lobby Cancel/Escape unconditionally flips `isPlayerReady`
**Location:** `src/CoopMod/LobbyMenu.cpp:385`
Toggle happens before any branch — Escape in the lobby, or Cancel on a locked/disconnected session, silently flips READY out of sync with what was communicated.

### L25. Lobby latency sorted lexicographically
**Location:** `src/CoopMod/LobbyMenu.cpp:81-98, 342`
`latency` is a `std::string`; "9" sorts after "10". Cosmetic with 2 players.

### L26. `src/Menu/ChatMenu.cpp` is stale dead code not in the build
**Location:** `src/Menu/ChatMenu.cpp` (only `CoopMod/ChatMenu.cpp` is compiled per `src/CMakeLists.txt`)
Older API, 5-message cap; any bugfix applied there has no effect. Delete to avoid patching the wrong file.

---

## Cross-cutting themes

1. **Melee is the least-safe replay path.** Turn (post-fix) and shooting follow the healthy pattern (replay returns before charging, or sender transmits pre-action stats and the remote replays the charge once). Melee transmits post-spend stats AND replays the spend (H4), skips sync entirely for terrain hits (H5), and has the precedence (M11) and null-lookup (C6) holes.
2. **The `isYourTurn` off-turn cluster (1||3||4) is applied inconsistently.** Several guards test only `==1` or `!=1` (H9, H12, H13), and `playableUnitSelected()`'s state-1 shortcut inverts the protection for everything downstream.
3. **`SPSCQueue` + plain-global flags underpin most "random" network weirdness.** C8/M7/M34/L15 are one root cause: the transport was written single-threaded-ish and grew producers/consumers. Making the queues MPMC (or a mutex + deque) and the flags atomic would eliminate a whole class of intermittent bugs.
4. **`g_rxHold` never expires across battles.** M3/M4/M5 and part of H3 come from stale battle packets surviving into the next mission. Clearing battle-scoped packets at battle end would fix several crashes at once.
5. **Item identity is fragile.** C7 (phantom items skew the id counter) plus M18's name-based fallback turn a single mismatch into escalating cross-map item corruption.
6. **Copy-paste/typo family:** `status == 4` twice (H1), `slot_x` for slot_y (M1), wrong array cleared (M2), `setMonthsPassed(daysPassed)` (H18), `base_lot`/`base_lon` (M25), `crafts`/`craft_count` (M24), `= true` in a condition (H22), `|| 1==1` debug leftover (C2), `setColumns(188,...)` (M36). A compiler-warning pass (`-Wparentheses -Wunused-value`) would have caught several.
