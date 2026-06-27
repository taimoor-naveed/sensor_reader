# Dashboard v2 — requested improvements (brainstorming notes, 2026-06-27)

v1 is built and merged to `main`: passive monitor + on-demand GATT backfill + responsive
dashboard, 42 tests passing, validated on real hardware. The user then asked for a v2 round
of improvements. We were mid-brainstorming when the session was cleared — resume from here.

## User's requests (verbatim intent)
1. **Front-end design is not good** — wants a better-looking dashboard.
2. **Live values don't update regularly.** Root cause: factory firmware advertises one field per
   ~10 s, staggered (see hardware findings). Candidate fix: while the dashboard is open, connect
   via GATT and subscribe to the `DATA` characteristic (`EBE0CCC1`) for ~1 Hz smooth updates.
3. **Replace the RSSI dBm number with a phone-style 4-bar signal-strength indicator.**
4. **Graphs must be fluid & interactive:** drag/pan left–right, pinch/scroll to expand/contract
   the timeline (zoom), and load/render data for the visible window. Current charts are static
   with fixed hour/day/week buttons.
5. **Backfill needs progress UX:** "Connecting to device…" → "Reading…" → a progress bar/animation
   showing how many records have been read (use NUMREC total + count received). Currently the
   button just disables with no feedback.
6. **BUG: after backfill, banner still says "X missing".** Root cause: the device only stores
   ~54 h of history since boot; gaps older than that (or before the app ran) are unrecoverable.
   Fix: scope gap reporting to what's actually fillable (within the device's history range),
   and/or clearly label unrecoverable gaps so it isn't alarming.

## Open design questions (resolve with user before building)
- **Live updates:** connect-while-viewing via the `DATA` GATT subscription (best freshness, uses
  some battery while open) vs passive-only with a per-field "updated Ns ago" indicator? (Lean:
  GATT subscribe while a client is connected, fall back to passive when idle.)
- **Interactive charts:** Chart.js + `chartjs-plugin-zoom` (drag-pan + wheel/pinch-zoom) with
  dynamic loading via a new `/api/history?from=<ts>&to=<ts>` (currently only hour/day/week
  presets). Confirm library/approach (must stay vendored/offline).
- **Backfill progress channel:** Server-Sent Events stream vs polling a `/api/backfill/status`
  endpoint while backfill runs async and reports records-received / total.
- **Gap handling:** hide unrecoverable gaps entirely vs show them greyed/labeled "unrecoverable".

## Current behaviors to change (file refs)
- `static/index.html`: `setInterval(refreshCurrent,5000)`, RSSI shown as dBm (`#rssi`), gap text
  "N missing hour(s) in the last week", static charts (no pan/zoom), `doFill` has no progress.
- `web.py`: `/api/history` only takes range presets (`_RANGES` hour/day/week); `/api/gaps`
  uses a 1-week window; `/api/backfill` is synchronous (no progress).
- Backfill correctness already fixed & hardware-validated (`gatt.backfill`, 4-byte clock).

## Next steps
Resume brainstorming → finalize design → write spec to `docs/superpowers/specs/` → writing-plans
→ subagent-driven-development on a NEW feature branch (v1 is on `main`).
