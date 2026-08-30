"""tmaze.analysis - offline, cross-session behavioural analysis layer.

This subpackage turns the per-session Phase 3 recordings (the per-trial ``task/``
CSVs, and the per-excursion log when present) into cross-session, analysis-ready
tables and measures for the longitudinal ``animal_progress`` notebook. It is a
READ-ONLY consumer of the session bundles: it never touches the rig, serial, Tk,
or OpenCV; it only reads files the rig already wrote.

It is its OWN layer (not under ``tmaze.core``) on purpose: unlike the pure live
cores, this analysis leans on ``pandas``/``numpy``/``statsmodels`` - heavy
data-science deps that ADR-0083 deliberately keeps out of the pure core (enforced
by ``test_architecture``). Those deps are analysis-only and not required by the
live rig tools. See ADR-0112.

To let this layer be versioned (and, later, split into its own repo) independently
of the stabilizing rig code, it depends only on the on-disk bundle *format*, not on
``tmaze.core`` / ``tmaze.paths``: the ``phase3_format`` module owns the analysis-side
copy of the format tokens, the zone classifier, the notes parser, and the recordings
roots, with a same-repo parity test guarding drift until the split.

Layers (each a small, headlessly testable module):

- ``phase3_format``   - the analysis-side DATA CONTRACT: outcome/cue/arm tokens, a
  vendored copy of the maze zone classifier, the notes-JSONL parser, the recordings
  roots, and the bundle version guards - so nothing here imports the live rig core.
- ``bundle_reader``   - discover/filter session bundles; load the per-trial (and,
  when present, per-excursion) CSVs into tidy pandas frames; parse the ``#``-header
  config and join per-session context.
- ``choice_signals``  - per-trial derived strategy features (switch / win-stay /
  lose-shift / cue-follow / cue-switch-tracks-choice-switch), the return-cue
  carryover features, and a free / in-block / post-block epoch tag.
- ``rolling_metrics`` - windowed rate traces over the time-ordered stream, with
  session boundaries, intervention-block epochs, and an engagement proxy.
- ``intervention_response`` - per-block response metrics and the peri-intervention
  pre->post mode-transition summary (auto blocks).
- ``mode_label``      - rule-based behavioural-mode labelling (interpretable, no
  fitting) plus the in-block response labels.
- ``choice_model``    - per-session logistic regression of choice on cue + history
  + return-cue carryover (the "is it using the cue" signal).
- ``progression``     - per-session accuracy + left-choice bias over out-of-block
  trials (the notebook's per-session progression rows; see docs/decisions/0132).
- ``return_cue``      - return-cue carryover summary (Wilson-CI P(switch) by N-1
  return-cue congruency, and P(choice == N-1 return-cue side)).

Requires ``pandas``/``numpy`` (and ``statsmodels`` for ``choice_model``); these are
analysis-only deps, not needed by the live rig tools. See docs/decisions for the
ADR on the interpretable-first approach and the trial partitioning.
"""
