# Glossary

Plain-language definitions of technical terms that recur in this project's code,
comments, ADRs, and chat. The humans who own this code are not all trained in
computer science, so this is the canonical place to look a term up — and the
canonical place to *point to* instead of re-explaining a term in every document
(see the "Explain for a non-specialist audience" rule in
[`AGENTS.md`](../AGENTS.md)).

**Maintaining it:** keep entries short (a sentence or two, in plain language) and
add a term here when it starts recurring across the codebase rather than
re-defining it each time. Link the relevant ADR when one exists. Terms are listed
alphabetically.

---

### Actuation
Making the physical rig *do* something in response to a decision — firing a
reward valve, sounding a tone, sending a firmware command. In this code the
"actuation path" is the whole chain from *rat position/lick* → *decision* →
*hardware command*, and shortening its delay (latency) is a recurring goal.

### Behavioural mode (mode decomposition)
A named *style* of behaving that an animal falls into for a stretch of trials —
e.g. *disengaged*, *side-bias* (keeps picking one arm), *alternating* (keeps
switching arms), *lose-shift* / *win-stay* (reacts to the last outcome), or
*cue-following* (uses the tone). The longitudinal analysis assigns each rolling
window one dominant mode by simple, explicit rules (no model fitting), so the
label always traces back to one checkable number. This makes the ebb and flow of
strategies visible where a single accuracy number would hide it. See ADR-0112.

### Bleed (LED crosstalk)
Light from one board LED that shows up in the measurement of a neighbouring LED,
so the neighbour looks as if it changed when it did not. Written as a fraction of
the neighbour's own change: "L1 bleeds 37 % into `L0_b`" means that when L1
lights, the brightness measured at `L0_b` moves by 37 % of what it moves when L0
itself lights. It can also be negative, when the flash lands in the background
ring subtracted around the neighbour. Measured once per map build and stored in
`led_map.json` (ADR-0196).

### Bounded queue
A [queue](#queue) with a fixed maximum size. When it is full, new items are
**dropped** (and counted) instead of the queue growing without limit or the
producer having to wait. Used so a burst of camera frames can never stall the
tracking thread or exhaust memory. See ADR-0043.

### Broker
The small central process every tool connects to over the network. The firmware
talks to the broker; the GUIs are [TCP clients](#tcp-client--tcp) of it. It
fans one serial stream out to many readers so several programs can watch the rig
at once. See [`README.md`](../README.md).

### Callback
A function you hand to another piece of code to be called *later*, when something
happens — e.g. "call this function every time a new camera frame is ready." The
[fast view](#fast-view-vs-debounced-view) delivers frames by calling a callback.

### Change log (records)
An **append-only** file that records each edit to a data file as its own line
- who (operator), when, which file and row, which field, old value, new value -
and is never rewritten. It complements git: git keeps whole-file snapshots at the
moments someone commits, while the change log keeps per-cell attribution for
every save in between. The coordinated records keep one as
`records/animal/records_changes.csv` in the resources repo (ADR-0155). It is data
*about* the records, not a source of truth: if the log and a records file
disagree, the records file wins.

### Clock fit / drift
No two clocks tick at exactly the same speed, so the camera's clock, the
Arduino's `millis()`, the PC's wall clock and a neural recorder's sample counter
all slowly slide apart — that slide is **drift**. A **clock fit** is the straight
line `time_B = offset + rate * time_A` estimated from events seen in both streams
([fiducials](#fiducial)); the residuals (how far each event sits from the line)
tell you the alignment's precision. See
[`docs/design/cross-stream-sync-design.md`](design/cross-stream-sync-design.md).

### Constellation registration
Finding where a known, fixed pattern of points (the "constellation" — here the
indicator LEDs on the rig's flat electronics board) has landed in an image, by
fitting one rotation + shift (+ scale) to all of them at once rather than trusting
stored pixel positions. Lets the LED detector survive the board or camera being
nudged between sessions, and refuse loudly when it can't find the pattern. The
board is now registered on its persistent red/green [fiducial](#fiducial) LEDs,
with the LEDs' **colour** as a matching constraint (same-colour points only) so a
row of otherwise-identical LEDs can't line up shifted by one. See
[`docs/design/cross-stream-sync-design.md`](design/cross-stream-sync-design.md) §3.4,
ADR-0153.

### Convex hull
The smallest convex outline that encloses a set of points — imagine a rubber band
snapped around them. The board region-of-interest (ROI) is the convex hull of the
persistent red/green [fiducial](#fiducial) LEDs, grown outward by a margin; the
blinking channel LEDs are searched only inside it, so a hand or the rat moving
elsewhere on the maze can't flood the detector. See ADR-0153.

### Core (pure core) / shell (thin shell)
The project's main code-organization pattern. A **pure core** is logic written in
plain Python with no GUI, camera, or hardware — so it can be tested headlessly
(e.g. `rules_engine.py`, `maze_state.py`, `task_events.py`). A **thin shell** is
the thin layer around a core that wires it to the screen, camera, or serial port
(the `*_view.py` files). Keep logic in cores, not shells. See
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

### Credential
A fact about *where the animal is in the current trip* that something must have
earned before it is allowed to happen — "this trip left the hub up the stem"
(`left_hub_via_stem`), or "this trip has run out via the stem" (`outbound_used_stem`).
Distinct from a [gate](#gate), which is about the rig being ready; a credential is
about the animal's route. They all come from one place, the task-event layer
(`host/task_events.py`), so the two tones and the reward cannot disagree about
what a trip is. See ADR-0026, ADR-0061, ADR-0067.

### DAC
Digital-to-Analog Converter — the hardware that turns a digital number into an
analog voltage. Here it drives audio-tone output. "Host owns the DAC" means the
PC, not the firmware's own logic, is deciding what tone plays (the
[handshake](#handshake) that arranges this is the `CUE,VISION` / `CUE,BEAM`
exchange). See ADR-0044.

### Daemon thread
A background [thread](#thread) that does not keep the program alive on its own —
when the main program exits, daemon threads are abandoned rather than blocking
shutdown. The actuation thread is a daemon so closing a tab can't hang the app.

### Debounce
Suppressing rapid flicker by requiring a change to persist before you accept it —
e.g. the rat must be seen in a new zone for several frames before the display
commits to it, so a single noisy position estimate doesn't make the readout
jump. Contrast the [fast view](#fast-view-vs-debounced-view). See ADR-0042.

### Edge (rising / falling edge)
The *moment a state changes*, as opposed to the state itself. A "rising edge" is
the transition from off→on (e.g. a lick beam just became blocked; the gate just
opened); a "falling edge" is on→off. Much of the logic reacts to edges, not
levels, so an action fires once per event instead of every frame.

### Endogeneity
When a "predictor" you want to use to explain behaviour is itself partly *caused by*
that behaviour, so a correlation between them can't be read as one driving the
other. Example: the return cue an animal hears (`return_cue_played`) depends on
which arm it chose to come back through, so "does the return cue affect the next
choice" is entangled with the animal's own habits. The return-cue analysis controls
for prior choice/reward but stays *observational* — only a randomised return-cue
schedule could make it causal. See ADR-0112.

### Event-triggered image (composite)
An average picture lined up on many repeats of one event: for every beam break
(say), take the video frames during the break minus the frames just before it,
and average over all the breaks. Anything that did not change at those moments
cancels out, so what is left is the LED that responds to that event. The
LED-map builder uses one per serial channel to find each channel's LED(s),
including ones that are almost always lit or only flash briefly (ADR-0196).

### Excursion event
One *outbound trip* the rat makes away from the hub, treated as a single logged
event. Most trips go up the central stem and become a scored trial, but the rat
also darts up a side arm without using the stem — a "non-stem" (denied) excursion
that never becomes a trial yet still carries behaviour (impulsive shortcut pulls,
a warming-up or tiring animal). The Phase 3 **excursion log** records every trip,
stem and non-stem, as its own timestamped row so both kinds are visible over
time, not just tallied at the end. See ADR-0109.

### Fast view vs. debounced view
Two readings of the rat's position from the same camera stream. The **debounced
view** is smoothed for a stable display and analysis ([debounce](#debounce)). The
**fast view** reacts in ~1 frame, trading stability for speed, and is what the
reward/tone decisions use so they aren't delayed. See ADR-0042.

### Fiducial
A landmark that shows up in more than one data stream at the same instant, used
to line the streams up — like a clapperboard in film. On this rig: an LED flash
that is both in the video and the same electrical edge the Arduino timestamps;
later, a [sync barcode](#sync-barcode) seen by the Arduino, the camera and the
neural recorder. A second, *spatial* sense is now also in use: the board's
**persistent red/green LEDs** are visual fiducials — always-on landmarks whose
[convex hull](#convex-hull) defines the board ROI and whose pattern gives the
board's pose (position/orientation) each session. The session manifest's
`fiducial.method` names which alignment a session used (`host-t0` today; `led-fit`
once the sync fit is registered). See ADR-0048, ADR-0124, ADR-0153.

### Firmware
The program running *on the microcontroller* (the Arduino/Due in the rig), as
opposed to the "host" software running on the PC. It reads sensors and drives
valves/tones in real time. See [`firmware/README.md`](../firmware/README.md).

### FSM (finite state machine)
A way of describing behavior as a set of named **states** with defined
transitions between them (e.g. the tracker's `ACQUIRING → TRACKING → COASTING →
LOST`). At any moment the system is in exactly one state, which determines what it
does next.

### Gate
A condition that must be true before something is allowed to happen. The
"actuation gate" folds together several safety checks — armed, firmware in the
right mode, tracking running, calibration present — into one `gate_open`
true/false. Nothing fires while the gate is closed. See ADR-0043.

### Handshake
A short exchange of messages by which two parties agree to switch modes or hand
over control — here, the host and firmware swapping who owns the [DAC](#dac) at
the start/end of a task (`CUE,VISION` … `CUE,BEAM`). See ADR-0044.

### Hot path
The code that runs *most often* — here, the work done on **every camera frame**,
tens of times per second. Anything added to the hot path (even a tiny delay)
multiplies by the frame rate, so it is kept as lean as possible.

### Hysteresis (thresholding)
Using *two* thresholds instead of one to decide on/off: a signal must climb above
the higher one to count as ON and fall below the lower one to count as OFF, so a
value hovering near a single threshold cannot flicker between states. The sync
core turns an LED's per-frame brightness into lit/dark spans this way (levels at
40 % and 60 % of the trace's min-to-max range); the maze-state estimator's
min-dwell is the same idea applied to time. See `host/tmaze/core/sync/spans.py`.

### Idempotent rebuild
A regeneration step you can run any number of times and always get the same
result — because it *re-derives everything from scratch* rather than adding to
what's already there. The session registry (`build_session_registry.py`) re-scans
every bundle each run, so re-running it after another session (or twice by
mistake) never double-counts. Contrast an "append" step, whose output depends on
how many times it ran. See ADR-0081.

### Inter-trial interval (ITI)
The gap between one trial ending and the next beginning — here the *dead time*
from the rat closing a trial's loop back at the hub (`trial_end`) to it next
setting off up the stem to start the next one (`trial_start`). A rising rolling
median ITI is the main sign a rat is **disengaging** (losing motivation): it
stops initiating trials. The live "Metrics" trace panel plots it with a
configurable threshold line (default 45 s, matching the offline mode labeler).
See ADR-0114 and [rolling window](#rolling-window-trace).

### Intervention tag
A short, reusable label the operator applies to a [stamped note
entry](#stamped-note-entry) to mark a recurring human action during a session —
e.g. "non-stem dissuasion" or "lift-backs". The set of tags is *user-defined*
(you create/rename/delete them) and persists across sessions in
`host/note_tags.json`, so the same interventions are recorded consistently and
can be compared across sessions later. A tag can be a *counter* (each press
records a `+1`). See ADR-0074.

### Latency
The delay between a cause and its effect — e.g. between the rat entering a zone
and the reward firing. A lot of the design exists to reduce actuation latency.

### Liveness probe
A message sent *just to check the other side is still alive*, when silence alone
would be ambiguous. The [broker](#broker) sends the firmware a `STATUS` after ten
seconds of quiet: a healthy board answers, so no answer means it has stopped
running. Needed because silence is normal here — a resting rat breaks no beams,
so the rig can legitimately say nothing for minutes. See ADR-0062 and
[watchdog](#watchdog).

### Lock
A concurrency tool that lets only **one [thread](#thread) at a time** into a
section of code that touches shared data. Other threads must wait. Locks make
shared data safe but cost time, so this code avoids putting a lock on the
[hot path](#hot-path) by routing all work through a single thread instead. See
ADR-0043 and [race condition](#race-condition--torn-state).

### Logistic regression (choice model)
A standard statistical fit for a yes/no outcome: it weighs several inputs and
returns how strongly each pushes the outcome one way. Here the outcome is
"chose LEFT vs RIGHT" and the inputs are the current cue and the recent history
(the previous choice, whether it was rewarded, the previous return cue). The
fitted *weight* on the cue, tracked across sessions, is a direct read of "is the
animal starting to use the tone?" — the cue weight climbing above the
history weights is the emergence signature a single accuracy number hides. See
ADR-0112.

### Noise floor (video)
How much a pixel's brightness wanders when *nothing* is happening — here mostly
video-compression noise, ~25 grey levels (of 255) in the "brightest ever minus
typical" image of the board at the rig's H.264 setting. Anything blinking must
stand clearly above it (the LEDs reach 80–190), so the LED detector reports the
floor with every run: if a future recording setting lifts it toward the blob
threshold, that number is the warning. See `tmaze/vision/led_detector.py`.

### One-active group (LED sync)
A set of serial channels of which at most one can be active at a time: one rat
cannot break two beams, or lick two ports, at the same instant, and the firmware
never opens two reward valves at once. Listed under `exclusive_groups` in
`host/led_board_layout.json` (`beams`, `licks`, `rewards`). On every frame the LED
sync keeps only the group's strongest channel active and returns the others' LEDs
to rest, so [bleed](#bleed-led-crosstalk) from the active channel is not read as a
second event. Remove a group when the premise stops holding (e.g. two animals).
See ADR-0201, ADR-0203.

### Persistence image (video)
The mirror of the [noise-floor](#noise-floor-video) idea used to find the *steady*
LEDs. For each pixel, take a **low percentile** (e.g. the 10th) over sampled
frames of its colour-ness — how red (`R−max(G,B)`) or green (`G−max(R,B)`) it is.
A [fiducial](#fiducial) LED that is on in nearly every frame scores high even at
that low percentile; a dot lit only briefly (a hand, the rat) is dark most of the
time and scores low; a neutral/white [retroreflective marker](#retroreflective-marker)
has `R≈G≈B` and scores ≈0. This is how the persistent red/green fiducials are
detected — invisible to the blink detector, which sees only *changes*. See
ADR-0153.

### Poll / poll loop
Repeatedly checking or doing something on a timer, rather than reacting to an
event. The GUI runs a "Tk poll" roughly every 100 ms to refresh status and hand
the actuation thread a fresh [snapshot](#snapshot). Polling is simple but adds up
to a poll-interval of delay, which is why the fast/hot path avoids it.

### Precision / recall (LED binding)
Two fractions that describe how well a blinking LED lines up with a serial
channel. *Precision*: of the LED's blinks, how many coincide with an event of
that channel — this answers "which wire is the LED on?" and is the sync core's
binding *score*. *Recall*: of the channel's visible events, how many the LED
was seen blinking for — this measures how much the camera catches (it misses
most licks, since they are faster than a frame or two) and is reported as
coverage, not folded into the score. See `host/tmaze/core/sync/matching.py`.

### Probe block
A short run of **unrewarded** trials dropped into an otherwise normal Phase 3
session: the cue plays and the choice is scored as usual, but no reward is
delivered. Comparing accuracy under a probe (what the animal *knows* — its latent
cue→action map) against its reinforced accuracy (what it *expresses* when paid)
separates knowledge from reward-driven engagement — the acquisition/expression
dissociation of Drieu et al. 2025. Like an [intervention block](#intervention-tag)
it is *inserted on top of* the counterbalanced schedule (it doesn't consume the
seed-deterministic left/right spine) and it is internally 5/5 balanced, but it is
orthogonal to the debias/alternation corrections and takes priority over them.
A probe fires **automatically** on a *correct choice*: the config gives one or more
trial-number *windows* (e.g. trials 40–60), and the first correct choice on a
normal trial inside an un-fired window has its reward withheld (the **trigger**
trial, marked `probe_trigger`) and starts an unrewarded block of `block_len`
(default 10) trials — so the unrewarded run is `block_len + 1` (11) trials. That
block is internally 5/5 counterbalanced and honours the session's anti-bias run
caps (`max_same_run`/`max_alt_run`), so it is never more biased than the main
schedule ([ADR-0138](decisions/0138-probe-block-honors-session-run-caps.md)). The
trigger trial is always correct by construction, so it is kept out of the probe
accuracy read-out (but still shown as a probe trial). There is no manual start, but a
single control-bar toggle can turn probes **off** (and back on) mid-session: off stops
any further window from firing and aborts a running block, so the experimenter can call
off probes after a session has started (a Restart re-arms). Each probe is marked by the
`probe` column in the log and excluded from reinforced accuracy everywhere; a
mid-session disable is recorded in the session notes and flagged in the CSV header so
the saved data shows it. See
[ADR-0115](decisions/0115-phase3-unrewarded-probe-blocks.md) (why),
[ADR-0134](decisions/0134-probe-correct-choice-trigger.md) (the correct-choice
trigger), and [ADR-0137](decisions/0137-probe-mid-session-disable.md) (the mid-session
on/off toggle).

### Protocol
The agreed format of the messages between firmware and host — the
`<ms>,<TYPE>,<payload…>` serial lines and what each field means. Parsed in one
place (`protocol.py`). See [`firmware/PROTOCOL.md`](../firmware/PROTOCOL.md).

### Queue
A first-in-first-out hand-off buffer between threads: one thread *puts* items in,
another *takes* them out in order. It is the thread-safe meeting point that lets
producers (camera, serial reader) pass work to the single actuation
[thread](#thread). See also [bounded queue](#bounded-queue).

### Race condition / torn state
A bug that appears only with unlucky *timing*: two [threads](#thread) touch the
same data at once and their steps interleave, leaving it half-updated and
inconsistent ("torn"). It may work thousands of times and corrupt on the one bad
interleaving, which makes it hard to reproduce. Avoided here by never letting two
threads call the same core (see [lock](#lock), [thread](#thread)). See ADR-0043.

### Reconcile (bundle audit)
Check that two records of the *same* events agree. One session writes each
reward three times: as a trial-CSV row, as a `REWARD` line in the serial log,
and as a count in the summary. If the counts differ, something was lost or
duplicated. The bundle audit reconciles rewards, cues, licks and trial counts
this way. See ADR-0204.

### Reference (ghost) trace
A faded, dashed line on the Metrics tab showing a *past* session of the same rat
drawn behind the live traces, so today's run can be compared against an earlier
one (is the [inter-trial interval](#inter-trial-interval-iti) climbing sooner than
last time? did accuracy settle lower after a config change?). The old session's
recorded CSV is read back into the same [snapshot](#snapshot) shape and run
through the *same* trace computation as the live line, so the two are directly
comparable; a caption flags any run-config differences between them. See ADR-0117.

### Retroreflective marker
A small patch of material that bounces light straight back at its source, so it
shows up bright under the rig's lighting. A ring of them has been stuck around the
electronics board's border. They are **not** used by the sync detector (it keys on
the LEDs' colour, and markers are colour-neutral); they are a **physical QA
reference** — the operator eyeballs the debug overlay to confirm the LED-derived
board ROI lands inside the marker corners. A repeatable placement scheme is
deferred (FW-0059). See ADR-0153.

### Robust fit (Theil–Sen)
A way of fitting a straight line that a few wildly wrong points cannot pull off
course: take the slope between every pair of points and use the *median* slope
(then the median intercept), instead of least squares, which lets one outlier
drag the whole line. The sync core's [clock fit](#clock-fit--drift) uses it so a
handful of mis-matched LED/serial pairs do not bias the camera-vs-Arduino line.
See `docs/design/cross-stream-sync-design.md` §3.5.

### Rolling window (trace)
Instead of one number per session, compute a rate over the most recent *N* trials
and slide that window forward trial by trial, giving a *trace* that shows the rate
rising and falling within and across sessions. The longitudinal analysis uses
rolling windows (default 20 completed trials) for every strategy rate, so a mode
that lasts a few dozen trials is visible rather than averaged away. See ADR-0112.

### Snapshot
An immutable (unchangeable) copy of some state, taken at one instant and handed
off whole. The Tk [poll](#poll--poll-loop) publishes a "gate snapshot" that the
actuation [thread](#thread) reads all at once, so the thread never sees a
half-updated mix of GUI values. See ADR-0043.

### Source of truth
The one file that *owns* a piece of data; everything else is derived from it and
must never silently disagree. For the lab records these are the diffable CSV/JSON
files — `animals.json` (identity), `daily_log.csv` (daily husbandry),
`session_annotations.csv` (per-session notes) — while the Excel workbooks are
*generated views / hand-editable fallbacks* reconciled back by an
[upsert](#upsert-keyed-merge). Keeping a single source of truth is what lets the
rig, the builders, and the [Records Manager](#tk--tkinter) GUI all read the same
data without it drifting apart. See ADR-0076, ADR-0085.

### Span (sync)
"Channel X was in state Y from t0 to t1" — the one shape both streams are reduced
to before they are compared: a beam that read `S,2,0` then `S,2,1` gives a span
of `S2=0`; an LED that went dark then lit again gives a *dark* span. Each span
carries the PC wall-clock time at both edges (for finding candidates) and the
stream's own clock (firmware ms, camera ticks) for the [clock fit](#clock-fit--drift).
Matching spans rather than single edges — both ends must agree — is what tells
apart beams crossed within a few hundred ms of each other. See
`host/tmaze/core/sync/spans.py`.

### Similarity transform
The simplest way one flat shape can move in a picture without distorting: a
rotation, a shift, and one uniform scale factor (four numbers). It is what a
fixed camera sees when a flat board is nudged, so it is the transform
[constellation registration](#constellation-registration) fits between the stored
LED positions and this session's blobs; the leftover distance between the two
point sets after the fit (the *residual*) says how well the pattern was found.
See `register` in `tmaze/vision/led_detector.py`.

### Stamped note entry
One operator observation recorded as a *timestamped event* rather than a line in
a free-text blob: it carries when it happened (wall-clock time + the steady
[monotonic](#session-clock) clock the other streams use) and which Phase 3 trial
was in progress, plus the note text and an optional [intervention
tag](#intervention-tag). Entries are saved to `summary/notes_<id>.jsonl` as they
are added/edited/deleted (so they survive a crash) and folded into the session
summary. Storing notes as timed events — not a paragraph — lets them line up with
the behavior/video/neural data on the one shared timeline. See ADR-0074 (timeline
rationale: ADR-0048).

### Sync barcode
A pseudorandom on/off pattern on one digital line that encodes a sequence number
and repeats every few seconds. Because each repeat is unique, a decoder can match
the *same* code in every stream that recorded the line (neural digital input,
camera LED, serial echo) even if some frames were dropped — a self-identifying
[fiducial](#fiducial) that does not depend on the animal doing anything. The
standard Allen / Open Ephys form is 32 bits at 10 ms per bit; a camera needs a
slower variant. See FW-0018.

### TCP client / TCP
TCP is a standard way for programs to talk over a network connection. A "TCP
client" here is a tool (a GUI) that connects to the [broker](#broker) to send
commands and receive the rig's data stream. See [`README.md`](../README.md).

### Thread
An independent line of execution running *at the same time* as others inside one
program. This app has several: a **tracking worker** (finds the rat in each
frame), a **serial/Tk thread** (reads firmware lines and drives the GUI), and a
dedicated **actuation thread** (makes reward/tone decisions). Concurrency between
them is the reason [locks](#lock), [queues](#queue), and
[snapshots](#snapshot) exist. See ADR-0043.

### Session clock
The number at the start of every firmware line (`<ms>,<TYPE>,...`). It counts
milliseconds since the *session* began — `millis() - session.startMs` — which is
not the same as how long the board has been powered: it starts when the firmware
stops waiting for the PC to open the port, and **restarts on a MODE switch**. The
board's true uptime is never sent. Worth remembering when comparing timestamps
across a reconnect or a mode change. See ADR-0064.

### Session cap (soft cap)
The Phase 3 rule that ends the *task* after a fixed number of scored trials or a
fixed number of minutes since Session Start, whichever comes first (120 trials /
45 min by default, in `phase3.json`'s `session_cap`). It is **soft**: the rig
stops cueing and rewarding and shows a `CAP REACHED` banner, but the recording
keeps running until the operator removes the rat and clicks Stop — nothing is
closed automatically. Fixing the number of *opportunities* per session is what
makes the water a rat earns depend on its strategy (an alternating rat is right
half the time, so it earns half of what a cue-following rat does). Checked only
at a **trial boundary**, so a trial already under way finishes normally. See
ADR-0190 and [water target](#water-target).

### Session registry
One table that folds *every* recording into rows so sessions can be compared and
filtered at a glance — one row per session with its husbandry context (weight,
% baseline), behaviour (accuracy, side bias, excursions), the four water figures,
and per-[tag](#intervention-tag) counts. Built by an [idempotent
rebuild](#idempotent-rebuild) (`build_session_registry.py`) that reads each
bundle's already-captured files and writes `session_registry.csv` (+ the
[tidy](#tidy-long-format) `session_registry_tags.csv`) under the recordings root.
It is the analysis entry point; see [docs/records-schema.md](records-schema.md)
for the columns and ADR-0081.

### Tidy (long) format
A table shaped so each row is one observation with its labels, rather than
spreading values across many columns ("wide"). The tag tallies ship in a tidy
`session_registry_tags.csv` (`session_id, tag, count` — one row per tag press
total) because the [tag](#intervention-tag) vocabulary is user-editable, so a
fixed set of wide `tag_*` columns would change shape every time someone adds a
tag. The registry still *also* offers wide `tag_*` columns as a convenience,
derived from the current vocabulary at build time. See ADR-0081.

### Tk / tkinter
Python's built-in GUI toolkit, used for all the on-screen windows and tabs. "The
Tk thread" is the thread that owns the GUI; GUI objects must only be touched from
it, which is why cross-thread hand-offs use [snapshots](#snapshot).

### Trial epoch (free / in-block / post-block)
How the longitudinal analysis tags each trial by its relationship to an
[intervention block](#intervention-tag): **free** = a normal, counterbalanced
trial (the animal's uncoerced behaviour); **in-block** = a trial inside an active
block, where the schedule is forcing one side; **post-block** = a normal trial
shortly after a block ended (to see carryover). Blocks are kept, not thrown away —
baseline strategy is measured on *free* trials, the animal's response on *in-block*
trials, and persistence on *post-block* trials. A [probe block](#probe-block)
trial is tagged as its own **probe** epoch, kept out of all three reinforced sets.
See ADR-0112, ADR-0115, and ADR-0134.

### USB CDC
The USB standard that lets a device pretend to be an old-style serial port, so
`COM3` on the PC is a *virtual* port rather than a wire. The Due has two, and the
difference matters:

- **Native USB** — the SAM3X chip is itself the USB device, servicing its CDC
  endpoints from the firmware's main loop. Data moves at USB speed and the 115200
  baud both sides quote is ignored (nothing is clocked at that rate). A hung loop
  therefore stops the link while the board still looks plugged in. This is the
  port that wedges ~1030 s after the host opens it, which is why we left it.
- **Programming port** (what the rig uses now) — the SAM3X's real UART goes to a
  separate ATmega16U2 chip, which does the USB talking. 115200 is a genuine wire
  rate here, and opening the port resets the board.

See ADR-0062, ADR-0064 and ADR-0065.

### Upsert (keyed merge)
Update-or-insert: fold a batch of edited rows into an existing table *by a key*
(e.g. `(animal_id, date)` for the daily log, `session_id` for annotations),
replacing the matching row or adding a new one, and leaving every other row
untouched. The records tools upsert rather than overwrite so that a save from the
[Records Manager](#tk--tkinter) GUI can't drop a row someone added straight in
Excel (and vice-versa) — the basis of the reversible CSV<->Excel design. The one
thing an upsert can't carry across surfaces is a *deletion*. See ADR-0085.

### Watchdog
Code whose only job is to notice that something has stopped working and raise the
alarm. The [broker](#broker)'s watchdog uses a
[liveness probe](#liveness-probe) to spot a frozen Arduino within ~25 seconds,
where previously a dead rig looked identical to a quiet one and went unnoticed for
17 minutes. It reports; it cannot revive the board (only the RESET button does
that). See ADR-0062.

### Water target (daily water)
Each rat's daily water need in millilitres (`daily_water_ml` in `animals.json`,
e.g. 8 mL for rat 600). It is the yardstick the Stop-time summary measures a
session against: whatever is not earned (`target − earned`) is the
**supplement** given in the home cage later. It can also size the reward: when
the rat has no explicit `reward_ul` in `animals.json`, the rig sets the volume of
one correct-trial reward under the [session cap](#session-cap-soft-cap) so that a
rat at chance over the full cap earns a set fraction of the day's water (half, by
default) and a perfect rat earns exactly all of it:
`uL per reward = daily_water_ml × 1000 × water_at_chance_fraction ÷ (max_trials × 0.5)`.
An explicit `reward_ul` (70 µL for every rat today) overrides that derived
volume so the reward is the same size across rats. Either way the pulse width is
read backwards off the reward calibration curve. See ADR-0191.
