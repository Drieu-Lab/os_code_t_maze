# ADR 0123: Service host-control commands (PING/STATUS) even when the session is inactive

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/task_core.h` (`step`), `firmware/cue-guided-task/cue-guided-task.ino` (`loop`), `firmware/cue-guided-task/test/test_task_core.cpp`, `firmware/PROTOCOL.md`
- **Supersedes / Superseded by:** —

## Context

The firmware answers three host-control commands: `PING` (-> `PONG`), `STATUS`
(-> `STATUS,<count>,<index>,<active>`), and `ABORT`/`STOP` (end the session).
The broker uses `PING`/`PONG` as its liveness probe to tell whether the Arduino
Due is still responsive.

Two guards meant those commands were only serviced while a session was *active*:

- `loop()` (`cue-guided-task.ino`) only called `step()` inside
  `else if (session.active)`, so when the session was idle `step()` never ran.
- `step()` (`task_core.h`) returned immediately at `if (!s.active) return n;`,
  which sat *above* the command-handling block.

A Full-task session goes inactive as soon as it completes (max-trials, timeout,
or `ABORT`) and stays inactive until the next `MODE` switch. In that window the
Due was still alive and pulsing valves/TTLs from `loop()`, but a host `PING` got
no `PONG` and a `STATUS` got no reply. The broker's liveness probe could
therefore read a perfectly healthy, just-finished rig as unresponsive — exactly
the kind of false "rig is down" signal we don't want while an operator is
between sessions or reviewing a recording, and it becomes more load-bearing as
neural recording adds more post-session choreography.

## Decision

Service the read-only host-control commands regardless of session state:

- In `step()`, move the `PING`/`STATUS` handling *above* the
  `if (!s.active) return n;` guard so they are answered in any state. `STATUS`
  already reports the live `<active>` flag, so a post-session reply correctly
  reads `0`. `ABORT` is now guarded by `&& s.active`, so aborting an
  already-idle session is a no-op (no duplicate `SESSION_COMPLETE`).
- In `loop()`, call `step()` on every non-`MODE` iteration (not only when
  `session.active`). While the session is idle, `step()` returns right after the
  command block, so the trial FSM stays off and this is a cheap no-op when
  nothing is asked.

This is a servicing-timing bugfix, not a wire-contract change: the line format,
message types, commands, and framing are all unchanged, so `PROTO_VERSION` is
not bumped. `PROTOCOL.md` is updated to state that `PING`/`STATUS` are answered
in any session state.

## Alternatives considered

- **Handle `PING`/`STATUS` in `loop()` directly, outside `step()`.** Rejected:
  it would split command handling across the pure core and the shell and
  duplicate the reply-formatting logic, defeating the "pure core owns the
  decisions" pattern (a *pure core* is the I/O-free logic that decides what to
  do; see `docs/glossary.md`). Keeping it in `step()` keeps one tested seam.
- **Only ungate `PING`, leave `STATUS` gated.** Rejected: `STATUS` has the same
  liveness/diagnostic value between sessions and already carries an `<active>`
  flag that makes an idle reply meaningful.
- **Bump `PROTO_VERSION`.** Rejected: no byte on the wire changed; only *when* an
  existing reply is produced. A version bump would wrongly imply host parsers
  must change.

## Consequences

The broker's liveness probe now stays truthful across the whole
between-sessions window, not just during an active run. The cost is that
`loop()` calls `step()` every iteration even when idle; because `step()` exits
immediately after the (usually empty) command block when `!s.active`, the extra
work is negligible and adds no new effects. If a future command needs different
semantics while idle vs active, it can branch on `s.active` inside `step()` the
way `ABORT` now does.

## Evidence / links

- `firmware/cue-guided-task/task_core.h` — `step()` command block re-ordered
  above the `!s.active` guard; `ABORT` gated on `s.active`.
- `firmware/cue-guided-task/cue-guided-task.ino` — `loop()` steps on every
  non-`MODE` iteration.
- `firmware/cue-guided-task/test/test_task_core.cpp` —
  `test_commands_serviced_when_inactive` (PING/STATUS answered post-session;
  idle ABORT is a no-op). Full native test run: "ALL TESTS PASSED".
