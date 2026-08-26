# ADR 0122: A failed reward send is recorded as not-fired, not as a delivered reward

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/reward/reward_dispatcher.py` (`RewardDispatcher.fire` / `_do_send`), `host/tests/test_reward_dispatcher.py`
- **Supersedes / Superseded by:** —

## Context

`RewardDispatcher.fire` is the one shared seam where every host reward is gated,
recorded, and sent (ADR-0054). Its `RewardRecord.fired` flag is the ground truth
both task modes use for accounting: Phase 3 only increments its reward count and
adds the calibrated volume when `fired` is True, and the Rules engine logs a
delivered reward vs a `suppressed` event off the same flag.

The old code advanced the rate-backstop timer and returned `fired=True` *before*
knowing whether the wire send succeeded, and `_do_send` swallowed any exception
from the injected serial send (`except Exception: pass`). So if the serial link
hiccuped at the moment of a fire, the rig recorded a reward — count, volume, and
all — that the animal never actually received. For a study that correlates
neural reactivations with reward history, a phantom reward in the behavioral
record is exactly the kind of silent mis-record we cannot recover after the
fact. The swallowed-exception guard was still correct in one respect: a serial
hiccup must never propagate out and break the actuation thread.

## Decision

Make `_do_send` *report* success instead of silently swallowing: it returns
`True` when the pulse reached the wire (or when `send is None`, the deliberate
headless no-op that counts as delivered) and `False` when the injected send
raised. The exception is still caught — it never propagates — but the outcome is
now visible to `fire`.

`fire` now sends **before** advancing the backstop timer. On a failed send it
returns `fired=False`, `volume_ul=0.0`, `suppressed_reason="send_failed"`, and
leaves the timer untouched, so:

- accounting is truthful — a reward that didn't reach the hardware is not counted
  and no volume is added; and
- an immediate retry isn't blocked — the backstop only arms after a reward
  actually goes out.

Both existing consumers already handle a non-fired record correctly (Phase 3
skips the count/volume; Rules logs a `suppressed` event), so the new
`"send_failed"` reason flows through to the logs with no controller changes.

## Alternatives considered

- **Keep advancing the timer on failure (old behavior, minus the phantom
  count).** Rejected: a failed fire that still armed the backstop could silently
  swallow the operator's/scorer's immediate retry, compounding the miss.
- **Raise on send failure.** Rejected: the send happens on the ADR-0043
  actuation thread; letting a transient serial error propagate there could tear
  down task control over a recoverable hiccup. Reporting via the record keeps it
  non-fatal and self-healing.
- **Add a new `fired`-adjacent field (e.g. `delivered`).** Rejected as
  over-engineering: `fired` already means "reached the wire", and the existing
  `suppressed_reason` channel distinguishes `global_backstop` from `send_failed`
  for anyone who needs the cause.
- **Also route the manual "Solenoid test" reward-spout button through the
  dispatcher (share the backstop + volume seam).** Deliberately NOT done. That
  panel is a hardware diagnostic that fires every solenoid (reward/drain/rinse);
  a human clicking it is an intentional action, and the 500 ms global backstop
  would silently drop a deliberate click - a worse behavior than the phantom
  count this ADR fixes. The operator reward-spout water is already recorded at
  its source (pulse width -> `manual_water`, plus an `auto:manual_reward`
  timeline marker) because the firmware can't distinguish task vs operator fires
  on the wire (ADR-0084/0111). Routing it through the dispatcher would also risk
  double-counting that water and would only match on the default reward sol
  index (the dispatcher hardcodes `R<port>1`). The phantom-reward integrity bug
  lived on the *automated* task path, which this change fixes; the manual path
  stays a direct, always-fires diagnostic send.

## Consequences

Reward accounting now reflects only rewards that actually reached the hardware.
A serial failure surfaces the same way a backstop drop does (a not-fired record
with a distinct reason), which the Rules CSV logs as `suppressed / send_failed`
and Phase 3 simply doesn't count. The trade-off: a `send_failed` reward is not
retried automatically — the caller/operator decides. If we later want automatic
retry or an operator-facing alarm on repeated `send_failed`, that builds on this
truthful signal rather than the old phantom-success one.

## Evidence / links

- `host/tmaze/core/reward/reward_dispatcher.py` — `fire` / `_do_send`.
- `host/tests/test_reward_dispatcher.py` —
  `test_send_failure_reports_not_fired_and_does_not_advance`,
  `test_recovered_send_after_failure_fires_normally`.
- Builds on ADR-0054 (the shared reward seam) and ADR-0019/0091 (volume
  accounting).
