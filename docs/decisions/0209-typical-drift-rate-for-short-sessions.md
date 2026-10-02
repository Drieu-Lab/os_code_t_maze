# ADR 0209: Use the rig's typical drift rate when a session cannot pin its own

- **Status:** Accepted
- **Date:** 2026-09-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/sync/clockfit.py` (`RatePrior`, `rate_se_ppm`, `fit_clock(prior=)`, `drift_ppm`, new `ClockFit` fields), `host/tmaze/core/sync/typical_rate.py` (new), `host/sync_clock_rate.json` (new), `host/tmaze/core/sync/pipeline.py` (`analyze(rate_prior=)`), `host/tmaze/tools/sync_fit.py` (`TOOL_VERSION` 4, `rate_lines`, flags, `--no-typical-rate` / `--typical-rate`)
- **Supersedes / Superseded by:** —
- **Renumbered:** committed as ADR-0205 in `bd36bd1`, a number already used by
  `0205-cap-counts-normal-trials-only.md`; renumbered 0209 on 2026-10-02 (the
  reasoning is unchanged).

## Context

The offline LED sync (FW-0045, ADR-0153) fits, per session, the straight line
`cam_ticks = offset + rate * firmware_ms` ([clock fit](../glossary.md#clock-fit--drift))
with a robust Theil-Sen fit. The **rate** is the camera-vs-Arduino drift
(quoted in ppm, parts per million). How well a session pins it depends on how
long the session is and how many LED edges were matched: the rate's standard
error is about `sd(residual) / (sd(firmware_ms) * sqrt(n))`.

The whole-archive dry run (`sync_fit --all --dry-run`, folder
`_sync_dryrun_all_20260929_152425`, 100 usable fits) showed:

- The rate is a **property of the hardware pair, not of the session.** The 10
  fits whose own standard error is < 0.5 ppm have a median of **26.92 ppm** and
  a spread (SD) of 0.41 ppm - about what their own estimation error (mean
  0.39 ppm) explains, so the real session-to-session spread is ~0.1 ppm.
- Short or sparse sessions give **meaningless rates**: `600_20260827_141400`
  (1.4 min, 42 edges) fitted -116 ± 131 ppm, `601_20260918_134723` (2.4 min)
  -66 ± 44 ppm, `600_20260918_154025` (1.7 min) +72 ± 86 ppm. A negative drift
  is physically impossible for this pair.
- The standard-error estimate is realistic: the 16 fits with errors of
  1.0-1.5 ppm sit on average about one of their own errors (at most ~2) from
  26.92 ppm. The errors form a continuum with no natural break - 38 fits are
  above 1 ppm, 22 above 1.5, 18 above 2, 13 above 3.

The timing effect on those sessions is small today: moving from the session's
own rate to the typical one shifts the session-end timestamps by at most ~7 ms
(1-4 ms for most), below the ~12 ms residual scatter of a fit. But the reported
rate is wrong, and any analysis that extrapolates the line (e.g. aligning a
neural recording that runs past the video) inherits the bad slope.

## Decision

1. **A typical rate on file.** `host/sync_clock_rate.json` (schema 1) holds
   `rate` (camera ticks per firmware ms, 1e6 = no drift; now `1000026.92`),
   `sd_ppm` (0.41), and where it came from (the 10 well-pinned fits above,
   the camera s/n and the board). `tmaze.core.sync.typical_rate.load()` reads it;
   a missing or invalid file means "no typical rate", and every session then
   uses its own rate as before.
2. **When to use it.** `clockfit.fit_clock(prior=)` always computes the
   session's own rate and its standard error (`rate_se_ppm`: 1.4826 × the MAD
   of the residuals as the SD, and the firmware-ms spread pooled within
   segments). When that error exceeds **`RATE_SE_MAX_PPM = 2.0` ppm** it uses
   the typical rate instead and recomputes the offsets and residuals under it.
   The offsets are still the session's own. Why 2 ppm: statistically the
   typical rate (known to ~0.1 ppm) beats almost any single session's fit, so
   the threshold is a choice about how much a session's own rate is worth as
   an independent check of the hardware. Below 2 ppm the own rate is good
   enough - a 2 ppm error moves the ends of a 30 min session by < 2 ms,
   far below the ~12 ms residual scatter - so those sessions keep it and keep
   testing the typical value. Above it the own rate degrades quickly (tens of
   ppm for the short sessions). On the archive it moves 18 of 100 fits: every
   short or sparse session, five ~20-26 min sessions with few matched edges
   (errors 2.2-3.0 ppm), and the mapping session `000_20260922_163830`.
3. **A stale typical rate is caught.** When a session pins its own rate well
   but it is more than `RATE_FAR_SIGMAS = 4` combined standard errors from the
   typical one, the fit keeps its own rate and sets `rate_far_from_typical`.
   That is the sign the camera or the Arduino was changed and the file must be
   re-measured.
4. **Always said, never silent.** `ClockFit` records `rate_source`
   (`fitted`/`typical`), `own_rate`, `own_rate_se_ppm`, `typical_rate` and
   `rate_far_from_typical`. These appear in `clock_fit_<id>.json` (plus the
   `rate_prior` used). The printed report says `USING THE TYPICAL DRIFT RATE
   26.92 ppm instead of this session's fit: its own rate 72.03 ± 70.30 ppm is
   too uncertain`, or else `drift 26.95 ± 0.30 ppm (own fit; typical 26.92 ±
   0.41 ppm)` plus a WARNING line when the rate is far from typical. The
   `--all` batch line is flagged `typical rate used` / `rate far from typical`.
   The manifest's `post_hoc` entry records `rate_source`. `TOOL_VERSION` goes
   to 4. `--no-typical-rate` turns the fallback off; `--typical-rate <file>`
   reads another file.

## Alternatives considered

- **Always use the typical rate.** Simplest, but then a changed camera or
  Arduino (or a real clock fault) would go unnoticed and be silently fitted
  with the wrong slope. Keeping the session's own rate where it is well
  determined keeps each long session as an independent check.
- **Blend the two (a weighted average by inverse variance).** Statistically
  tidy, but the output is harder to explain ("which rate did this session
  use?") and it changes every long session's rate by a hair for no practical
  gain. A clear either/or with the reason logged was preferred.
- **Hard-code the rate in `clockfit.py`.** It is a property of the hardware,
  like `led_map.json`, and must be re-measurable when the camera or board
  changes. A config file beside the map, with its provenance, makes that
  explicit.
- **Refuse short sessions (verdict "too few").** That would throw away 18
  usable offset fits whose only problem is the slope.
- **Switch on session length or edge count instead of the standard error.**
  Length alone misses a long session with few matched edges (five ~20-26 min
  sessions here have errors of 2.2-3.0 ppm); the error combines both.
- **A lower threshold (1 ppm, 38 fits switched).** Defensible - the typical
  rate is more precise - but the gain is < 2 ms and it would take away the
  independent check from twice as many ordinary sessions.

## Consequences

- Short sessions get a physically sensible rate and a clear note saying so;
  long ones are unchanged (same rate, same residuals).
- The standard-error estimate errs high rather than low. On synthetic flat
  frame-quantisation noise it is ~2× the true scatter; on the archive it
  matched the long sessions' scatter. Erring high only moves a borderline
  session to the typical rate slightly sooner, which costs a few ms at most.
- Bundles already fitted by the older tool keep their `TOOL_VERSION` 3 fits
  until re-fit (`--all --refit`). The fits the upcoming real `--all` run
  writes will carry `rate_source`.
- **Revisit when:** the camera or the Arduino Due is replaced (re-measure the
  file from the new pair's long sessions; a `rate far from typical` flag on
  several sessions is the prompt), or a future hardware sync (FW-0018) makes
  the LED fit secondary.

## Evidence / links

- Dry-run folder `t-maze_recordings/phase3_recordings/_sync_dryrun_all_20260929_152425`;
  scratch analysis `t-maze_recordings/_led_fiducial/one_active/eval_rate_se.py`
  (per-fit standard error, the 10-fit median and SD, the threshold sweep:
  > 1 ppm → 38 fits, > 1.5 → 22, > 2 → 18, > 3 → 13) and `eval_rate_se_band.py`
  (every fit with an error of 1.0-3.5 ppm and its distance from 26.92 ppm).
- Check on real bundles (dry run to `_led_fiducial/typical_rate_check/`):
  `600_20260918_154025` (1.7 min) own 72.0 ± 70.3 ppm → typical, residual MAD
  12.5 ms; `600_20260923_135823` (17.5 min, 336 edges) own 30.1 ± 2.9 ppm →
  typical, MAD 11.6 ms.
- Tests: `host/tests/test_sync_core.py::TestTypicalRate`,
  `host/tests/test_sync_fit.py::TestTypicalRateLogging`.
- Related: ADR-0153 (the LED sync), ADR-0188 (batch buckets), ADR-0189
  (post-hoc manifest edits), FW-0045.
