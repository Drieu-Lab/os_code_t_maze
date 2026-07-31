# ADR 0070: Drive Phase 3 parameters from a versioned phase3.json (pure core + Save/Reload)

- **Status:** Accepted
- **Date:** 2026-07-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/phase3_config.py` + `host/phase3.json`,
  `host/phase3_controller.py` (load/apply/Save/Reload), `host/phase3_log.py`
  (header records reward params), `host/tooltip_text.py`, `host/test_phase3_config.py`
- **Supersedes / Superseded by:** — (implements FW-0024; mirrors ADR-0031; extends ADR-0046/0069)

## Context

Phase 3's parameters were split across two unsatisfying places: frozen dataclass
defaults in code (`Phase3Config` port/pulse/reward-incorrect, `TrialConfig`
schedule + tone Hz, `CueParams` cue gates) and ephemeral Tk vars that reset every
launch (seed, cue toggles, and — after ADR-0069 — reward pulse width + max
top-ups). So Phase 3 knobs couldn't be changed without editing code, weren't
reproducible across launches, and gave the operator a different mental model from
the Rules engine, which is fully driven by a versioned `rules.json` with a GUI
editor, validation, and write-back (ADR-0031). FW-0024 tracked closing that gap.

Some of these parameters are operator conveniences (reward pulse/top-ups, cue
enables, confidence); others are **scientific** (trial count, block balancing,
tone identities, seed) where a careless mid-session edit would corrupt the task's
statistical structure.

## Decision

Add a versioned `host/phase3.json` with three sections — `reward`, `cue`,
`sequence` — loaded at startup with defaults that reproduce today's behavior, and
a pure core `host/phase3_config.py` that owns loading, merging, validation, and
resolving the dict into the three frozen dataclasses (mirroring `rules_engine`'s
editor-support layer; the thin controller shell does the Tk + file I/O).

- **Defaults derived from the dataclasses.** `default_config()` reads the fields
  off `Phase3Config()` / `CueParams()` / `TrialConfig()` (plus `max_topups` and an
  optional `seed`), so the config can never silently drift from the code's built-in
  behavior. A missing file → defaults; missing keys → per-key fallback; unknown
  keys (a `_schema` doc block) are preserved on round-trip.
- **Two tiers of editability (the key choice).** `reward` + `cue` are operator
  knobs, editable in the tab and persisted by an explicit **Save settings** button
  (validate-before-write, like ADR-0031). The `sequence` section (schedule + tones
  + seed) is **file-only**: shown read-only in a "Sequence & tones (edit
  phase3.json)" readout with a **Reload** button, so scientific params are
  deliberate, not fat-fingered mid-session.
- **Optional seed policy.** `sequence.seed` null/absent → a fresh random seed each
  launch (today's behavior; reproducible only within a session); an integer pins
  the schedule across launches. The tab's "New" button still overrides live, and
  Save does not overwrite the file's seed.
- **Validation before use.** `validate_phase3_config` resolves every section
  (coercing types + range-checking: `pulse_ms > 0`, `max_topups >= 0`,
  `0 <= min_confidence <= 1`, `block_size/max_trials > 0`, tone Hz `> 0`). The
  controller validates after load and falls back to defaults (surfacing the error
  in status) rather than crashing; Save refuses to write an invalid config.
- **Threading.** The resolved `TrialConfig` + reward port are folded into the
  atomic `_Phase3Snapshot` on the Tk poll, so the actuation thread builds the
  sequence from the snapshot on arm and never reads Tk/config state (ADR-0043).
- **Reproducibility.** The Phase 3 data-file header now also records
  `reward_pulse_ms` + `max_topups` (it already recorded seed/tones/balancing).

## Alternatives considered

- **One shared task-config file across controllers.** Rejected for now: Phase 3
  and Rules have disjoint parameter sets and lifecycles; a Phase-3-scoped file is
  simpler and matches `rules.json`'s per-domain shape. Revisit only if a genuinely
  shared config emerges.
- **Full GUI controls for the sequence/tone params too.** Rejected: these are
  scientific and rarely changed; spinboxes invite an accidental mid-session edit
  that silently alters the task. Read-only display + file editing keeps them
  deliberate. Can be promoted to GUI later if a real need appears.
- **Named profiles + a phase dropdown from the start (like some rules.json).**
  Rejected as premature (CONTRIBUTING #6): start flat, add profiles only once a
  second real Phase 3 stage exists (e.g. FW-0008 correction trials).
- **Auto-persist on every edit / on arm.** Rejected in favor of an explicit Save
  button: persisting is a deliberate operator act, and it avoids surprising writes
  to a versioned file mid-session.
- **Persist the last-used (random) seed on Save.** Rejected: it would silently pin
  a schedule; the optional-seed policy keeps "random each launch" the default and
  pinning an explicit, file-level choice.

## Consequences

- Phase 3 reward + cue settings persist across launches and are reproducible;
  scientific schedule params live in one auditable file, safe from accidental
  mid-session edits, with a Reload to pick up hand-edits.
- Defaults reproduce prior behavior exactly (no `phase3.json` needed), so the
  change is safe to land before anyone edits the file.
- The resolved reward `port`/`pulse_ms` still flow through the shared
  `RewardDispatcher` (ADR-0054), and the data-file header records the reward params
  used, satisfying FW-0024's acceptance.
- Follow-ups: profiles + a phase dropdown when a second Phase 3 stage lands
  (FW-0008); promoting a sequence param to a guarded GUI control if needed;
  possibly stamping the fully-resolved config into the session manifest.

## Evidence / links

- Implements [FW-0024]; mirrors [ADR-0031](0031-gui-edit-rules-json.md)
  (load/validate/write-back) and the `rules_engine` editor-support pattern; extends
  [ADR-0046](0046-host-owned-phase3.md) (host-owned Phase 3) and
  [ADR-0069](0069-phase3-reward-topups-via-shared-dispenser.md) (the reward
  pulse/top-up knobs now persisted); reward path per
  [ADR-0054](0054-unified-reward-dispatcher.md); snapshot hand-off per
  [ADR-0043](0043-rules-actuation-thread.md).
- Tests: `test_phase3_config.py` (defaults/merge/round-trip/validation/seed);
  `test_phase3_log.test_header_records_reward_params`; full host suite green
  (418 tests).
