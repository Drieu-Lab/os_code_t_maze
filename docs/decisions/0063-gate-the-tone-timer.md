# ADR 0063: Run the 80 kHz tone timer only while a tone sounds

- **Status:** Accepted
- **Date:** 2026-07-27
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/sound.h` (`setupSound`,
  `startToneAsync`, `stopToneAsync`, `TC0_Handler`)
- **Supersedes / Superseded by:** Supersedes the "configure the timer once and
  leave it running" part of [ADR-0008](0008-wavetable-tone-isr.md); the wavetable,
  the 32-bit phase accumulator, the direct DAC register write, and the 80 kHz
  `DAC_SAMPLE_RATE` all stand unchanged.

## Context

`setupSound()` called `TC_Start(TC0, 0)` at boot and nothing ever stopped it, so
the DAC sample interrupt fired **80,000 times a second for as long as the board
was powered** — tone or no tone. The handler returned immediately when silent
(`if (!tonePlaying) return;`), so the work was wasted, but the interrupt entry,
flag read and return were not free. A cue plays for a second or two per trial, so
the board was paying that overhead for roughly 99% of a session to no purpose.

This came up while diagnosing two lockups on 2026-07-27 in which the Due's
`loop()` hung while the board stayed enumerated (host-side detection for that is
ADR-0062). One occurrence happened on a completely **idle** board: empty maze,
nothing recording, no tones since boot, and no bytes exchanged in either
direction. Very little happens inside an idle Due, and this interrupt was one of
the few things that did.

It is the same failure family as ADR-0008 — interrupt pressure starving the
native-USB stack, which is serviced from `loop()` on the SAM3X — but arising from
continuous fixed overhead rather than from expensive `sin()` calls, which is why
the wavetable fix did not remove it.

**This is a suspect, not a proven cause.** The gating has been unchanged since
2026-06-03, so on its own it cannot explain a failure that appeared this month.
It is fixed here because the interrupt has no reason to run when silent
regardless, and because removing it is a clean bisection step: if an idle board
now survives well past the ~20 minutes at which it previously wedged, the
always-on interrupt is confirmed.

## Decision

Start the timer with the tone and stop it with the tone.

- `setupSound()` keeps the `TC_Configure` / `TC_RC` / `TC_IER` / `NVIC_EnableIRQ`
  configuration but **no longer calls `TC_Start`**, so the board boots with the
  interrupt idle.
- `startToneAsync()` sets `toneFreq`, `phaseInc`, `phaseAcc` and `tonePlaying`
  **first**, then calls `TC_Start(TC0, 0)`. Order matters: the first interrupt
  must never sample half-written state.
- `stopToneAsync()` clears `tonePlaying`, then calls `TC_Stop(TC0, 0)`, then parks
  the DAC at mid-rail. Also ordered deliberately: stopping before the park means
  no late interrupt can overwrite the parked value and leave a DC offset on the
  amplifier.
- `TC0_Handler()`'s `if (!tonePlaying) return;` guard **stays**, to swallow an
  interrupt already pending when the tone stops.

No protocol change and no `PROTO_VERSION` bump: no message, timing contract, or
host-visible behaviour changes.

## Alternatives considered

- **Leave it as is (ADR-0008's "configure once and leave running").** Rejected:
  the overhead buys nothing, and it is the strongest available explanation for a
  failure on an otherwise idle board.
- **Disable the interrupt (`NVIC_DisableIRQ`) instead of stopping the counter.**
  Rejected: the counter would keep running and setting its RC-compare flag; the
  interrupt would just be masked. Stopping the timer removes the source.
- **Keep the timer running but raise `DAC_SAMPLE_RATE`'s period when idle.**
  Rejected as strictly worse: more moving parts, still a periodic interrupt, and
  a rate change would have to be undone at every tone onset.
- **Drop the `tonePlaying` guard in the ISR now that the timer is gated.**
  Rejected: it costs one register test and covers the pending-interrupt window at
  `TC_Stop`.

## Consequences

- An idle board runs no periodic interrupt at all, so `loop()` and the native-USB
  stack keep the whole CPU between cues.
- Tone onset is delayed by up to **one sample period (~12.5 us at 80 kHz)** while
  the timer's first RC compare arrives. That is far below audibility, and
  negligible beside the `TONE_ON` TTL raised a few instructions later in the same
  function — which is the mark neural alignment actually uses. So alignment is
  unaffected.
- Requires a **reflash** and a bench check (below); it is the only change here
  that touches the board.
- If an idle board still wedges after this, the always-on interrupt is excluded
  and the remaining candidates (the `CONFIRM wired` TONE_ON/TONE_OFF TTL pins 6
  and 7, a latent out-of-bounds write, or something non-firmware that changed the
  same week) move up.

## Evidence / links

- ADR-0008 — the original ISR starvation bug and the wavetable fix, whose
  "leave running" choice this narrows.
- Code: `firmware/cue-guided-task/sound.h` — `setupSound` (no `TC_Start`),
  `startToneAsync` (`TC_Start` after state), `stopToneAsync` (`TC_Stop` before the
  DAC park).
- Lockup evidence and what it rules out: ADR-0062 and
  `docs/notes/Bugs.md` `#260727_SerialLockout`. Occurrence 2 in particular — an
  idle board, no tones since boot, no serial traffic in either direction, dead
  somewhere in a 19-minute window.
- Bench check, 2026-07-27 (Celine, on the rig, after reflashing): 4 kHz and
  8 kHz from the Rig view's audio-cue test bar both sounded clean with no click
  at onset or offset, and a Phase 3 cue fired normally. So gating the timer costs
  nothing audible and breaks no cue path.
- **Soak test, 2026-07-27 — the hypothesis is EXCLUDED.** On the reflashed
  firmware, with the timer gated and no tone played for the preceding ~16
  minutes, the board wedged anyway: port opened 15:53:12, the liveness probe's
  write failed at 16:10:23 (~17 min), reopen failed with error 31 as expected.
  The always-on 80 kHz interrupt was therefore **not** the cause of the lockup
  (`docs/notes/Bugs.md` `#260727_SerialLockout`, occurrence 3). This ADR's
  decision still stands on its own merit — the interrupt has no reason to run
  when silent — but it buys no reliability, and the remaining candidates in
  "Consequences" above now move up.
