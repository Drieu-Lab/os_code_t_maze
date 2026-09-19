# ADR 0173: Command sends are truthful — a cue is logged only when it reached the wire

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/io/rig_io.py` (`RigClient.send`),
  `host/tmaze/ui/phase3_controller.py` (`_send_cmd`, the cue-send blocks in
  `_process_frame`, `_note_send_failed`, status line),
  `host/tmaze/core/session/session_notes.py` (`AUTO_SEND_FAILED`)
- **Supersedes / Superseded by:** —

## Context

Review finding L2 has two halves. The firmware-clock **ack columns** half already
shipped: `choice_cue_on_fw_ms` / `reward_cmd_fw_ms` (ADR-0165) and
`return_cue_on_fw_ms` (ADR-0166) record the firmware `millis()` of cue onset and
reward from the firmware echoes, at trial-CSV `proto=12`. This ADR is the other
half (L2b): the command-**send** path lied by omission.

Two layers swallowed send failures:

- `RigClient.send` (`rig_io.py`) returned `None`. With no socket it returned
  silently; on an `OSError` from `sendall` it fired a status message and returned —
  the caller could not tell whether the line left the host.
- `phase3_controller._send_cmd` returned `None` and caught every exception.

The cue path then logged the onset unconditionally: it sent `CUE_ON,CHOICE,…` and
immediately called `Phase3Log.choice_cue_started` (and, for the return cue,
`return_cue_started`). So if the broker/socket was down, the trial CSV recorded a
cue that never played — `choice_cue_on_iso` filled, `choice_cue_on_fw_ms` blank —
and the analysis had no way to know. The reward path was already honest here
(`RewardDispatcher._do_send` returns a bool and records
`suppressed_reason="send_failed"`, ADR-0122); the cue path was the remaining hole.

This also undercuts the ADR-0171 UNCUED logic, which keys "was the animal cued this
trip?" on the per-trip onset stamp `_choice_cue_on_iso`: a dropped cue that still
set that stamp would score as a normal cued trial.

## Decision

Make the send path report whether the command reached the wire, and log a cue only
when it did.

- `RigClient.send` returns `bool`: `True` on a successful `sendall`, `False` when
  there is no socket or `sendall` raised `OSError` (the status line still reports
  the failure). Callers that ignore the return are unaffected (returning a value
  where there was `None` breaks nothing); the production Phase 3 send is
  `lambda cmd: client.send(cmd)`, which now propagates the bool.
- `phase3_controller._send_cmd` returns `bool`: `False` when no send is wired
  (`_send is None`), the injected send reports failure, or it raises.
- In `_process_frame`, the CHOICE and RETURN cue blocks capture the send's return
  and record the cue **only when it succeeded** — the onset log row, the
  firmware-ack pending index, and (for CHOICE) the `_choice_cue_on_iso` stamp. A
  dropped send stamps nothing, so a dropped CHOICE cue makes the trip score
  **UNCUED** (ADR-0171) — honest end to end.
- A dropped cue is made visible: a per-run `_send_fails` counter in the status line
  (`send_fail:N`, like `uncued:N`) and an `AUTO_SEND_FAILED` (`auto:send_failed`)
  timeline note naming the cue kind, via a small `_note_send_failed` helper. This
  distinguishes a rig fault (broker/socket down) from the animal simply not being
  cued by position.

No format/protocol bump: `proto` is already 12 (the ack columns), and this changes
only *whether* an onset is recorded, not the column set. Rig-repo only; no
analysis-mirror change.

## Alternatives considered

- **Status-line flag only, no per-cue note** — rejected. A dropped cue is a real,
  timestamped session event; recording it on the note timeline (like the reward
  `send_failed` precedent, and like AUTO_UNCUED) lets an offline reader see when
  and how often it happened, not just a running count.
- **Log the onset silently and add nothing** — rejected. That leaves the L2b hole
  (a phantom cue in the CSV) exactly as found.
- **Keep the 2c onset stamp on the send attempt, not its success** — rejected. The
  operator's call was to be honest end to end: if the tone did not play, the trip
  was not cued, so it must score UNCUED.
- **Have `_send_cmd` keep returning `None` and check `RigClient.send` at the call
  site** — rejected. Threading the bool through `_send_cmd` keeps every caller on
  one truthful contract and lets the reward/other sends stay simple.

## Consequences

- The trial CSV never records a cue onset that did not leave the host; a dropped
  CHOICE cue is scored UNCUED and re-run (ADR-0171), so the data and the welfare/
  scheduler history stay honest under a flaky link.
- **Injected sends must now report success truthfully.** A test double or wrapper
  that returns `None`/falsy is read as a failed send. The wiring-test doubles were
  updated to return `True`; the real client and the `experiment_control` closures
  already return the client's bool. This is the one behavioural ripple to watch
  when wiring a new send.
- The gate has latched "in cue" by the time the send is attempted, so a failed
  send does not re-fire this trip — the tone simply does not play, which is the
  correct physical outcome and what UNCUED then records.
- Follow-up: none required. If other cue-adjacent sends (`CUE_OFF`, `CUE,VISION`)
  ever need the same visibility, they can reuse `_note_send_failed`.

## Evidence / links

- Ack-columns half of L2: ADR-0165 / ADR-0166. Reward send truthfulness precedent:
  ADR-0122. UNCUED outcome that this makes honest: ADR-0171. Cue command protocol:
  ADR-0044/0046; per-cue calibration ADR-0125/0148.
- Tests: `host/tests/test_session_phase3_wiring.py::
  test_failed_cue_send_is_not_logged_and_scores_uncued` (a failing send records no
  onset, bumps `_send_fails`, emits `auto:send_failed`, and scores UNCUED) and
  `test_cued_trip_still_scores_and_advances` (the success path unchanged).
