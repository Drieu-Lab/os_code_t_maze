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

### Tk / tkinter
Python's built-in GUI toolkit, used for all the on-screen windows and tabs. "The
Tk thread" is the thread that owns the GUI; GUI objects must only be touched from
it, which is why cross-thread hand-offs use [snapshots](#snapshot).

### USB CDC
The USB standard that lets a device pretend to be an old-style serial port. The
Arduino Due's Native USB port is a CDC device, so `COM4` on the PC is a *virtual*
port: data travels in USB packets at USB speed, and the 115200 baud both sides
quote is carried along and ignored (no wire is clocked at that rate). It matters
here because the board services its CDC endpoints from the firmware's main loop —
so a hung loop stops the "serial" link while the board still looks perfectly
plugged in. See ADR-0062 and ADR-0064.

### Watchdog
Code whose only job is to notice that something has stopped working and raise the
alarm. The [broker](#broker)'s watchdog uses a
[liveness probe](#liveness-probe) to spot a frozen Arduino within ~25 seconds,
where previously a dead rig looked identical to a quiet one and went unnoticed for
17 minutes. It reports; it cannot revive the board (only the RESET button does
that). See ADR-0062.
