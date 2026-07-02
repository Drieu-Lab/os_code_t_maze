# ADR 0023: Position-based rules engine with cm-based spatial calibration

- **Status:** Accepted
- **Date:** 2026-07-02
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rules_engine.py` (new pure core), `host/rules_view.py`
  (new "Rules" tab + executor), `host/rules.json` (new, versioned config),
  `host/rig_scale.json` (new, per-rig, gitignored), `host/experiment_control.py`
  (tab wiring + injected send), `host/test_rules_engine.py` (new tests),
  `host/tooltip_text.py`, `.gitignore`, `PROGRAMS.md`, `docs/future-work.md`
- **Supersedes / Superseded by:** — (implements FW-0004; builds on
  [ADR-0018](0018-landmark-annotations.md) landmarks,
  [ADR-0014](0014-markerless-position-tracking.md) tracker,
  [ADR-0015](0015-undistort-at-source.md) undistort-at-source,
  [ADR-0007](0007-reward-at-central-port.md) reward path,
  [ADR-0010](0010-manual-solenoid-control-in-task.md) manual solenoid,
  [ADR-0019](0019-reward-volume-and-pulse-duration-logging.md) reward volume)
- **Refined 2026-07-02 (same day, pre-adoption):** after a first bench run, the
  single per-rule `cooldown_ms` was split into two timers - `topup_cooldown_ms`
  (short, spaces top-ups within a visit) and `rearm_refractory_ms` (longer, the
  between-visit anti-camp timer) - and top-ups now require that a prime actually
  fired this visit. The old `cooldown_ms` key is still accepted (seeds both).
  The Decision/Consequences below reflect this.

## Context

The tracker (ADR-0014) gives the rat's centroid in full-frame px, and ADR-0018
lets the experimenter mark the reward wells and body-sensor beams as normalized
image-space landmarks - explicitly so a future task-rule engine could reason
about the animal's position relative to them (FW-0004). We now need that engine,
with a first concrete rule: prime the central reward port when the animal
approaches it, then top up on each subsequent lick while it stays close.

Three forces shaped the design:

1. **This actuates reward hardware.** A position-triggered auto-reward path
   needs the same care as ADR-0007/0010: it must be off by default, gated, rate-
   limited, and must not fight the firmware's own Full-task reward logic.
2. **Rules must be editable by a non-coder.** Radii, counts, pulse widths, and
   which landmark/port a rule targets should live in a declarative file, not in
   Python.
3. **Rule distances are physical (cm), not pixels.** "Within 8 cm of the center
   well" is meaningful; "within 80 px" is not (it changes with camera height /
   resolution). We need a cm<->px calibration. Because the feed is lens-corrected
   (ADR-0015) and the floor is planar under an overhead camera, a single linear
   scale is a good first approximation, with a perspective homography as an
   accuracy upgrade if one global scale proves inaccurate across the frame.

## Decision

**Split evaluation from execution.** A pure, UI-free core
(`host/rules_engine.py`, like `tracker.py`) only *decides*: `tick(track_result,
landmarks, scale, now)` and `event(line, now)` return a list of action *intents*
(`FireReward`, `LogEvent`). It never sends serial, never writes files, holds all
rule state, and has no Tk/OpenCV/numpy dependency (a homography is applied with
plain arithmetic), so it is unit-testable headlessly. A thin GUI layer
(`host/rules_view.py`, the "Rules" tab) is the **single actuation authority**: it
loads config, polls the tracker, feeds lick lines, and executes the returned
intents (`RigClient.send("R<port>1,<ms>")`, write a rule-event row).

**First rule template: `prime_and_lick_topup`.** On entering the zone (confident
track within `radius_cm` of the landmark, a *radius* not a diameter), dispense
once ("prime"); then each subsequent lick at the port while still inside
dispenses again, up to `max_topups` times, spaced by `topup_cooldown_ms`. To
prime again the animal must LEAVE the zone AND wait out `rearm_refractory_ms`
since the last fire; a re-entry inside the refractory pays **nothing** (no prime
fires, and top-ups are gated on a prime having fired this visit), so camping at
the well or quick out-and-back is not rewarded - the animal is pushed to explore
and return. The "within R at lick time" conjunction uses the most recent tick's
inside+confidence, guarded by a freshness window so a stale position can't gate a
lick. Rules are declarative (`host/rules.json`) with a documented `_schema` block
and a template registry, so a non-coder edits parameters and picks a template
without touching Python.

**Spatial calibration (`host/rig_scale.json`).** Baseline is a single global
`cm_per_px`, obtained by measuring the real distance between pairs of already-
placed landmarks; the stored scale is the **mean over several segments**, and the
**coefficient of variation** across segments is surfaced in the UI so a poor
single-scale fit is visible. The engine's `Scale` also supports an optional
image(px)->world(cm) **homography**: when present, distances are computed in
world-cm space (perspective-correct); otherwise it falls back to the global
scale. The proximity radius is drawn on the preview (cm->px) so R is verifiable
while tuning.

**Safety / authority gating.** The engine is disabled by default and only
*enabled* (evaluated) while ALL hold: the user has armed it, a valid scale
exists, tracking is running, and the firmware is in **Free Roam**. Restricting
actuation to Free Roam keeps the firmware the sole reward authority during the
Full task (ADR-0007), avoiding two systems rewarding at once. Because the gate
sets `engine.enabled`, a closed gate means the engine does not run at all - no
state advances, nothing fires. On top of the per-rule timers (`topup_cooldown_ms`
within a visit, `rearm_refractory_ms` between visits) and per-visit `max_topups`,
the engine enforces a **global `global_min_interval_ms` backstop** against
runaway firing across all rules. Every fire is logged to a dedicated
`rule_events` CSV (in the recordings' `track_logs/` folder) and also flows to the
existing GUI reward tally via the firmware `REWARD,<port>,MANUAL,<ms>` line - no
firmware/protocol change (the reward reuses the existing `R<port>` command).

## Alternatives considered

- **Rules that only log, never actuate** — rejected: the concrete need is
  priming/topping-up reward at the center port. Actuation is in scope, so the
  safety machinery is built in rather than deferred.
- **Let a rule fire in any firmware mode** — rejected as the default: in Full
  task the firmware already auto-rewards on correct choices (ADR-0007); a second
  authority risks double reward. Free-Roam-only actuation is the safe split
  (revisit if a host-side task engine ever needs to drive Full task).
- **Bake a proximity radius into the landmark marker** — rejected (also per
  ADR-0018): radius is a rule-time parameter, so it lives in `rules.json`, not in
  the geometry.
- **Radii in pixels** — rejected: not physically meaningful and not portable
  across camera setups; cm + calibration is the right unit for a behavioral rule.
- **Homography from the start** — deferred: a single global scale is simpler and
  adequate for a lens-corrected overhead planar floor; the engine is designed so
  a homography can be dropped in later (the multi-segment CV tells us when it is
  warranted).
- **Put actuation in the pure core** — rejected: keeping the core free of I/O is
  what makes it headlessly testable and reusable, and concentrates the hardware
  authority (and the mode gate) in one auditable place.
- **A single per-rule cooldown for both top-ups and re-priming** — rejected after
  the first bench run: one timer forces a bad trade-off (small = top-ups work but
  the animal can camp and re-trigger primes; large = anti-camp but top-ups are
  suppressed - exactly the "no top-ups" symptom observed). Splitting into a short
  `topup_cooldown_ms` and a longer `rearm_refractory_ms` decouples the two. The
  legacy `cooldown_ms` key still loads (seeds both) for back-compat.

## Consequences

- A clean, testable foundation: `rules_engine.py` has no UI/hardware deps and is
  covered by `test_rules_engine.py` (enter/exit/re-arm, cm->px thresholds,
  top-up count cap, the lick conjunction, the confidence gate, the split timing
  model - short within-visit top-up cooldown vs the longer between-visit
  refractory, incl. the anti-camp "re-entry pays nothing" guarantee and legacy
  `cooldown_ms` back-compat - the global backstop, and the homography path).
- Non-coders can add/tune rules in `rules.json`; new rule types are added as
  templates in the registry.
- One more per-rig gitignored artifact (`host/rig_scale.json`), like
  `landmarks.json`/`roi_polygon.json`. Like them it is tied to the camera
  geometry: re-measure if the camera moves or the calibration changes.
- A minor, deliberately conservative edge: if the global backstop drops a fire,
  the producing rule has already advanced its own counter/cooldown (the core
  errs toward *fewer* fires). This only matters when multiple rules contend
  within the backstop window; documented rather than complicated away.
- Revisit if: a single scale proves inaccurate across the frame (add the
  homography via the same measurement pairs as correspondences), rules need
  sequencing across landmarks (center -> choice arm -> reward), or the engine
  should also drive the firmware Full task.

## Evidence / links

- Code: `host/rules_engine.py` (`Scale`, `InZone`/`TrackConfident`/`LickAt`,
  `FireReward`/`LogEvent`, `PrimeAndLickTopupRule`, `TEMPLATES`, `RulesEngine`),
  `host/rules_view.py` (`RulesControlView`: gate, executor, calibration UI,
  proximity overlay, rule-event log), `host/rules.json` (schema + first rule),
  `host/experiment_control.py` (Rules tab + injected `send`).
- Tests: `host/test_rules_engine.py` (22 cases, run
  `python -m unittest test_rules_engine` from `host/`).
- Related: ADR-0018 (landmarks consumed here), ADR-0014 (tracker), ADR-0015
  (undistort-at-source - landmarks/centroid share this corrected px space),
  ADR-0007/0010/0019 (reward path + volume tally reused), FW-0004 (the backlog
  entry this closes).
