# How the tracking and task-rules pipeline works

A plain-language guide for anyone running the rig who wants to understand what
the software is doing when it "watches the rat" and "gives rewards from the
camera." No programming needed. If you later want the code-level details, each
section points at the file and the design note (ADR) behind it.

## The big picture: a chain of four steps

The rig has an overhead camera looking down at the T-maze. Four pieces of
software sit in a line, each one taking the output of the one before it and
turning it into something more meaningful:

```
 Camera frame
     |
     v
[1] Blob tracker        "Where is the rat, in pixels?"      (tracker.py)
     |   a dot on the floor
     v
[2] Maze-state estimator "Which part of the maze is that?"  (maze_state.py)
     |   e.g. "in the left choice arm, running up the stem"
     v
[3] Task events          "What did that mean for the task?"  (task_events.py)
     |   e.g. "arrived at the well", "closed a clean left loop"
     v
[4] Rules engine         "Should we do anything about it?"   (rules_engine.py)
     |   e.g. "yes -> dispense water at the center spout"
     v
 Reward (only in Free Roam; the executor actually sends it)
```

Think of it as translation from "pixels" up to "task language" and finally to
"decisions." Each step is deliberately simple and only knows about the step
just below it. That is why they are separate files: you can test, tune, or
replace any one of them without disturbing the others.

Two things are true of the whole chain and worth knowing up front:

- **The camera image is already cleaned up.** Before any of this runs, the feed
  is lens-corrected and oriented so the floor looks flat and square from above
  (see `../os_code_t_maze_resources/rig-hardware/camera/` and ADR-0015). That
  is what lets the later steps measure distances and positions honestly.
- **This whole pipeline only *decides*; it does not control the task by
  itself.** During the real behavioral task, the Arduino firmware is in charge
  of rewards. The camera-driven rules only actuate during **Free Roam** (the
  shaping/training mode), and even then only when you have explicitly armed
  them. This is a safety choice (ADR-0023), covered at the end.

You interact with each step through a tab in `experiment_control.py`
("Tracking", "Maze state", "Rules"), or with the standalone tuning tool
`blob_tracker_prototype.py`. The tabs are the knobs; the files above are the
engines.

---

## Step 1: The blob tracker - "where is the rat?"

**File:** `tracker.py` (tuned via the "Tracking" tab or
`blob_tracker_prototype.py`). **Design notes:** ADR-0014, 0017, 0020, 0027.

The tracker's whole job is to look at each camera frame and answer one question:
*where, in the image, is the rat right now?* It does this the classic
computer-vision way - by comparing the current frame to an empty picture of the
maze - rather than with markers on the animal or a neural network.

### How it finds the rat

1. **Take a "background" photo first.** You show it one frame of the maze with
   no rat in it (the "grab background" button). This is the reference.
2. **Subtract.** For every new frame, it looks at what changed compared to the
   empty maze. The rat is the big clump of "different" pixels. Everything that
   didn't move cancels out.
3. **Clean up the difference** into a black-and-white mask (white = "this
   changed"), and smooth away speckle.
4. **Only look inside the maze.** You draw an outline (the "ROI polygon") once,
   and the tracker ignores anything outside it - a hand reaching in from the
   edge, a reflection on the wall, etc.
5. **Find blobs** (connected white clumps) and throw out ones that are too small
   or too big to be a rat.
6. **Report the center** of the winning blob as the rat's position. A smoothing
   filter (a Kalman filter) removes the frame-to-frame jitter so the dot glides
   instead of twitching, and can *coast* briefly in a straight line if the rat
   is momentarily lost (e.g. under a cable).

### Why it doesn't get fooled by the experimenter's hand

Plain background-subtraction would happily track your arm if you reached into
the maze. To prevent that, you can teach the tracker what *this* rat looks like
- an **appearance model** (`RatModel`). During a short registration window you
let the animal move around (run, turn, rear, groom) and the tracker records:

- the rat's **color** (a hue/saturation fingerprint), and
- the rat's **size and shape range** (how big the blob is, how elongated,
  how solid), captured jointly so it understands that a running rat is big and
  stretched while a grooming rat is small and round.

After that, when several blobs appear, each candidate is scored on *how close it
is to where the rat was expected*, *how well its color matches*, and *how well
its size/shape matches*. Color and size are the strong cues that reject a
wrong-looking intruder; shape is used gently. A blob only "wins" if its score is
good enough - otherwise the tracker reports "no confident target" rather than
grabbing the wrong thing.

The model can optionally keep **learning** while it tracks (adding new confident
observations to its color/size/shape range), so it copes with lighting or pose
it didn't see during registration - without ever forgetting the original
registration or drifting onto an intruder.

### Physical blockers (the orange-taped obstacle)

Sometimes you drop a physical blocker into the maze to close off a path. It was
not in the "empty maze" photo, so plain background-subtraction sees it as a new
blob - and because a blocker is dark like the rat, the track can jump to it or
"stick" when the rat gets close. The fix (ADR-0034):

1. **Put orange tape on the blocker's edges.** Orange is a colour the rat never
   has, so it is a reliable "this is the blocker" marker no matter where the
   blocker sits.
2. **Register the blocker from a few still snapshots.** On the "Tracking" tab,
   place the blocker, take your hand out of view, and click **Capture sample**;
   repeat at a few positions/orientations, then **Finish**. The tracker learns
   the tape's actual colour *under your room lighting* (you'll see the learned
   hue range in the status), so there is nothing to hand-tune. Save/Load keeps
   it for next time.
3. **During tracking it is followed in parallel and erased from the rat's view.**
   Each frame the software finds the orange tape, fills in the blocker's outline,
   and subtracts that patch before it looks for the rat. Because the tape is only
   ever on the blocker, this works even when the rat is touching the blocker (the
   rat is not erased), and it follows the blocker if you move it. The blocker is
   drawn as an orange square marked `BLOCKER` on the preview.

Caveat: while your hand is in the frame moving the blocker, tracking can be
briefly noisy - fine between trials, so reposition then.

### What it tells the next step

Every frame the tracker emits a small record (`TrackResult`) with the rat's
position, a smoothed position, its size/shape, a **confidence** number (0-1),
and a **state**:

| State | Meaning |
| --- | --- |
| `NO_REFERENCE` | No background photo grabbed yet - it can't do anything. |
| `ACQUIRING` | Has a background, trying to lock onto the rat. |
| `TRACKING` | Confident, locked-on. This is the good state. |
| `COASTING` | Lost the blob this frame; briefly guessing from momentum. |
| `LOST` | Coasted too long; gave up and will re-acquire. |

The confidence and the `TRACKING`/`COASTING`/`LOST` state matter downstream:
the next steps refuse to draw conclusions when the tracker isn't sure, so a
brief loss of the rat never produces a bogus "the rat went left."

You save the appearance model and the ROI outline to disk so you don't have to
re-teach them every session; the model even remembers the exact tuning it was
built under (ADR-0027).

---

## Step 2: The maze-state estimator - "which part of the maze is that?"

**File:** `maze_state.py` (tuned via the "Maze state" tab). **Design note:**
ADR-0025.

A moving dot in pixels isn't very useful on its own. The maze-state estimator
turns that dot into a **discrete, named location** in the maze and a sense of
**which way the rat is going**.

### The map

Once, using the "Maze state" tab, you draw the maze's regions on a still frame.
The T-maze is really a figure-8 the rat loops around, so the regions are:

- **HUB** - the central area where the reward well sits and where the stem and
  both return arms come together. It has a sub-region, the **well** itself, so
  the software can tell "at the well" from "just in the middle."
- **CENTRAL_STEM** - the runway leading away from the hub.
- **JUNCTION** - where the stem splits.
- **LEFT_CHOICE / RIGHT_CHOICE** - the two choice arms.
- **LEFT_RETURN / RIGHT_RETURN** - the arms that bring the rat back to the hub.

A normal lap is: HUB → stem → junction → a choice arm → its return arm → HUB.

### What it works out each frame

- **Which zone** the dot is inside (a point-in-polygon test). It uses a little
  patience called **min-dwell hysteresis**: the rat has to actually stay in a
  new zone for a few frames before the estimator "commits" to it, so a dot that
  wobbles across a border doesn't cause the state to flicker back and forth.
- **Direction along the stem** - by projecting the dot onto the stem's
  centerline it can tell *up-stem* (heading out toward the choices) from
  *down-stem* (coming back). This is something the physical beam sensors
  **cannot** do on their own (a beam break doesn't know which way you were
  going), which is a big reason this module exists.
- **Completed loops** - when the rat leaves the hub, goes through a choice arm,
  and returns to the hub, that's a closed loop, and it notes which side.
- **A short history** of recent zones, so later steps can ask questions like
  "has the rat been in the left arm since it last left the stem?"

### The honesty rule

If the tracker is `COASTING`/`LOST` or below a confidence threshold, the
estimator reports `UNKNOWN` and **emits no transitions at all**. It quietly
holds its last real position internally, so when tracking recovers it picks up
cleanly. It never invents a "the rat entered the left arm" from a bad frame.

The output each frame is a `RatState`: current zone, well sub-state, any
enter/exit that just happened, direction, whether a loop just closed and on
which side, and a validity flag.

---

## Step 3: Task events - "what did that mean for the task?"

**File:** `task_events.py`. **Design note:** ADR-0026.

Zones and directions are still geometry. The training curriculum thinks in
different words: "the rat arrived at the well," "it entered a choice arm," "it
ran a clean left loop," "that's three left loops in a row." This layer is the
single **translator** into that task vocabulary.

From the stream of `RatState`s it produces, each frame, things like:

- `well_arrived` - the moment the rat first enters the well (an edge, true for
  one frame only).
- `choice_entered` - "left" or "right" the moment it enters a choice arm.
- `outbound_used_stem` - did this lap's outbound leg actually go up the stem
  (versus sneaking out a return arm)?
- `loop_closed` + `loop_clean` - a lap closed at the hub, and whether it was a
  proper unidirectional lap (out via the stem, back via the matching return
  arm) rather than a reverse.
- `same_side_run`, `left_loops`, `right_loops` - running counts, e.g. how many
  same-side loops in a row (useful for de-biasing a rat that keeps turning the
  same way).

### Why this is its own step (important for good science)

The reward the animal earns and the statistics you later report **must** come
from the *same* definition of "a clean left loop." If the reward code and the
analysis code each defined it separately, they could disagree - and you'd never
be sure whether a number meant what you thought. Putting the definitions in one
neutral place removes that risk. Crucially, this layer only **reports** what
happened; it never decides to reward. That decision is the next step's job.

---

## Step 4: The rules engine - "should we do anything about it?"

**File:** `rules_engine.py` (configured via `rules.json`, driven by the "Rules"
tab). **Design notes:** ADR-0023, 0026.

This is the decision-maker. It watches the rat's position, the task events
above, and the rig's lick sensors, and decides when an action - almost always
"dispense a bit of water" - should fire. It's built to be edited by a
non-programmer: you describe rules in a `rules.json` file by filling in numbers,
without touching Python.

### Rules are little templates you fill in

The main building block is a **rule template**. For example
`prime_and_lick_topup` says, in effect:

> When the tracked rat comes within *R* centimeters of *this reward well* (and
> the track is confident), dispense once (a "prime"). Then each time it licks
> that spout while still in the zone, dispense again, up to *N* extra times.
> Before it can earn a prime again, it must **leave** the zone and a **wait
> timer** must elapse.

Everything in italics is a number you set. A second template,
`well_reward_with_excursion`, is used in the reward-shaping phases and only pays
out when the task-event "credential" is satisfied (e.g. the rat actually ran a
proper loop first), which is how the curriculum gradually demands more.

Distances are written in **centimeters**, not pixels. A one-time calibration
("Measure scale" in the Rules tab) tells the engine how many centimeters a pixel
is worth, so your rules stay meaningful even if the camera moves.

### The guard rails against runaway rewards

Because this thing dispenses water automatically, it's wrapped in several
safety limits:

- **A short within-visit cooldown** between top-ups so it doesn't machine-gun.
- **A longer re-arm timer** so a rat camping at the well, or bouncing in and
  out, doesn't keep getting paid.
- **A per-visit cap** on how many top-ups a single visit can earn.
- **A confidence gate** - nothing fires unless the tracker is confidently
  locked on.
- **A global rate backstop** - a last-resort limit that drops any reward coming
  too soon after the previous one, no matter which rule asked for it.

### It decides, it does not act

This is the most important safety point. The rules engine only **returns
intentions** ("I would like to fire reward at port 2"). It never sends anything
to the hardware, never writes files, never touches the screen. A separate, thin
piece of glue - the "Rules" tab (`rules_view.py`) - is the *only* thing that
actually turns an intention into a real water pulse, and it does so **only
when**:

1. you have explicitly **armed** it,
2. the tracker is confidently **tracking**,
3. the firmware is in **Free Roam** (never during the real task, where the
   Arduino firmware is the reward authority), and
4. the active rule's requirements (scale calibrated, zone map present) are met.

Every fire is logged to a CSV so you have a record of exactly what the camera
dispensed and why.

---

## Putting it together: a worked example

Imagine a rat during Free Roam shaping:

1. **Tracker:** finds the rat's blob near the center, reports position with
   confidence 0.9, state `TRACKING`.
2. **Maze-state estimator:** that position is inside the well sub-region of the
   HUB, so it reports `zone = HUB, substate = in_well`, and since it just
   crossed the boundary, an `entered` edge.
3. **Task events:** "the rat was not at the well last frame and is now" →
   `well_arrived = true`. It also notes this lap's outbound leg used the stem
   and that a clean left loop just closed.
4. **Rules engine:** a `well_reward_with_excursion` rule sees that the
   credential (a clean loop happened) is satisfied and the confidence gate
   passes, so it returns `FireReward(center port, 150 ms, reason="prime")`.
5. **Rules tab (executor):** you have it armed, the firmware is in Free Roam, so
   it sends the reward pulse to the Arduino and writes a line to the rule-events
   log. Water is delivered.

If at step 1 the tracker had been `COASTING`, step 2 would have reported
`UNKNOWN`, steps 3-4 would have produced nothing, and no water would have been
dispensed. That is the pipeline protecting you from acting on a bad frame.

---

## The reward-shaping phases (Phase 1, 2a, 2b)

Before the rat is ready for the real cued task (which runs on the firmware and
is sometimes called "Phase 3"), it goes through a **host-side reward-shaping
curriculum** that runs entirely in Free Roam. All three phases use the *same*
rule template, `well_reward_with_excursion`, and differ only by a few settings -
mainly the **credential** (plus when the reward primes and how many top-ups) - so
switching phase is just picking a profile in the Rules tab's Phase dropdown, not
changing any code (see ADR-0026 and ADR-0030).

### The idea shared by every phase

Water at the **central well** is not free - it has to be *earned* by a trip out
into the maze:

1. The rat does something out in the maze that counts (the **credential**).
   That "arms" the rule - it now owes one payout.
2. That triggers a **prime** - a single reward pulse at the central spout - which
   consumes the credit. *When* it primes depends on `prime_on`: in Phase 1 it
   waits until the rat reaches the well; in Phases 2a/2b it fires the instant the
   credential is earned, out at the choice arm, so the solenoid click marks the
   correct choice (just like the full task).
3. Each **lick** at the well tops up a little more, up to `max_topups` times
   (spaced by `topup_cooldown_ms`).
4. To get paid again, the rat must go earn a **fresh** credential. It can't just
   sit and lick at the well ("camping"), and it can't earn credit twice without
   leaving and coming back.

This "earn it, spend it, earn it again" loop is what makes the phases *build on
each other*: each phase simply demands a harder excursion than the last. The
same-side run counter and the loop/choice definitions all come from the shared
`task_events` layer, so what the rat earns and what you later measure use one
definition.

Common knobs (in every phase's rule): `port` (which spout, usually 2 = center),
`pulse_ms` (reward size), `max_topups`, `lick_debounce_ms`, `topup_cooldown_ms`,
`min_confidence`, and `prime_on` (`well_arrival` = prime on reaching the well,
`well_lick` = wait for the first lick there, or `credential` = fire at the choice
arm the moment the credential is earned). All three phases need the maze zone map
(`zones.json`); none of them need a cm/px scale.

### Phase 1 - "the well = reward, so go explore" (`credential: "choice"`)

**Goal:** teach the rat that the central well gives water, and get it moving
around the whole maze instead of camping.

**What earns a payout:** entering **either** choice arm (left *or* right). As
soon as the rat has been out to a choice arm, the rule is armed; the next well
visit pays out. Because the credit is consumed each time, the rat has to make a
new out-and-back trip for every reward - which naturally spreads it around the
maze and prevents camping.

**Optional gentle transition:** a `nonstem_reward_prob` setting lets a *non-stem*
excursion count only some of the time (a stem excursion always counts). Fading
this probability from 1.0 toward 0.0 across sessions smoothly turns Phase 1 into
Phase 2a without an abrupt "nothing works anymore" cliff.

### Phase 2a - "go out the proper way" (`credential: "choice_via_stem"`)

**Goal:** shape *unidirectional* travel - the rat should head out along the
central stem, not sneak into a choice arm backwards through a return arm.

**What earns a payout:** entering a choice arm, but **only if the outbound leg
of this lap actually went up the central stem** first. If the rat reaches a
choice arm without having used the stem, the trip is logged as `credit_denied`
(reason `no_stem`) and no credit is given.

**Where/when the reward fires:** the moment the rat enters the choice arm on a
valid stem-gated choice, the central spout primes once (`prime_on: "credential"`)
- the solenoid click is the correct-choice signal, exactly as in the full task.
The rat then goes to the well, where up to **2** licks top up (`max_topups: 2`).
Each payout still needs a fresh stem-gated excursion.

### Phase 2b - "go out the proper way, smaller reward, and don't always turn the same way" (`credential: "choice_via_stem"` + `debias`)

**Goal:** the same stem-gated choice as Phase 2a, but with (a) a smaller reward so
more trials fit in a session before the rat is satiated, and (b) a nudge not to
over-use one side.

**What earns a payout:** exactly like Phase 2a - a stem-gated choice-arm entry,
primed at the central spout the instant the rat enters the arm - **except** the
reward is a single pulse with **no top-ups** (`max_topups: 0`), and an anti-bias
cap can withhold it (below).

**Anti-bias (`debias`):** to stop a rat from just choosing the same side forever,
you cap how many same-side stem-gated **choices** in a row will pay out:

- `{mode: "fixed", n: 3}` - a hard cap of 3 same-side choices.
- `{mode: "uniform", min: 3, max: 5}` - re-draws a new random cap in that range
  each time the rat switches sides, so the rat can't learn a fixed alternation
  rhythm (this is the default in the shipped `rules.json`).

When the current same-side run is over the cap, the otherwise-valid choice is
logged as `debias_withhold` and nothing fires - nudging the rat to try the other
side to get paid again. The run counter counts *every* stem-gated choice (paid or
withheld); a non-stem entry doesn't count. This is the same anti-bias idea as the
original clean-loop 2b (still available as the `clean_loop` credential, which
credits a full stem-out-and-matching-return lap), moved onto the choice so it
works with the prime-at-choice timing. See ADR-0030.

### Fail-safes across all phases

Nothing fires unless the track is a confident lock (`min_confidence`). If the
tracker briefly loses the rat at the well, the visit is **not** dropped and the
prime is **not** repeated - the rule only treats the rat as having left the well
on a confident "outside" reading, so a momentary track glitch never
double-rewards. And the engine-wide `global_min_interval_ms` backstop still caps
the absolute reward rate no matter what any phase asks for.

---

## Where to go next

- **Run things:** `../PROGRAMS.md` lists every program and its exact launch
  command (the "Tracking", "Maze state", and "Rules" tabs all live in
  `experiment_control.py`).
- **Tune the tracker alone:** `blob_tracker_prototype.py` is a standalone
  window for building an appearance model and drawing the ROI.
- **The "why" behind each design choice:** the numbered notes in
  `decisions/` - especially 0014 (tracking), 0017 (tuning knobs), 0023 (rules
  engine + decide-vs-act split), 0025 (maze state), and 0026 (shaping phases).
- **The scientific goal these serve:** the grant and protocol PDFs in the
  sibling `os_code_t_maze_resources` repo explain the cue→choice→reward task the
  rig is ultimately built to run.

---

## Appendix A: What algorithm does each step use?

This is a slightly more technical summary of the *method* behind each stage -
still no code, but naming the techniques so you know what you're tuning.

### Blob tracker (`tracker.py`)

| Sub-step | Technique | In plain terms |
| --- | --- | --- |
| Foreground detection | **Reference-frame background subtraction** (absolute difference vs a stored empty-rig frame, then a fixed threshold) | "What changed since the empty maze?" |
| Noise cleanup | **Gaussian blur** before thresholding, then **morphological open + close** | Smooth speckle and fill small holes so the rat is one solid blob. |
| Region limiting | **Point-in-polygon ROI mask** | Ignore everything outside the maze outline you drew. |
| Blob extraction | **Contour finding** + area filtering; shape from **image moments** (elongation) and **convex-hull solidity** | Turn white clumps into candidates with a size and a shape. |
| Appearance model | **Pooled Hue-Saturation histogram** for colour + a **3-D Gaussian** over `[log area, log elongation, solidity]` for size/shape | A statistical fingerprint of *this* rat that can be extended without storing raw frames. |
| Choosing the right blob | **Weighted score** = distance-to-prediction + colour-histogram correlation + shape **Mahalanobis** term; a hard size band gates first | Pick the candidate that is close, the right colour, and the right size/shape; reject intruders. |
| Smoothing / gap-filling | **Constant-velocity Kalman filter** | Glide instead of jitter, and coast briefly in a straight line when the rat is lost. |
| Lock management | A small **state machine** (`ACQUIRING → TRACKING → COASTING → LOST`) with a per-frame confidence | Know when to trust the track and when to say "I'm not sure." |
| Online learning (optional) | **Sufficient-statistics accumulation** (not exponential drift) | Grow the fingerprint's range to new poses/lighting without forgetting or chasing an intruder. |
| Blocker handling (optional) | **Learned orange-tape HSV gate** locates the obstacle; its **convex-hull footprint** is subtracted from the mask before the rat is chosen (ADR-0034) | Track an orange-taped blocker in parallel and erase it so it can't steal the rat's track, even on contact. |

### Maze-state estimator (`maze_state.py`)

| Sub-step | Technique | In plain terms |
| --- | --- | --- |
| Which zone | **Ray-casting point-in-polygon** against your drawn zone map (normalized 0-1 coordinates) | "Which labelled region is the dot in?" |
| Border stability | **Min-dwell hysteresis** (a new zone must persist N ticks before it commits) | Don't flip-flop when the dot wobbles on a boundary. |
| Travel direction | **Scalar projection** of the dot onto the stem centerline, with a **deadband** on the change | Tell "running out" from "coming back" - something the beam sensors can't. |
| Loop detection | **Bounded zone-visit history** + a small loop rule (choice arm visited between two hub visits) | Recognize a completed figure-8 lap and which side. |
| Trustworthiness | **Confidence gate + validity flag**; emits `UNKNOWN` and no transitions when the track is weak | Never invent a move from a bad frame. |

### Task events (`task_events.py`)

No heavy algorithm here - it is a **stateful reducer** that folds the per-tick
`RatState` into named edges/predicates/counts (e.g. `well_arrived`,
`loop_clean`, `same_side_run`) using the canonical "visited X since leaving Y"
query. Its value is being the *single definition* both reward and analysis use.

### Rules engine (`rules_engine.py`)

| Concern | Technique | In plain terms |
| --- | --- | --- |
| Units | A **linear cm/px scale** or an optional **3×3 homography** (perspective-correct) | Write rules in centimeters; the engine converts to pixels. |
| Rule structure | A **declarative template registry** (`prime_and_lick_topup`, `well_reward_with_excursion`) evaluated against a per-tick `Frame` | Non-coders fill in numbers in `rules.json`; Python does the rest. |
| Decision logic | Small **per-rule state machines** (OUTSIDE/PRIMED; credit earned/consumed) | Prime once, top-up on licks, re-arm after leaving. |
| Safety | Layered **rate limits** (within-visit cooldown, between-visit refractory, per-visit cap, confidence gate, and a global min-interval backstop) | Multiple independent brakes against runaway rewards. |
| Separation | **Intent objects** returned, never actuated here | The engine decides; the Rules tab is the only actuator, and only in Free Roam when armed. |

---

## Appendix B: Tunable parameters in the GUI

Every knob below is exposed in `experiment_control.py` (or the standalone
`blob_tracker_prototype.py`) and has a hover tooltip in the app. This is the
same information, gathered in one place. Values in **bold** are the code
defaults.

### Tracking tab - detection sliders (`Tuning`)

| Label | Parameter | Range | What it does | Turn it UP if... |
| --- | --- | --- | --- | --- |
| Threshold | `thresh` | 0-255 (**30**) | How different from the empty maze a pixel must be to count as "foreground." | The background is noisy / rat blob is fragmented → lower it; false specks appear → raise it. |
| Min area | `min_area` | 0-8000 px² (**150**) | Smallest blob accepted as the rat. | Small non-rat specks get tracked → raise it; the rat blob is being rejected → lower it. |
| Blur | `blur` | 1-31, odd (**5**) | Smoothing kernel applied before thresholding. | The mask is grainy → raise it (loses fine detail). |
| Gate (px) | `gate_radius` | 0-500 px (**120**) | Biggest frame-to-frame jump allowed before a candidate is rejected. | The rat moves fast and the track drops → raise it; a far intruder steals the track → lower it. |
| Accept | `accept_score` | 0-1 (**0.50**) | Minimum match score (with a model) to accept a blob as the rat. | Intruders get accepted → raise it; the real rat is rejected too often → lower it. |
| Coast (frames) | `coast_max_frames` | 1-200 (**30**) | How long to keep guessing position after losing the blob before declaring `LOST`. | The rat briefly disappears under a cable → raise it; a stale lock lingers → lower it. |

### Tracking tab - appearance-model tolerances (`Model tolerances`, collapsed)

These only matter once you've registered an appearance model. They are read
live, so you can tune them while watching the track.

| Label | Parameter | Range | What it does |
| --- | --- | --- | --- |
| Size tol (k) | `area_gate_k` | 1-6 (**3.0**) | Width of the accepted size band, in standard deviations around the model's size. Bigger = more forgiving of size changes. |
| Appearance bias | `appearance_bias` | 0-1 (**0.55**) | Balance between "looks like the rat" (appearance) and "is where the rat should be" (distance). Higher = trust appearance more. |
| Shape tol | `form_tol` | 0.2-5 (**1.0**) | Softness of the size/shape match. Higher = more tolerant of posture changes. |
| Color gate | `hist_min` | 0-1 (**0.0 = off**) | Hard minimum colour-correlation. Above 0, blobs whose colour is clearly wrong are rejected outright. |

### Tracking tab - other controls

- **Enable tracking** - run the tracker on each frame (off by default).
- **Grab background / Save background...** - capture (or export) the empty-rig
  reference the subtraction needs.
- **Register rat / Cancel / Enrich while tracking** - build the appearance
  model over a window of frames, and optionally keep refining it live.
- **Save/Load/Clear model** - persist the appearance model (`.npz`, per animal),
  including the settings it was tuned under (ADR-0027).
- **Define/Clear/Save/Load region** - the maze ROI polygon (`roi_polygon.json`).
- **Define/Clear landmarks** - mark the reward wells and body-sensor beams on a
  still frame (`landmarks.json`); used by the landmark-based rules.
- **Show mask / track log** - a diagnostic view and an optional standalone track
  CSV.

### Maze state tab

| Label | Parameter | Range | What it does |
| --- | --- | --- | --- |
| Min confidence | `min_confidence` | 0-1 (**0.5**) | Track confidence below which the maze state is reported `UNKNOWN` and no transitions are emitted. |
| Min dwell | `min_dwell_ticks` | 1-30 ticks (**3**) | How many consecutive ticks a new zone must persist before it commits (border-flicker hysteresis). |

Plus **Define zones** (draw the HUB, stem, junction, choice/return arms, the
well sub-region, and the stem axis on a still frame → `zones.json`),
**Save/Load zones**, and an optional **state-timeline CSV**.

### Rules tab and `rules.json`

The Rules tab exposes the **Arm** switch, a **Phase** dropdown (the shaping
profiles below), **Reload**, and the **Measure scale** UI. The rules themselves
live in `host/rules.json`, which you edit directly. Its knobs:

**Engine defaults** (apply to all rules unless a rule overrides them):

| Key | Default | What it does |
| --- | --- | --- |
| `min_confidence` | 0.5 | Only act while the track is `TRACKING` at or above this confidence. |
| `topup_cooldown_ms` | 500 | Minimum time between fires *within* one well visit (prime→top-up, top-up→top-up). Keep short. |
| `rearm_refractory_ms` | 5000 | Anti-camp timer: minimum time before a rule may *prime* again on a fresh visit. |
| `global_min_interval_ms` | 500 | Runaway backstop: minimum time between *any* two fires across all rules. |

**Per-rule fields:**

| Field | What it does |
| --- | --- |
| `id` | Unique short name used in the logs. |
| `template` | `prime_and_lick_topup` (landmark + cm scale) or `well_reward_with_excursion` (zone/loop credential). |
| `enabled` | Disable a rule without deleting it. |
| `port` | Reward port to fire (0 = left, 1 = right, 2 = center). |
| `pulse_ms` | How long the reward valve opens (ms) - i.e. how much water. |
| `max_topups` | Max lick-triggered top-ups per visit, after the prime. |
| `lick_debounce_ms` | Ignore lick edges closer together than this (anti-chatter). |
| `topup_cooldown_ms` / `min_confidence` | Optional per-rule overrides of the defaults. |
| `landmark`, `radius_cm` | *(prime_and_lick_topup)* which annotated point, and the proximity radius in cm. |
| `rearm_refractory_ms`, `rearm_on_exit` | *(prime_and_lick_topup)* the between-visit timer and re-arm-on-exit behaviour. |
| `credential` | *(well_reward_with_excursion)* which excursion earns the next payout: `choice`, `choice_via_stem`, or `clean_loop`. |
| `prime_on` | *(well_reward_with_excursion)* when the prime fires: `well_arrival` (on reaching the well), `well_lick` (first lick there), or `credential` (at the choice arm the moment the credential is earned; Phase 2a/2b). Legacy `prime_on_arrival` (true/false) still accepted. |
| `debias` | *(well_reward_with_excursion, clean_loop or choice_via_stem)* cap on same-side runs to nudge the rat to alternate; e.g. `{mode:"uniform", min:3, max:5}`. |

**Shaping profiles** (the Phase dropdown selects one; `active_profile` sets the
default):

| Profile | Credential | Meaning |
| --- | --- | --- |
| Phase 1 | `choice` | Reward at the well after the rat enters *either* choice arm - "go explore, then drink." |
| Phase 2a | `choice_via_stem` | Reward only if the outbound leg actually went up the central stem first; primed at the choice arm (up to 2 top-ups). |
| Phase 2b | `choice_via_stem` + `debias` | Same stem-gated choice, primed at the choice arm, but zero top-ups + a choice-side anti-bias cap. (The `clean_loop` credential - a full clean lap - remains available.) |

Distances (`radius_cm`) are converted to pixels through the cm/px scale you set
with **Measure scale** (saved to `rig_scale.json`); the zone/loop templates use
the maze map (`zones.json`) instead and need no cm scale.