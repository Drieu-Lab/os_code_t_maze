# ADR 0027: Embed acquisition-time tracker settings in the saved model and restore them on load

- **Status:** Accepted
- **Date:** 2026-07-07
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (`RatModel.save`/`load`, `RatTracker.load_model`,
  `process()` registration finalize, new `MODEL_SHAPING_FIELDS` /
  `MODEL_PROVENANCE_ONLY_FIELDS` / `MODEL_REAPPLY_FIELDS`), `host/tracker_view.py`
  (load report + control refresh), `host/blob_tracker_prototype.py` (load report),
  `host/tooltip_text.py`, `host/test_tracker_model.py` (new); saved `.npz` model
  format
- **Supersedes / Superseded by:** — (extends ADR-0014 model persistence; relates
  to ADR-0017 live scoring knobs)

## Context

A saved appearance model (`RatModel`, ADR-0014) stored only sufficient statistics
plus `animal`/`date`. Two kinds of tracker settings that a model depends on were
dropped on save, and `load_model()` recomputed with whatever the *current* session
params happened to be:

1. **Correctness.** The FORM features `[log(area), log(elongation), solidity]` and
   the colour histogram are measured on a downscaled copy (`proc_max_w`) after the
   mask pipeline (`thresh`, `blur`, `morph`) and blob filter (`min_area`,
   `max_area`). Change any of these and the same rat produces different stored
   numbers, so a model loaded under a different `proc_max_w` was silently scored
   against a wrong-scaled size band with no error raised (see the warning in
   `tracker.py`'s module docstring).
2. **Robust detection of *this* model.** The scoring/tolerance/tracking knobs
   (`appearance_bias`, `w_*`, `accept_score`, `form_tol`, `hist_min`,
   `area_gate_k`, `gate_radius`, `use_kalman`, `confirm_frames`,
   `coast_max_frames`) are the operating point an experimenter tunes so the model
   locks onto the rat and rejects intruders. Reloading a model without them threw
   that tuning away.

ADR-0017 made the tolerance knobs *live* (read per-frame, no recompute). That was
about **when** a knob takes effect, and was mistaken here for a reason not to
persist them; the two are orthogonal.

## Decision

**Persist the full acquisition-time `TrackerParams` snapshot in the model `.npz`,
and on load restore the full tuned operating point (no opt-out), except a small
provenance-only group.**

- `RatModel.acq_params` holds the settings the model was built under. It is
  captured at registration finalize (`process()`: `built.acq_params =
  replace(params)`) and set for a fresh model; a loaded model keeps whatever it
  was saved with.
- `RatModel.save()` writes it as a JSON string (`acq_params_json`, plus a plain
  `proc_max_w` for quick inspection), mirroring how `animal`/`date` are stored so
  the file stays `allow_pickle=False`-safe. `load()` rebuilds a `TrackerParams`
  using only known dataclass fields (tolerant to added/removed knobs).
- Three field groups in `tracker.py`:
  - `MODEL_SHAPING_FIELDS = (proc_max_w, thresh, blur, morph, min_area,
    max_area)` — correctness-critical; used to flag a background re-grab (a
    `proc_max_w` change already invalidates the reference via `update_params`).
  - `MODEL_PROVENANCE_ONLY_FIELDS = (reg_min_frames, reg_dominance,
    reg_logarea_spread_min, adapt_enrich, adapt_min_conf, adapt_max_samples)` —
    saved for the record but **not** re-applied: `reg_*` only matter while
    building a *new* model, and `adapt_*` (keep-learning) is a live session
    choice.
  - `MODEL_REAPPLY_FIELDS` = every other `TrackerParams` field. These are restored
    on load so the model comes back with its tuning.
- `RatTracker.load_model(path, restore=True)` re-applies `MODEL_REAPPLY_FIELDS`
  from `acq_params` via `update_params`. Models saved before this change have
  `acq_params is None`, so restore is a no-op and the session's current tuning is
  kept.
- The GUI (`tracker_view.py`) and prototype report exactly which fields changed,
  refresh the Tracking-tab sliders to the restored values, and remind the user to
  re-grab the background when a shaping field changed. `MODEL_VERSION` stays `2`
  (the new keys are additive/optional), so existing v2 models still load.

## Alternatives considered

- **Reapply only the shaping subset, keep scoring knobs live** — rejected: it
  fixes correctness but discards the tuning that makes a model detect robustly,
  which is most of the value of saving a model per animal.
- **Restore with an opt-out toggle** — considered, not taken (per rig decision):
  a full, predictable restore is simpler and the values remain freely adjustable
  after load, so an operator who wants current-session tuning can just re-tune.
- **Also reapply `reg_*`/`adapt_*`** — rejected: they have no effect on a loaded
  model (only on a future registration / on whether to keep learning), so forcing
  them would silently reconfigure the operator's setup for no benefit.
- **Bump `MODEL_VERSION` to 3** — rejected: the change is purely additive; keeping
  v2 with optional keys means older models load unchanged instead of erroring.
- **Store params as a pickled object / separate sidecar file** — rejected: JSON in
  the same `.npz` keeps one file, no pickling, and matches the existing
  `animal`/`date` pattern.

## Consequences

- A reloaded model is scored under the settings it was built with, and a
  `proc_max_w` mismatch can no longer silently corrupt size scoring.
- The `.npz` gains optional keys; readers that ignore unknown keys are unaffected,
  and pre-change models load with `acq_params=None` (current tuning kept).
- Loading a model overwrites the session's live detection/scoring knobs with the
  model's saved values (by design). The UI/prototype surface every change so this
  is not silent.
- `area_gate_k` is now restored even though it is read live (ADR-0017); "live" and
  "persisted" coexist — the restored value is still adjustable afterward.
- Revisit if a future knob needs a different save-vs-restore policy (add it to the
  appropriate field group) or if per-field restore control is wanted.

## Evidence / links

- Code: `host/tracker.py` (`MODEL_*_FIELDS`, `RatModel.acq_params`,
  `save`/`load`, `load_model`, `process()` finalize), `host/tracker_view.py`
  (`_load_model`, `_load_model_message`, `_refresh_controls`),
  `host/blob_tracker_prototype.py` (load handler), `host/tooltip_text.py`.
- Tests: `host/test_tracker_model.py` — full `TrackerParams` save/load round-trip,
  backward-compat load without stored settings (`acq_params is None`), and (OpenCV
  present) `load_model` restoring every `MODEL_REAPPLY_FIELDS` value while leaving
  `MODEL_PROVENANCE_ONLY_FIELDS` at the session defaults. Round-trip +
  backward-compat verified headlessly (numpy-only); restore tests skip without
  OpenCV.
- Related: ADR-0014 (model persistence + sufficient statistics), ADR-0017 (live
  scoring-time tolerance knobs), ADR-0020 (background image is session-specific,
  still re-grabbed and not persisted with the model).
