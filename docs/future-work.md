# Future work backlog

A running list of **candidate code changes** that have been deliberately deferred
- ideas worth doing later but out of scope for the change that surfaced them.
The goal is that a future reader (human or agent) can reconstruct the idea, its
motivation, and where it fits without the original context in the room.

## How this differs from other docs

- **ADRs (`docs/decisions/`)** record decisions *already made* - immutable,
  "why we did X." This backlog is the opposite: *candidates not yet decided*.
  When a backlog item is implemented, capture the reasoning in a new ADR and
  **remove the entry from this file** (or mark it Done with a link to the ADR).
- **Planning / Gantt (in the `os_code_t_maze_resources` repo)** is project and
  timeline planning. This file is narrower: concrete code changes (which module,
  which files, how to validate). If an item is really a project task, put it in
  the planning workbook instead.

## How to add an entry

1. Copy the **Entry template** below to the bottom of the **Entries** section.
2. Give it the next `FW-NNNN` id and fill in every field you can.
3. Add a row to the **Index** table.
4. Keep it proportional - a small idea is a few lines; a big one gets the full
   template.

**Status values:** `Proposed` (captured, not evaluated), `Deferred` (evaluated,
intentionally postponed), `Blocked` (waiting on something), `Done` (implemented
- link the ADR and consider removing).

## Entry template

```
### FW-NNNN: <short title>

- **Status:** Proposed | Deferred | Blocked | Done
- **Date added:** YYYY-MM-DD
- **Author / Agent:** <name, or "Agent (Cursor) on behalf of <human>">
- **Area / modules:** <files or subsystems this would touch>
- **Related ADRs:** <ADR links, or "—">

**Motivation / context.** Why this matters (scientific rationale, references,
the observation that prompted it).

**Sketch of approach.** Enough detail to reconstruct the idea.

**Evaluation / acceptance.** How we would validate it (tests, bench checks,
criteria).

**Effort / risk.** Rough size and the main risks or tensions.

**Open questions.** What still needs deciding.

**ADR needed?** Yes/No - and why.
```

## Index

| ID | Title | Area | Status |
|----|-------|------|--------|
| [FW-0001](#fw-0001-6040-sameswitch-transition-bias-in-sequence-generation) | ~60/40 same/switch transition bias | Trial-sequence generation | Deferred |
| [FW-0002](#fw-0002-head-vs-body-orientation-heading-from-the-tracker) | Head-vs-body orientation (heading) | Markerless tracker | Deferred |
| [FW-0003](#fw-0003-multi-animal-association-and-irlow-saturation-tracking) | Multi-animal association + IR/low-saturation | Markerless tracker | Proposed |
| [FW-0004](#fw-0004-position-based-task-rule-engine-over-landmarks) | Position-based task-rule engine over landmarks | Tracker + GUI + protocol | Done ([ADR-0023](decisions/0023-position-based-rules-engine.md)) |
| [FW-0005](#fw-0005-in-gui-rat-id-entry-firmware-animal-command--logger-file-roll) | In-GUI rat id entry (firmware `ANIMAL` command + logger file-roll) | Firmware + protocol + logger + GUI | Deferred |

## Entries

### FW-0001: ~60/40 same/switch transition bias in sequence generation

- **Status:** Deferred
- **Date added:** 2026-06-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/cue-guided-task/task_core.h` (`generateSequence`), `firmware/common/rig_config.h` (a new `SAME_SIDE_FRACTION` knob), `firmware/cue-guided-task/test/test_task_core.cpp`
- **Related ADRs:** [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md) (added the anti-bias run-length cap; this is the deferred half of the same anti-bias goal)

**Motivation / context.** Rats have a strong innate **spontaneous-alternation**
tendency (natural foraging: don't return to a just-depleted source), so they
switch sides by default. A balanced-random sequence has ~50% same-side / ~50%
switch *transitions* between consecutive trials, which lets a pure-alternation
strategy score above chance without learning the cue->side association. Holleman
et al. 2019 (*Sci Rep* 9:12589) found the alternation drive must be *actively
discouraged* by making same-side transitions more common than switches, and that
a ratio of ~**60% same / 40% switch** was most conducive to learning. The
firmware already implements the companion rule from that paper - no more than 3
consecutive same-side trials (`MAX_SAME_RUN`, ADR-0009) - but not the transition
bias itself.

**Sketch of approach.** The current generator enforces *exact per-block L/R
balance* (counterbalancing), which naturally pulls transitions toward ~50/50, so
the 60/40 target can't just be layered on top - it needs a different generator:
a **transition-sampling** scheme. Start on a random side; for each subsequent
trial pick "same" with p approx `SAME_SIDE_FRACTION` (default ~0.6) else
"switch"; force a switch whenever a same-side run would exceed `MAX_SAME_RUN`;
and nudge toward overall L/R balance within a tolerance (relax the strict
per-block halves to an approximate global balance). Keep it deterministic from
the threaded PRNG seed and host-testable, as `generateSequence` is today.

**Evaluation / acceptance.** Add unit tests that, over a long generated
sequence, assert: (a) realized same-side transition fraction within tolerance of
the target; (b) overall L/R balance within tolerance; (c) `maxRunOk` still holds;
(d) **strategy simulations** - win-stay, win-shift, spontaneous-alternation, and
constant-side strategies all score <= ~60% on the generated sequence (the
paper's bar that no simple heuristic beats using the cue).

**Effort / risk.** Medium. Main tension is balance-vs-ratio: exact per-block
balance and a 60/40 transition ratio are mutually constraining, so this likely
relaxes block balance to a global tolerance - a behavioral-design change worth an
ADR. Low hardware risk (sequence generation only).

**Open questions.** Is approximate global balance acceptable instead of strict
per-block halves? Should `SAME_SIDE_FRACTION` be per-stage once more training
stages exist? What tolerances make the strategy-simulation tests stable yet
meaningful?

**ADR needed?** Yes - it changes how trial sequences are generated and relaxes
the per-block balance guarantee from ADR-era behavior.

### FW-0002: Head-vs-body orientation (heading) from the tracker

- **Status:** Deferred
- **Date added:** 2026-06-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker.py` (`Detection`/`RatModel` already compute the
  body axis via 2nd-moment elongation; add a heading output + disambiguation),
  `host/blob_tracker_prototype.py` (overlay + CSV), later the GUI overlay/sidecar
- **Related ADRs:** ADR-0014 (markerless tracking, planned in Phase 4)

**Motivation / context.** The cue-guided task is about reward-directed *choices*
and the grant studies navigation/reactivation, so the animal's **head direction**
is a valuable behavioral (and neural-correlate) variable. The blob's major axis
is already available essentially for free from the second central moments used for
the shape model. Note the *left/right choice* itself is better read from
**position/ROI** (which arm the centroid enters) than from heading, so this is an
enrichment, not a prerequisite for scoring choices.

**Sketch of approach.** Emit `body_axis_deg` (0-180, from the moment
eigenvector). Resolve the 180 deg head/tail ambiguity using the **Kalman
velocity** (assume forward motion: pick the axis end aligned with travel),
emitting `heading_deg` (0-360) plus a `heading_valid` flag that is false when the
blob is near-round (eccentricity below a threshold -> axis ill-defined) or the
speed is below a threshold (direction unreliable). Smooth heading **circularly**
(EMA on sin/cos, after resolving body-axis flips), separate from the position
Kalman. Optionally add a morphology tie-break (head end bulkier than the tapering
tail) once real video is available. Heading is an OUTPUT only - never a model
feature (it is orientation-dependent by construction). Document that it is in
image space (post rot180 + undistort), not maze/world coordinates.

**Evaluation / acceptance.** Validate against **recorded real-rat video** (the
trigger for deferring this): check `body_axis_deg` is rotation-stable on straight
runs; that velocity-based disambiguation matches the visible nose direction on
runs; that `heading_valid` correctly goes false during rearing/grooming and when
stationary; and that circular smoothing has no wrap artifacts. Add the fields to
the prototype CSV for offline scoring.

**Effort / risk.** Low-medium. Body axis is nearly free; the real work and risk
are head/tail disambiguation and validity gating, which is exactly why it waits on
real video. Low hardware risk (output only).

**Open questions.** Eccentricity/speed thresholds for `heading_valid`? Is
velocity-only disambiguation enough, or is a morphology tie-break needed for slow
turns? Should heading later be mapped from image space into maze coordinates?

**ADR needed?** Fold into ADR-0014 (tracking architecture) when implemented, or a
small follow-up ADR if the disambiguation strategy is non-obvious.

### FW-0003: Multi-animal association and IR/low-saturation tracking

- **Status:** Proposed
- **Date added:** 2026-06-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker.py` (`_associate`, `RatModel`, state machine),
  `host/tracker_view.py` (per-animal controls), recording sidecar columns
- **Related ADRs:** [ADR-0014](decisions/0014-markerless-position-tracking.md)
  (single-rat design these defer), [ADR-0015](decisions/0015-undistort-at-source.md)

**Motivation / context.** ADR-0014 deliberately tracks a **single** rat and leans
on a **colour** histogram as a strong cue. Two foreseeable needs break those
assumptions: (a) tracking **more than one animal** at once (e.g. social/again-two-
in-rig scenarios), which the current "pick one best blob" association can't do;
and (b) **IR or low-light** imaging, where saturation collapses and the colour
histogram becomes nearly useless, so association must lean on geometry/size/shape.

**Sketch of approach.** *Multi-animal:* keep one `RatModel` per identity and run a
proper assignment per frame (Hungarian / linear-sum assignment on a cost matrix of
gated detections vs. tracks, cost = blend of distance + appearance + form), with
per-track Kalman states and birth/death (ACQUIRING/LOST) logic; emit per-identity
sidecar columns. *IR/low-saturation:* detect the low-saturation regime (or a
manual toggle) and **re-weight** the score toward `w_dist`/`w_form` and away from
`w_hist` (optionally swap the H-S histogram for an intensity/texture descriptor);
the FORM Gaussian (size+shape) already works in greyscale.

**Evaluation / acceptance.** Multi-animal: on clips with two animals, identities
stay stable across crossings within tolerance (few ID swaps), validated against
hand-labeled frames. IR: with saturation artificially reduced, tracking + intruder
rejection still hold using geometry/size/shape; confirm the re-weighting kicks in.

**Effort / risk.** Medium-high (multi-animal association + ID management is the
bulk). IR re-weighting is small. Main risk is ID swaps during occlusion/crossings
- the classic multi-object-tracking failure mode.

**Open questions.** Is multi-animal actually in scope for this task, or only
single-rat + occasional experimenter hand (already handled)? Auto-detect the IR
regime from saturation stats, or expose a manual "low-saturation" mode? Per-
identity vs. shared models?

**ADR needed?** Yes - multi-object association and the appearance-weighting change
are non-obvious enough to warrant their own ADR (or an extension of ADR-0014).

### FW-0004: Position-based task-rule engine over landmarks

- **Status:** Done - implemented in [ADR-0023](decisions/0023-position-based-rules-engine.md)
  (`host/rules_engine.py` pure core + `host/rules_view.py` "Rules" tab,
  `host/rules.json`, `host/rig_scale.json`, `host/test_rules_engine.py`).
- **Date added:** 2026-06-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker.py` (consume `get_landmarks()` + the tracked
  centroid), a new rule module in `host/` (or `tracker_view.py` controls), the
  recording sidecar, possibly `firmware/PROTOCOL.md` (host -> firmware triggers)
- **Related ADRs:** [ADR-0018](decisions/0018-landmark-annotations.md) (landmark
  annotations this consumes), [ADR-0014](decisions/0014-markerless-position-tracking.md)
  (tracker), [ADR-0007](decisions/0007-reward-at-central-port.md) (reward path)

**Motivation / context.** ADR-0018 adds the *marking* of reward wells and
body-sensor beams as image-space points; the point of marking them is to drive
**task rules from the animal's position** - e.g. "rat is within R px of the left
reward well", "rat entered the choice arm", dwell-time at a port, or arming/
gating a reward based on trajectory. The fixed landmarks carry an `index` tying
each to its firmware `L,i`/`S,i` event, so position-derived rules can be
cross-checked against (or substitute for) the hardware beams/licks.

**Sketch of approach.** A small, pure rule layer that takes the latest
`TrackResult` centroid (full-frame px) + `get_landmarks()` (normalized -> px via
frame size) and evaluates predicates: distance-to-landmark thresholds (the
per-rule proximity radius lives here, *not* in the marker - see ADR-0018),
enter/exit zone transitions with debounce/dwell, and simple sequencing (e.g.
center -> choice arm -> reward). Emit rule events into the recording sidecar
and/or the standalone track log; optionally let a rule fire a host->firmware
command (e.g. an `R<port>` reward) with explicit safety gating. Keep evaluation
UI-free and testable; expose enable/threshold controls in the Tracking tab.

**Evaluation / acceptance.** Headless tests over synthetic trajectories: zone
enter/exit fires once with debounce; distance thresholds match expectation under
scaling; sequence rules only trip in order. On the rig, confirm position-derived
"at well" agrees with the corresponding lick/beam events within tolerance.

**Effort / risk.** Medium. Main risks: tuning thresholds/debounce to the tracker's
noise, and (if rules trigger firmware) reward-safety gating - any auto-reward path
needs the same care as ADR-0007/0010.

**Open questions.** Do rules ever *actuate* (fire reward) or only *log*? Per-rule
radius vs a shared default? Should rule events go in the video sidecar, the track
log, or a dedicated events file? How do position rules coexist with the firmware's
own beam/lick-driven task logic (authority/override)?

**ADR needed?** Yes - rule semantics (and especially any position-triggered
actuation) are a behavioral-design decision worth recording.

### FW-0005: In-GUI rat id entry (firmware `ANIMAL` command + logger file-roll)

- **Status:** Deferred
- **Date added:** 2026-07-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/cue-guided-task/cue-guided-task.ino` (new `ANIMAL,<id>` command + re-emit), `firmware/common/rig_config.h` + `firmware/PROTOCOL.md` (`PROTO_VERSION` bump), `host/rig_logger.py` (roll file on a new `ANIMAL` line), `host/experiment_control.py` (rat-id field), a new ADR
- **Related ADRs:** [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md) (the `ANIMAL` handshake + session/mode logging this extends)

**Motivation / context.** The log filename comes from the firmware's
`0,ANIMAL,<id>` handshake, but `ANIMAL_ID` is hard-coded (`RAT001`), so every
rat's session lands in `data/RAT001_<time>.csv` unless overridden. The interim
fix (2026-07-01) is a `start_rig.bat` prompt that passes `--animal` to
`rig_logger.py` - but that is set once at launch and can't be changed from the
running GUI. The goal is to type (or change) the rat id directly in
`experiment_control` and have new sessions attribute correctly.

**Sketch of approach.** Add an `ANIMAL,<id>` command the firmware accepts
(alongside `MODE`/`PING`/...): store the id and emit a fresh `0,ANIMAL,<id>`
line. The broker fans that out, so `rig_logger.py` (which today opens one file
per process and ignores later `ANIMAL` lines) rolls to a new
`data/<id>_<time>.csv` on each new `ANIMAL` line. `experiment_control` gets a
small rat-id entry + Apply button that sends the command. Bump `PROTO_VERSION`
(v5) and document the command in `PROTOCOL.md`. Note the broker constraint
([rig_broker.py](../host/rig_broker.py)) that forces this through the firmware:
it forwards a client's line to the Due but does **not** echo it to the other
clients, so a GUI-typed id can't reach the logger except via the firmware
re-emitting the `ANIMAL` line.

**Evaluation / acceptance.** Set an id in the GUI -> the logger opens
`data/<id>_<time>.csv` and subsequent lines go there; changing the id rolls to a
new file without losing buffered lines; blank/invalid ids are rejected/sanitized
(reuse `rig_logger._sanitize`). Confirm a reflashed Due reports the command in
its handshake/version.

**Effort / risk.** Small-medium. Needs a Due reflash (protocol change). Main
risks: not losing lines across a file roll, and id sanitization.

**Open questions.** Allow changing the id mid-session (roll) or only when idle?
Persist the last id across reboots? Should the GUI also reflect the current id
from the `ANIMAL` line?

**ADR needed?** Yes - it changes the serial protocol and the logger's
file-naming behavior.
