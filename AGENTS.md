# OpenXcom-Coop-Mod — Project guide

OpenXcom Extended (OXCE) fork that adds a co-op multiplayer mod. C++ / SDL 1.2.
The co-op code lives under `src/CoopMod/` (UI dialogs, TCP/UDP networking,
rendezvous/server-list). The rest of the tree is upstream OpenXcom/OXCE.

## Build & run (Windows, Visual Studio 2022)

- Config is **Release | x64** only. The Debug config is unmaintained upstream
  (missing include paths and link inputs) — don't use it.
- Build: `MSBuild src\OpenXcom.2010.sln -p:Configuration=Release -p:Platform=x64 -m`
  (or the `Build-Xcom` PowerShell helper if it's in the profile).
- Output: `bin\x64\Release\OpenXcom.exe` (needs the original X-COM game data to run).
- The working build relies on local-only files that are deliberately kept out of
  git (`Directory.Build.props`, the libsodium binary, a Release `jsoncpp.dll`).
  If a clean checkout won't build, that scaffolding is missing — see the build
  reference doc below.

## Git workflow (fork-first, big-PR-later) — as of 2026-07-03

- Remotes: `origin` = upstream (xcomcoopdev/OpenXcom-Coop-Mod, original developer),
  `fork` = ours (NonPolynomialTim/OpenXcom-Coop-Mod).
- Upstream PR merging is stalled on the original developer's side, so **all work
  lands on the fork now**: develop on a topic branch, merge to `fork`'s `main`,
  push branch + main to `fork`.
- **Do NOT open new PRs against `origin`** until the user says otherwise. Plan is
  to possibly become a maintainer upstream, then transfer everything in one big
  PR from `fork/main`. Keep individual topic branches pushed to `fork` so the
  work can still be split into per-fix PRs if the upstream dev prefers that.
- Pre-existing open upstream PRs (#20, #10, #9, #8, #5) stay open; pushing to
  their fork branches updates them, which is fine — just don't create new ones.
- Never push directly to `origin`.

## Reference documentation

These are **load-on-demand** docs. Read the one matching your task; do **not**
pull them all into context up front. Each entry says when it's relevant.

- [`.agents/docs/ui-dialog-windows.md`](.agents/docs/ui-dialog-windows.md) —
  **Read when creating or editing a dialog/menu/window** (anything that is a
  `State` subclass, especially under `src/CoopMod/`). Covers how dialogs are
  defined, the construction recipe, every common widget (buttons, text labels,
  text-input fields, dropdowns, lists), the event/`ActionHandler` model, how to
  wire inputs to backing data, opening/closing states, and a copy-paste
  skeleton for a new dialog.
- [`.agents/docs/coop-networking.md`](.agents/docs/coop-networking.md) —
  **Read when working on co-op networking** (TCP/UDP, the rendezvous/master
  server, the Server Browser, connect/host flows). Explains the two transports,
  why UDP and the public server browser require the rendezvous server while TCP
  works offline, the blank open-source `rendezvous_config.cpp`, how to run your
  own rendezvous server, the "master server unavailable" notice, and the fixed
  Server-Browser teardown bug.
- [`.agents/docs/coop-soldier-transfer.md`](.agents/docs/coop-soldier-transfer.md)
  — **Read when working on soldier ownership transfer or coop save
  persistence.** The Give-Unit dialog and its four entry points, the
  guest-soldier model (a soldier lives in one save, `coopBase` = station),
  the peer-base-visit swapped-world hazard, and — most important — the
  **host-save-is-the-single-authority** model (host `.sav` embeds the client
  world blob; loading rolls both rosters back together; receipts were deleted).
  Includes the X-Com Files mod-compatibility analysis.
- [`.agents/docs/coop-test-harness.md`](.agents/docs/coop-test-harness.md) —
  **Read when testing coop features end-to-end** (autonomous two-client
  tests). In-game TestServer (OXC_TEST_PORT) + Python driver in
  `tools/coop_test/`; full command list, the session-bootstrap sequence, and
  hard-won SDL_net/identical-roster/dedup gotchas.
- [`.agents/docs/p2p-webrtc-migration-plan.md`](.agents/docs/p2p-webrtc-migration-plan.md)
  — **Read before implementing the planned networking rewrite** (replacing the
  rendezvous server / custom UDP with libdatachannel ICE + STUN/TURN +
  Cloudflare Worker signaling). Approved design, requirements (4-player-capable
  plumbing, keep TCP fallback), module plan, migration order. EOS was evaluated
  and rejected (GPL conflict) — don't re-propose it.
- [`.agents/docs/geoscape-timescaling.md`](.agents/docs/geoscape-timescaling.md)
  — **Read when working on the geoscape time step/speed** (the 5 Secs … 1 Day
  buttons, `timeAdvance()`) or its **co-op time synchronization** (the `"time"`
  packet, `other_time_speed_coop`, the "both must match or fall back to 5 Secs"
  rule, host-only-speed mode). Includes the speed→timeSpan table and notes for
  showing a teammate's selected speed.

## Conventions

- Indentation is **tabs** (see `.editorconfig` / `.astylerc`).
- New `.cpp`/`.h` files under `src/CoopMod/` must be registered in the build.
  The committed `OpenXcom.2010.vcxproj` and `CMakeLists.txt` are known to lag
  behind newly added co-op sources — verify your file is listed there (or in
  the local `Directory.Build.props`) or it silently won't compile/link.
