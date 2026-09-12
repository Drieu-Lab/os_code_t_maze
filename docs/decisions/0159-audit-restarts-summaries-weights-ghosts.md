# ADR 0159: Four more integrity checks - restarts, summary-less bundles, weight disagreements, ghost annotations

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/records/records_audit.py` (`CHURN_WINDOW_S`,
  `WEIGHT_MISMATCH_G`, `ghost_annotations`, `sessions_without_summary`,
  `churn_pairs`, `weight_mismatches`, `build_report` keyword inputs),
  `host/tmaze/tools/records_check.py` (`on_disk_session_ids`),
  `host/tmaze/ui/records_manager.py` (`_audit_sections`, in-place dialog
  refresh, "Re-check" button, `on_disk_sessions`),
  `host/tests/test_records_audit.py`
- **Supersedes / Superseded by:** — (extends ADR-0097 and ADR-0105)

## Context

The integrity audit (ADR-0097) detects four kinds of drift: unknown animal ids,
sessions without a husbandry row, water-time cells holding volumes, and
sub-minute bundles. The 2026-09-11 records review turned up four more that no
surface reported, each of which had already happened in the real records:

1. **Restarts the duration check misses.** `short_sessions` flags bundles under
   60 s, but a mis-started session that ran 2-3 minutes before being stopped
   and restarted is not "short" - it just isn't a session. The review found
   bursts of same-rat starts 20-170 s apart (2026-07-24: four starts in six
   minutes; 2026-08-26: three in a minute).
2. **Bundles with no behavioural summary.** A session that ended without Stop
   (crash, kill) or that predates the `summary/` folder has every behaviour
   column blank in the registry. Nothing said so; it looked like a session
   with zero trials. Thirteen such bundles were indexed.
3. **Two weights for one weighing.** The bundle's frozen `animal_state.json`
   weight (ADR-0077) and the `daily_log.csv` weight for the same day are the
   same measurement copied at Start; if either is edited afterwards, the
   registry (which prefers the bundle) and the dashboard (which reads the
   daily table) silently disagree. None today - but nothing would have said.
4. **Ghost annotations.** `session_annotations.csv` is keyed by session id
   (ADR-0082). Three annotations exist for `600_20260803_1256xx` sessions that
   have no bundle on either recordings root; the View sheets list them, the
   registry can't. Either the bundles were deleted (then the notes are
   orphaned) or they live somewhere not configured (then the roots config is
   incomplete) - both are worth knowing.

Separately, the Records Manager's integrity dialog re-created itself after
every acknowledge/restore (`destroy()` + reopen), which lost the operator's
scroll position and selection while working down a 70-item list.

## Decision

Add four sections to `records_audit.build_report`, each with stable keys so
findings can be acknowledged (ADR-0105), all read-only:

- **`churn_window`** - consecutive same-animal starts less than
  `CHURN_WINDOW_S = 300` s apart; the *later* session carries the finding
  ("X starts 76 s after Y"). Start time is the summary's `start_iso`, falling
  back to the `<animal>_<YYYYMMDD>_<HHMMSS>` stamp in the id so summary-less
  bundles take part. Five minutes: the observed bursts were 20 s-3 min apart,
  and two genuine sessions of one rat can't be that close (the animal has to
  be taken out and put back).
- **`no_summary`** - registry rows with all of `start_iso`, `duration_s`,
  `trials_total`, `task_reward_ml` blank (the four come only from the summary
  file). Fix hint: acknowledge (a real session whose behaviour is simply
  unknown) or ignore-list (not a real session).
- **`weight_mismatch`** - registry rows whose weight came from the bundle
  (`husbandry_source` not `daily_table_join`/`none`) and differs from the
  daily row's weight by more than `WEIGHT_MISMATCH_G = 1` g (whole-gram scale
  readings; 1 g absorbs a rounding). The audit does not pick a winner; the fix
  hint names both sources and what each feeds. Skipped when the daily table is
  unavailable.
- **`ghost_annotations`** - annotation session ids not on the ignore list and
  not among the bundle folders found under the configured roots (ADR-0156).
  The shells pass `on_disk_ids=None` when the scan found *nothing anywhere*
  (roots unreachable), and the section is SKIPPED rather than calling every
  annotation a ghost. The GUI reuses the scan it already does at data load
  (ADR-0158); the CLI scans once.

`build_report` takes the new inputs as optional keywords (`annot_rows`,
`on_disk_ids`, `ignore`), so existing callers are unchanged and the new
sections simply appear (ghost skipped) when they don't pass them.

The GUI dialog now refreshes **in place**: one `refresh()` closure re-runs the
audit and re-fills the header and tree; acknowledge, restore (from the
"Manage dismissed" window, via an `on_change` callback) and a new "Re-check"
button all call it. The window, its position and size persist.

## Alternatives considered

- **Widen `SHORT_SESSION_S` instead of a churn window.** Rejected: a real
  session cut short at 3 minutes is a real session; what marks a restart is
  the *next* start following immediately, not the duration.
- **Flag the earlier session of a churn pair.** Rejected in favour of the
  later one carrying the finding with the earlier named in the text: the pair
  is what matters, and one finding per pair keeps the count honest; the
  operator decides which is the fragment.
- **Auto-drop ghost annotations or auto-reconcile weights.** Rejected: the
  audit is read-only by the ADR-0097 decision; both cases have two legitimate
  resolutions only a human can choose between.
- **Treat an empty on-disk scan as "everything is a ghost".** Rejected: the
  far likelier cause is an unmounted drive; a skipped section with a note is
  the honest report.
- **Keep destroy-and-reopen for the dialog.** Rejected: at 70+ findings the
  lost scroll position makes acknowledging a chore; a refresh closure is the
  same amount of code.

## Consequences

- The live report on 2026-09-11 grows from ~47 to 72 active findings: 9
  churn pairs, 13 summary-less bundles, 3 ghost annotations, 0 weight
  mismatches. These are the human pass's to-do list, not regressions.
- Two more checks are planned for the audit once their schemas land
  (hardening plan Phase 5): legacy `supp_water` cells not yet split into
  `supp_water_ml` / `supp_access_min`, and animals without `date_of_birth`.
- `records_check` now lists the recordings roots at start (tens of ms).
- The `_manage_acknowledged` signature gained an `on_change` callback; it has
  a single caller.

## Evidence / links

- `python -m tmaze.tools.records_check` on 2026-09-11 (post ADR-0158 rebuild,
  110 indexed + 3 annotation-only sessions): the four new sections listed
  above; the ghost ids are `600_20260803_125652/125706/125750`.
- ADR-0077 (frozen animal_state), ADR-0082 (annotations keyed by id),
  ADR-0097 (read-only audit), ADR-0105 (acknowledgements), ADR-0156/0158
  (roots + on-disk scan).
- Records Manager hardening plan, Phase 4.
