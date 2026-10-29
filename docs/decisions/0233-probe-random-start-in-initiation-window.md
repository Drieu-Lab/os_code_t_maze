# ADR 0233: Draw a random start trial inside each probe initiation window

- **Status:** Accepted
- **Date:** 2026-10-29
- **Author / Agent:** Agent (Cursor) on behalf of Céline Drieu
- **Affects:** `host/tmaze/core/phase3/probe_blocks.py`, `host/tmaze/core/phase3/trial_sequence.py`, `host/tmaze/core/phase3/phase3_log.py`, `host/tmaze/core/phase3/phase3_config.py`, `host/tmaze/ui/phase3_controller.py`, `host/tmaze/ui/tooltip_text.py`, `host/tests/test_phase3_probe_blocks.py`, `host/tests/test_phase3_log.py`
- **Supersedes / Superseded by:** Refines the trigger rule of [ADR-0134](0134-probe-correct-choice-trigger.md) (keeps the correct-choice gate, the unrewarded trigger trial excluded from probe accuracy, and the counterbalanced block that follows)

## Context

Under [ADR-0134](0134-probe-correct-choice-trigger.md), each `[start, end]` entry in `sequence.probe_blocks.windows` fired a probe on the **first** correct normal trial inside that range. With the window set to `[50, 120]`, even a rat at chance (~50% correct) triggered the probe on trial 50 half the time and by trial 53 almost every session. Across days, the animal could learn that water stops around trial 50 — a session-time cue that confounds the probe's purpose (reading out cue-to-arm knowledge without reward).

Wait-past-the-end is safe here because [ADR-0205](0205-cap-counts-normal-trials-only.md) already excludes the whole probe block (its trigger trial plus the `block_len` forced trials) from the [ADR-0190](0190-phase3-session-cap.md) 120-trial session cap. A probe that starts at trial 119 still runs all 10 forced probe trials, and then normal trial 120 finishes the session.

## Decision

1. **Initiation window + random start.** Each `[start, end]` pair in `sequence.probe_blocks.windows` is now an **initiation window**. When `ProbeBlockController.reset()` runs (when Phase 3 is armed, and on Restart), one 1-based start trial is drawn uniformly in `[start, end]` (inclusive) for each window, in window order, using `random.Random((seed & 0xFFFFFFFF) ^ _START_SALT)` (`_START_SALT = 0x85EBCA6B`, distinct from the within-block `_PROBE_SALT` and the main schedule streams):
   ```python
   def probe_start_trials(windows, seed: int) -> Tuple[int, ...]:
       rng = random.Random((int(seed) & 0xFFFFFFFF) ^ _START_SALT)
       return tuple(rng.randint(int(s), int(e)) for s, e in windows)
   ```
   A single-trial window `[k, k]` still pins the start to trial `k`.

2. **First correct normal trial at or after the drawn start, with no upper bound.** `on_scored` fires on the first correct, non-intervention trial whose 1-based `trial_no >= start_trial` for an un-used window — even if `trial_no` has passed the window's `end`. The window bounds only where the start can be drawn; the session cap bounds the session. If multiple windows are configured and `trial_no` has already passed more than one un-used window's start (e.g. probes were disabled across the first window and re-enabled later), the latest due window fires and the earlier un-used ones are marked skipped so two probes never fire back to back.

3. **Self-describing CSV header.** The `# probe_blocks` header line in `phase3_log.py` appends `probe_start_rule=random_start probe_start_trials=<comma-separated starts>`. Older files lack these keys, so offline analysis can tell sessions run under the random-start rule apart from sessions run under ADR-0134's first-correct rule without checking dates.

4. **Show the scheduled start in the Task tab.** `Phase3StateSnapshot` carries `probe_start_trials` and `probe_fired_trials` (0 per window until it fires, then the 1-based trigger trial). The Phase 3 panel's "Sequence & tones" box gains a second read-only line:
   - before arming: `Probe start: trial 73 (window 50-120; preview from seed)`, computed by the same `probe_start_trials` helper from the current Seed field;
   - while armed and waiting: `Probe start: trial 73 - waiting for first correct normal trial >= 73`;
   - after firing: `Probe start: fired at trial 76 (scheduled 73)`;
   - when disabled by the operator ([ADR-0137](0137-probe-mid-session-disable.md)): `Probe: disabled by operator`.
   If `sequence.seed` in `phase3.json` is pinned to an integer rather than `null`, the line appends `[seed pinned in phase3.json - same start every session]` so a fixed start cannot go unnoticed.

## Alternatives considered

- **Keep the window's `end` as a hard cutoff (skip the probe if no correct trial comes between the drawn start and `end`).** With `end = 120`, a late draw (say trial 118) followed by two wrong choices would skip the probe for the whole session, biasing completed probes toward earlier draws and higher-accuracy rats. Because ADR-0205 runs the probe on top of the 120 normal trials, waiting past `end` has no cost and avoids that selection bias.
- **Per-trial hazard rate inside the window (fire each correct trial with probability `1 / remaining`).** Gives a roughly uniform start only if every trial is an eligible correct trial; with wrong choices and intervention blocks the effective distribution shifts toward the end of the window, and there is no scheduled start trial to show the experimenter in the Task tab before it happens.
- **Fire on the *k*-th correct trial inside the window.** Spreads the start in "correct-trial count" rather than trial number, still clusters near the window start when accuracy is high, and requires picking an arbitrary upper bound on *k*.
- **Keep the old rule behind a `phase3.json` toggle.** No current need to run fixed-onset probes again; a `[k, k]` window still pins the start if a bench test ever wants it, and the CSV header's `probe_start_rule` key distinguishes new sessions from old ones.

## Consequences

- Across sessions (with `seed: null`), the probe start is uniform over the configured window (`50-120`), and the probe fires on the first correct normal trial at or after that start.
- Given the session seed, the drawn start is reproducible; the actual trigger trial still depends on the animal's choices, as under ADR-0134.
- The experimenter sees the scheduled start trial in the Task tab before and during the session, and the trial CSV header records both the rule (`probe_start_rule=random_start`) and the drawn trial (`probe_start_trials=...`).
- Follow-up (separate change in `drieu_lab_analysis`): parse `probe_start_rule` / `probe_start_trials` in `bundle_reader.py` and record the regime change in `notebooks/phase3_timeline.json`.

## Evidence / links

- Supersedes the trigger rule in [ADR-0134](0134-probe-correct-choice-trigger.md); builds on [ADR-0115](0115-phase3-unrewarded-probe-blocks.md), [ADR-0137](0137-probe-mid-session-disable.md), [ADR-0190](0190-phase3-session-cap.md), and [ADR-0205](0205-cap-counts-normal-trials-only.md).
- Tests: `python -m unittest tests.test_phase3_probe_blocks tests.test_phase3_log` (from `host/`).
