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

### Idempotent rebuild
A regeneration step you can run any number of times and always get the same
result — because it *re-derives everything from scratch* rather than adding to
what's already there. The session registry (`build_session_registry.py`) re-scans
every bundle each run, so re-running it after another session (or twice by
mistake) never double-counts. Contrast an "append" step, whose output depends on
how many times it ran. See ADR-0081.

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

### Poll / poll loop
Repeatedly checking or doing something on a timer, rather than reacting to an
event. The GUI runs a "Tk poll" roughly every 100 ms to refresh status and hand
the actuation thread a fresh [snapshot](#snapshot). Polling is simple but adds up
to a poll-interval of delay, which is why the fast/hot path avoids it.

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
