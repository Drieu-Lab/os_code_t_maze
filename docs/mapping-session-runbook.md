# Mapping-session runbook — build `led_map.json`

A **mapping session** is a short, deliberate recording in which you fire every
reward valve, break every beam by hand, and touch every lick port, so the video
captures each board indicator LED blinking at a *known, isolated* time.
`sync_fit --map` then binds each LED to its serial channel and writes the
authoritative [`host/led_map.json`](../host/led_map.json) that every later
session registers against (see
[`docs/design/cross-stream-sync-design.md`](design/cross-stream-sync-design.md)
§3.1/3.3; FW-0045, ADR-0153).

The board region searched is **self-calibrating**: the tool finds the persistent
red/green fiducial LEDs and searches only inside their convex hull, so you no
longer measure or type a board rectangle, and off-board motion (a rat in the
maze, or your own hand while firing) is outside the region and does not flood the
detector.

Do this **once now** (it validates the whole offline sync pipeline on the clean
case it was designed for and answers whether the ENTRY/EXIT beam LEDs are even
visible), and **again** whenever the board changes — most importantly after the
Phase 2 **cue LEDs** are installed, which this first map cannot include.

## Why a dedicated session (and the full log)

- **A clean, deliberate firing of every channel** — a rat session only fires the
  channels the rat happens to use (it never opens the left/right reward or the
  rinse/drain valves), so a dedicated session is how those LEDs get mapped.
  Off-board motion no longer floods the detector — the fiducial ROI confines the
  search to the board (ADR-0153), so even your hand near the board while firing,
  or a rat left in the maze, is outside the region — but a no-rat session still
  gives the cleanest, most isolated firings.
- **Isolated firings** — leave a gap between events of the same channel so the
  matcher pairs them unambiguously.
- **The FULL serial log, not the bundle capture.** The bundle's per-session
  `serial/` capture keeps only body beams + the **central** well's
  lick/reward/solenoid (by design, see `core/session/session_serial.py`). It
  **drops the left/right-reward and rinse/drain valve acks** — exactly the LEDs a
  mapping session exists to capture. So point `sync_fit --map` at the **full
  `rig_logger` CSV** with `--serial`, which keeps every port.

## What you need running

Four programs (each in its own terminal; conda env `drieu_t_maze`; see
[`PROGRAMS.md`](../PROGRAMS.md)):

1. `python -m tmaze.io.rig_broker --port COM3` — owns the Due, fans serial out.
2. `python -m tmaze.io.rig_logger` — **the full log**; note the path it prints
   (`host/data/<animal>_<YYYYMMDD_HHMMSS>.csv`). This is the `--serial` file.
3. `python -m tmaze.ui.maze_gui` — the Solenoids button grid (rows Left/Right/
   Center, columns `R1 R2 R3 DRAIN RINSE`; each button sends `R<port><sol>` and
   the Due acks `SOL,<port>,<sol>`).
4. `python -m tmaze.ui.experiment_control` — for the **camera recording**. Enter
   a throwaway id (see below) and use the Rig-view **Session** panel to
   start/stop; that writes the bundle with `video/<id>.mp4` + `video/<id>.csv`
   (the sidecar `sync_fit` needs).

## Step by step

**0. Before recording.** Rig lights as during a normal session (the board LEDs
must be the brightest changing things), camera focused and the electronics board
in frame, no rat on the maze. Pick a **throwaway session id** so this never
enters the training logs — e.g. `000` (the same convention as the existing
`000_…`/`123_…` test bundles). You will add the resulting id to
`host/session_ignore.json` in step 6.

**1. Start the recorders.** Start `rig_logger` (full log), then in
`experiment_control` enter id `000` and **Start** the Session (camera begins
recording to a new bundle). Let it run for the whole procedure.

**2. Fire every channel, isolated, with dwell and repeats.** Hold each event
**≥ ~150 ms** (a video frame is 40 ms and spans under 60 ms are dropped as
sub-frame) and do **≥ 5 repeats** per channel (the binder needs ≥ 3), leaving
**~2 s between firings** so events don't merge:

- **Reward + wash valves** (`maze_gui` grid) — for **each** of Left, Right,
  Center and **each** column `R1 R2 R3 DRAIN RINSE`: click ~5×, ~1–2 s apart.
  (`R2` is the reward spout the task actually uses, `REWARD_SOLENOID_INDEX`; the
  others still have their own indicator LEDs, so fire them all.)
- **Body beams** — break each of the six by hand ~5×, holding ~0.5 s, one beam
  fully done before the next. Order (firmware `SENSOR_PINS`): `0` ENTRY, `1`
  EXIT, `2` LEFT-choice, `3` RIGHT-choice, `4` LEFT-return, `5` RIGHT-return.
- **Lick ports** — touch each a few times (a damp cotton swab reads cleanly):
  `L0` left, `L1` right, `L2` center.
- **Cues** — *skip*: there is no cue LED yet (Phase 2). Note it for the re-map.

Announcing each channel out loud while the log records makes the CSV easy to
read afterwards.

**3. Stop.** Stop the Session in `experiment_control` (finishes the video +
sidecar), then stop `rig_logger`. Note the **bundle id** (the `000_…` folder
under `…/phase3_recordings/`) and the **full-log path** from step 1.

**4. Build the map.**

```
python -m tmaze.tools.sync_fit <recordings>/phase3_recordings/000_<stamp> \
    --map --serial host/data/000_<stamp>.csv
```

- **No `--board-rect` needed** — the tool finds the persistent red/green fiducial
  LEDs and uses their convex hull as the board region automatically. Pass
  `--board-rect X0 Y0 X1 Y1` only as a fallback if the fiducials aren't found
  (e.g. the board is off-screen or unlit).
- `--map` writes a QA **overlay PNG** next to the map
  (`led_map_overlay_<id>.png`) — you use it in step 5.
- `--map-out <path>` writes elsewhere than `host/led_map.json` if you want to
  inspect before installing.

**5. Review the report and the overlay.** Expect **every fired channel** to appear
as a bound LED with a plausible lag (start lag ~ +30 ms for lit-on-event LEDs,
~ +60 ms for the dark-on-event beams). Specifically check:

- **The fiducial line** — it should report a handful of persistent red *and*
  green LEDs and a board ROI. If it reports none, no auto-ROI was built; fall
  back to `--board-rect`.
- **Open the overlay PNG** and confirm the yellow ROI polygon lands **inside the
  physical retroreflective marker corners** on the board, with the fiducials
  (red/green rings) and the located LEDs (white crosses) where you expect. This
  is the manual marker check — the markers are a reference, not detected
  (ADR-0153; a repeatable marker scheme is FW-0059).
- **left/right reward + rinse/drain** now bind (they could not from a rat
  session — that is the whole point of `--serial`);
- **ENTRY/EXIT beams `S0`/`S1`** — the open question from design §3.7: if they
  stay *silent/unbound*, their channel LEDs are hidden by wiring or face away.
  Record which; a wider board or a `--board-rect` re-run can rule out that they
  sit outside the searched region.

Then open `host/led_map.json`: the persistent `fiducials` (red/green), the
`board_poly` ROI outline + its `board_rect`, and one entry per LED with `led_id`,
`ref_px`, `channel`, and `polarity`.

**6. Keep the test session out of the records.** Add the mapping bundle's id to
the `ignore` list in [`host/session_ignore.json`](../host/session_ignore.json)
(ADR-0097) so the registry/training-log builders skip it.

**7. Confirm it registers on a real session.** On the next ordinary (with-rat)
session, a plain `python -m tmaze.tools.sync_fit <bundle>` should now load the
new map, recover the **board pose from the fiducials** (`registration ok`), and
bind the LEDs passively — no manual firing ever again. Add `--overlay` if you
want the QA PNG for a with-rat run too.

## Quick reference

| Channel | How to fire | Serial line | Notes |
|---|---|---|---|
| Reward/wash valves | `maze_gui` Solenoids grid, `R<port><sol>` | `SOL,<port>,<sol>` | port 0 L / 1 R / 2 C; sol 0–2 = R1–R3, 3 DRAIN, 4 RINSE; **needs `--serial` full log** for L/R + drain/rinse |
| Body beams | break by hand | `S,<i>,0` (broken) | i: 0 ENTRY,1 EXIT,2 LCHOICE,3 RCHOICE,4 LRETURN,5 RRETURN |
| Lick ports | touch (damp swab) | `L,<i>,0` (contact) | i: 0 left, 1 right, 2 center |
| Cues | — | — | no LED until Phase 2; re-map afterwards |

Dwell **≥150 ms**, **≥5 repeats**, **~2 s** apart; hold each channel separate in
time. The map's fiducials, `board_poly`/`board_rect`, and detector settings are
stored in the JSON; later runs re-find the fiducials, recover the board pose, and
reuse the ROI automatically.
