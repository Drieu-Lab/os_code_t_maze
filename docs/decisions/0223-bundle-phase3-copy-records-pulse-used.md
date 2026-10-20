# ADR 0223: The bundle's copy of phase3.json records the reward pulse actually used

- **Status:** Accepted
- **Date:** 2026-10-20
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_config.py` (new
  `with_reward_pulse_used`), `host/tmaze/core/session/session_config.py`
  (`write_config_snapshot(json_overrides=)`, index entry `rewritten`),
  `host/tmaze/ui/phase3_controller.py` (new `archived_config`),
  `host/tmaze/ui/session_controller.py` (`_write_config_snapshot` passes it);
  tests `test_phase3_config`, `test_session_config`, `test_session_phase3_wiring`
- **Supersedes / Superseded by:** refines ADR-0167 (save-on-arm) and ADR-0072
  (config snapshot); follows ADR-0191 (per-animal reward)

## Context

Since ADR-0191 the reward pulse for a rat comes from `animals.json` when the
rat has a `reward_ul` (or a `daily_water_ml`); only when both are blank does
the rig use the Phase 3 tab's Pulse field (whose saved value lives in
`host/phase3.json`). At Session Start, save-on-arm (ADR-0167) writes the tab's
fields to `host/phase3.json` and the snapshot (ADR-0072) copies that file into
the bundle's `config/`.

So the copy held the Pulse field even when the field was ignored. In bundle
`600_20261019_155908` the trial-CSV header, the manifest's `reward_policy` and
all 85 per-trial `prime_ms` values say 365 ms (70 uL from `animals.json`), but
`config/phase3.json` said 470, and so did the summary's
`task_params.reward_pulse_ms`, which is read from that copy. One bundle gave two
different answers to "what reward did this rat get?".

## Decision

The bundle's `config/phase3.json` records the pulse the session actually used:

1. `reward.pulse_ms` is the resolved pulse, from the same
   `resolved_reward_policy()` the manifest records, so the two always agree.
2. Two keys sit beside it: `reward.pulse_ms_source` (the rule that decided it:
   `animals.json:reward_ul`, `animals.json:daily_water_ml`, or `config` when the
   field was used) and `reward.pulse_ms_field` (the Pulse-field value, kept so
   nothing is hidden). With source `config` the two pulse values are equal.
3. Only the bundle's copy changes. `host/phase3.json` keeps the Pulse field as
   the fallback for a rat with no reward set; writing the resolved value there
   would make the fallback drift to whatever the last rat received.
4. The snapshot core gets a general `json_overrides` option: write the given
   JSON instead of copying the source. The index entry keeps the source path,
   hashes the file as written, and is marked `rewritten: true`, so a reader
   comparing the copy with `host/phase3.json` knows why they differ. If the
   override can't be written the file is copied verbatim, as before.
5. Bundles already recorded are left alone. Their header, manifest and
   per-trial `prime_ms` are correct; editing their copy would no longer match
   the checksum recorded at Start.

The summary's `task_params.reward_pulse_ms` is fixed for free, since it reads
the copy.

## Alternatives considered

- **Also write the resolved pulse into `host/phase3.json`.** One fewer
  mechanism, but the operator's fallback would silently become the previous
  rat's pulse. Rejected by the operator.
- **Replace `pulse_ms` without the extra keys.** Simpler, but loses the
  Pulse-field value and doesn't say why the copy differs from the host file.
  Rejected by the operator.
- **Repair old bundles with a one-off tool.** Possible (rewrite, re-hash, log a
  post-hoc edit), but the correct values are already in each bundle's header and
  manifest. Rejected by the operator for now.
- **Patch the copy after the snapshot.** Would leave the index checksum stale
  or need a second rewrite of the index; writing the right content in the first
  place is simpler.

## Consequences

- New bundles agree with themselves: `config/phase3.json`, the trial-CSV
  header, the manifest and the summary all carry the same pulse.
- For new bundles the copy is no longer byte-identical to `host/phase3.json`;
  the index's `rewritten: true` flags it. Two keys are added under `reward`;
  the loader and validator already ignore unknown keys there, and the analysis
  repo reads only `cue` from this file, so no `format_version` bump is needed.
- Pre-change bundles still have the old contradiction; read the reward from the
  trial-CSV header (`reward_pulse_ms`, `reward_pulse_source`) or the manifest's
  `provenance.reward_policy`, not from their `config/phase3.json`.

## Evidence / links

- Bundle `600_20261019_155908`: header `reward_pulse_ms=365` /
  `reward_pulse_source=animals.json:reward_ul`, manifest `reward_policy.pulse_ms`
  365, `prime_ms` 365 on all 85 rewarded trials, `config/phase3.json`
  `reward.pulse_ms` 470, summary `task_params.reward_pulse_ms` 470.
- Code: `phase3_config.with_reward_pulse_used`,
  `session_config.write_config_snapshot(json_overrides=)`,
  `Phase3ControlView.archived_config`, `SessionControlView._write_config_snapshot`.
- Tests: `test_phase3_config.TestLoadMerge.test_with_reward_pulse_used`,
  `test_session_config` (`test_json_override_replaces_the_copy_and_is_indexed`,
  `test_unwritable_override_falls_back_to_the_verbatim_copy`),
  `test_session_phase3_wiring.test_bundle_config_records_the_reward_pulse_actually_used`.
- Related: ADR-0072, ADR-0167, ADR-0191.
