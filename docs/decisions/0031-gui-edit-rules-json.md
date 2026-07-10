# ADR 0031: Edit rules.json from the Rules tab (write back to the versioned file)

- **Status:** Accepted
- **Date:** 2026-07-09
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rules_engine.py` (editor support helpers), `host/rules_view.py` (Rules tab field editor + save), `host/tooltip_text.py`, `host/rules.json`
- **Supersedes / Superseded by:** —

## Context

`host/rules.json` is the declarative config for the position-based rules engine
(ADR-0023) and the reward-shaping phases (ADR-0026/0028/0030). Until now the
Rules tab could only *read* it: it loaded the file, let the experimenter switch
the active phase and nudge the live `nonstem_reward_prob`, and offered a "Reload
rules.json" button — but every real edit (a pulse width, a credential, a debias
cap) required hand-editing JSON in a text editor and reloading. That is slow and
error-prone mid-session, and a malformed edit is only discovered on reload.

The experimenter asked to tune rule fields directly from the GUI. The three
phases in use (`phase1`, `phase2a`, `phase2b`) all use the one
`well_reward_with_excursion` template, so a single template-driven form covers
them; a few fields (`nonstem_reward_prob`, `debias`) are only meaningful for
certain credentials.

Two questions shaped the decision: (1) how much editing to expose, and (2) where
saved edits should go, since `rules.json` is git-versioned (unlike the per-rig
`rig_scale.json` / `zones.json`, which are gitignored).

## Decision

Add a **per-rule field editor** to the Rules tab that edits the active phase's
rule tunables and **writes them back to `host/rules.json` directly** (the same
versioned file), rather than to a new per-rig override file.

- The editable-field spec, value coercion, whole-profile validation, and the
  file write live in the pure core `rules_engine.py`
  (`RULE_EDIT_FIELDS`, `RULE_FIELD_CHOICES`, `coerce_rule_field`,
  `set_rule_debias`, `validate_profile_rules`, `save_rules_config`) so they are
  unit-testable; the Tk shell (`rules_view.py`) only renders widgets and calls
  them. This keeps the "pure core + thin shell" split (CONTRIBUTING.md).
- Only a curated subset of each template's params is editable. The GUI mutates
  the already-loaded config dict **in place** and re-serializes the whole dict,
  so the `_schema` docs block, `version`, `defaults`, and any key the editor
  does not expose (e.g. a rule's `landmark`, or a well rule's legacy
  `min_confidence` that the engine ignores) are preserved verbatim.
- Conditionally meaningful fields are grayed out by credential:
  `nonstem_reward_prob` only for `choice` (ADR-0028); `debias` only for
  `choice_via_stem` / `clean_loop` (ADR-0030).
- **Guards:** a save first coerces field types (rejecting malformed values), then
  validates by building the profile through `validate_profile_rules`
  (`RulesEngine.from_config` path); if either fails, nothing is written. After a
  successful save the engine is rebuilt from the saved file and **disarmed**, so
  a live session never runs against half-applied edits (mirrors the existing
  disarm-on-phase-change behavior). The save also persists the current phase
  selection into `active_profile`.

## Alternatives considered

- **Write to a gitignored per-rig override file (e.g. `rules.local.json`).**
  Mirrors `rig_scale.json` / `zones.json` and avoids git churn on the shared
  config. Rejected for now because it splits the source of truth (a rule would
  live half in `rules.json`, half in the override), complicating both the engine
  load path and human review; `rules.json`'s own `_schema` already invites
  editing it as the single config. Revisit if per-rig divergence becomes real
  (tracked as future work).
- **A raw JSON text editor in the tab.** Maximally flexible but no guardrails —
  easy to write a structurally valid but semantically wrong file, and no typed
  widgets. Rejected in favor of a structured field editor.
- **Full add/remove/rename of rules and phases.** Out of scope for the request;
  field editing covers the day-to-day tuning need. Deferred.
- **Keep read-only + external text editing.** The status quo; rejected because
  it is slow mid-session and defers error discovery to reload.

## Consequences

- Editing rule tunables no longer requires leaving the GUI, and invalid edits
  are caught before they touch disk.
- `host/rules.json` will show up in `git diff` after GUI edits. That is
  intentional (it is the single config) but means per-rig tuning is a tracked
  change; if rigs need to diverge, the per-rig-override alternative above should
  be reconsidered.
- The editor exposes a curated field set; adding a new tunable to the GUI means
  adding it to `RULE_EDIT_FIELDS` (and `RULE_FIELD_CHOICES` if enumerated).
  Fields absent from the spec remain editable only by hand, but are preserved on
  save.

## Evidence / links

- Code: `host/rules_engine.py` (`# see docs/decisions/0031`), `host/rules_view.py`
  (Rules editor frame + `_save_rules`), `host/tooltip_text.py`
  (`rules.editor.*`).
- Tests: `host/test_rules_editor.py` (coercion, debias set/clear, validation,
  save/load round-trip preserving `_schema` and untouched keys).
- Related: ADR-0023 (rules engine), ADR-0026 (reward-shaping phases),
  ADR-0028 (probabilistic fade), ADR-0030 (prime at choice entry).
