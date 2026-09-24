# ADR 0199: The LED sync reads each bundle's sensor polarity instead of hard-coding it

- **Status:** Accepted
- **Date:** 2026-09-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/sync/spans.py` (`active_state`, `is_active_channel`,
  `active_spans` now take a polarity; new `infer_polarity` / `PolarityGuess`;
  `BEAM_ACTIVE_STATE` / `LICK_ACTIVE_STATE` removed), `host/tmaze/core/sync/pipeline.py`
  (`analyze(..., sensor_polarity=)` required), `host/tmaze/tools/sync_fit.py`
  (`resolve_sensor_polarity`, `SensorPolarity`, `PolarityUnresolved`,
  `--sensor-polarity`, `clock_fit_<id>.json` `sensor_polarity` block, the manifest
  `post_hoc` result), `matching.py` docstrings; tests `test_sync_core`,
  `test_sync_fixture`, `test_sync_fit`, `test_led_detector_clip`
- **Supersedes / Superseded by:** — (the follow-up ADR-0164 named: "the one host
  consumer, `core/sync/spans.py`, hard-codes the legacy active state")

## Context

A beam or lick sensor has two wire states, `0` and `1`, and only one of them is
the event the camera can see: the beam broken (its board LED goes dark) or the
tongue on the port (its LED lights). The offline LED sync (`sync_fit`, FW-0045)
matches each board LED against that *active* state only.

Which wire state is active changed with the firmware. Pre-v8 firmware reported
the active state as `0` (inverted by mistake); ADR-0164 fixed the firmware so
v8 onward reports it as `1`, and each bundle records the convention it was
recorded under in `config/format.json` as `sensor_polarity` (`corrected` /
`legacy_inverted` / `unknown`). But `spans.py` still hard-coded
`BEAM_ACTIVE_STATE = LICK_ACTIVE_STATE = "0"`, so on every v8+ bundle the sync
matched the LEDs against the **resting** periods. ADR-0164 flagged exactly this
and said not to run the sync on v8 bundles until it was fixed.

It was found on the first mapping bundle (`000_20260922_163830`, firmware proto
9, `sensor_polarity: corrected`) while locating the two-row LEDs for ADR-0196:
an event-triggered average of each LED's brightness, gated on state `0`, showed
every LED change *ending* at the serial event; gated on `1`, every change
*starts* 0-1 frames after it, as it physically must. The clock fit had still
passed (verdict ok), because a resting period begins exactly when an event ends,
so the matched edges are the same instants - but the on/off labels were swapped,
and the brief-event filtering (`visible()`: coalesce sub-frame gaps, drop
sub-frame spans) was applied to the wrong spans.

A survey of every recorded bundle's serial log (2026-09-24) found that
**24 of the 30 bundles recorded from 2026-09-15 on declare `unknown`**: the
firmware's `0,PROTO,<n>` handshake was not captured (the host opened the port
without resetting the board; ADR-0194 fixes this going forward). So a rule that
only trusted the declared flag would refuse most real sessions.

## Decision

1. **Polarity is an explicit input to the sync core, with no default.**
   `spans.active_state(polarity)` maps `legacy_inverted` -> `"0"` and
   `corrected` -> `"1"` and raises on anything else (including `unknown`);
   `is_active_channel` / `active_spans` / `pipeline.analyze` take it as a
   required argument. A silent default is how this bug happened, so there is
   none. Channel names keep the WIRE state (`S2=1` on a v8 bundle, `S2=0` on a
   pre-v8 one).

2. **The log can say its own polarity.** `spans.infer_polarity(events)` uses the
   fact the old fixture test relied on: the active state is the BRIEF one (a
   crossing or a lick lasts well under 2 s; the rest between events runs tens of
   seconds). Each beam/lick sensor compares its LONGEST span in each state (chatter
   chops the rest state into short pieces too, so medians don't work) and votes
   only when one is at least **5x** the other. The answer is the unanimous vote;
   any dissent or no vote gives "not conclusive".

3. **`sync_fit` resolves the polarity per bundle**, in this order:
   `--sensor-polarity` (operator override) > the bundle's declared
   `sensor_polarity` > `legacy_inverted` when `format.json` predates the flag
   (format < v3, i.e. recorded before v8 firmware existed) > the log's own answer.
   A declared `unknown` with an inconclusive log is REFUSED
   (`PolarityUnresolved`: exit 2 for one bundle, `skipped` with the reason in
   `--all`) rather than guessed. The log's answer is always computed; if it
   clearly disagrees with the value used, the report prints a WARNING.

4. **Every run records what it used.** `clock_fit_<id>.json` gains a
   `sensor_polarity` block (`value`, `active_state`, `source`, `declared`,
   `inferred`, `inferred_votes`, `disagrees`), the manifest's `post_hoc` result
   gains `sensor_polarity`, and the printed report states it.

## Alternatives considered

- **Trust only the declared flag.** Rejected: 24 of 30 recent bundles say
  `unknown`, so it would refuse most real sessions.
- **Infer from the recording date** (reflash day 2026-09-15). Rejected: a date
  is a proxy for which firmware was on the board, and would be silently wrong
  after a reflash to an old sketch or on a second rig. The log's own brief/long
  structure is a direct measurement.
- **Always infer, ignore the flag.** Rejected: the declared flag comes from the
  firmware's own version handshake and is the better source when present;
  inference is kept as a cross-check (the WARNING) and as the fallback.
- **Let a disagreement override the declared flag.** Rejected for now: a session
  where the rat sits in a beam for long stretches could make inference wrong, and
  the handshake is the more direct source. The disagreement is recorded and
  printed so a human decides; none occurred in the survey.
- **Rename channels by meaning (`S2=active`) at parse time.** Rejected: it would
  change `led_events` `matched_event` values, the `led_map` channel field and
  every test at once; keeping wire-state names with an explicit polarity is a
  smaller change and still unambiguous given the recorded `sensor_polarity`.

## Consequences

- The sync is now correct on v8+ bundles, and safe on pre-v8 ones (unchanged
  behaviour: they resolve to `legacy_inverted`).
- On `000_20260922_163830` (dry run) every beam and lick LED binds to its `=1`
  state, with physically sensible lags (licks light 40-49 ms after the serial
  event, beams go dark 57-70 ms after), 606 fit pairs, residual MAD 11.5 ms,
  verdict ok.
- No bundle had been clock-fitted yet (no `sync/` folders on disk), so nothing
  needs re-fitting.
- `led_map.json` channel names are wire states, so a map built on a v8 bundle
  says `S2=1` where one built on a pre-v8 bundle said `S2=0`. Nothing consumes
  the map's channel names per session yet; the two-pass map builder (ADR-0196)
  should record the mapping bundle's polarity alongside them.
- `infer_polarity`'s 5x ratio and unanimity rule were checked on the 36 bundles
  with sensor events: unanimous on all 36 (legacy before 2026-09-15, corrected
  from then on), agreeing with all 6 that declare `corrected`. Three bundles with
  no beam/lick events are inconclusive; they have nothing to fit.
- Revisit if a session ever prints the disagreement WARNING, or if a rig change
  makes long active states normal (e.g. a rat that must hold a beam for seconds).

## Evidence / links

- ADR-0164 (the firmware polarity correction + `sensor_polarity` flag; named this
  follow-up), ADR-0194 (why the handshake was missed), ADR-0196 (where this was
  found), FW-0058, FW-0045.
- Event-triggered timing on `000_20260922_163830`: scratch
  `t-maze_recordings/_led_fiducial/dual_led_out/eta_timing.png` (gated on `1`) vs
  `eta_timing_legacy_state0.png` (gated on `0`); survey script
  `_led_fiducial/infer_polarity_survey.py` (outside the repos).
- Tests: `test_sync_core` (`active_state` both ways, `unknown` refused,
  `infer_polarity` on synthetic logs in both conventions + dissent/abstain, the
  same session flipped to the corrected convention binds identically to `=1`);
  `test_sync_fixture` (the real pre-v8 excerpt infers `legacy_inverted` 7/7);
  `test_sync_fit` (resolve precedence, refusal, override, the recorded block).
