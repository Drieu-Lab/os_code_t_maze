# ADR 0197: Default the camera Format dropdown to H.264, not lossless FFV1

- **Status:** Accepted
- **Date:** 2026-09-23
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/camera_view.py` (`RECORD_FORMATS`)
- **Supersedes / Superseded by:** revises the default chosen in ADR-0013 (does not supersede it)

## Context

ADR-0013 made recording format a per-recording choice in the camera controls
(a **Format** dropdown: lossless FFV1 `.mkv` vs compressed H.264 `.mp4`) and
picked **lossless FFV1 as the default** — the cautious choice at the time.

In practice lossless is heavy: ~250 GB/hour at 1.3 MP / ~42 fps (ADR-0013's own
bench). For the T-maze the video is scored for position/behaviour, not analyzed
pixel-exactly, so visually-lossless H.264 (CRF 18) is the lab standard and is
~25-100x smaller. The operator selects H.264 for essentially every real session,
so the safe default and the practical default had diverged, and an accidental
lossless run fills the disk fast. The 2026-09-22 beam-LED sync work also
confirmed the lossy H.264 bundles are fine for the clock-sync fiducials
(see the resources repo camera README), so lossy is not a barrier for sync.

## Decision

Make **H.264 the GUI default** by listing it first in `RECORD_FORMATS`, whose
index-0 label is what `_format_var` initializes to:

```python
RECORD_FORMATS = (("Compressed (H.264)", "h264"), ("Lossless (FFV1)", "ffv1"))
```

Lossless FFV1 remains one dropdown click away; nothing else in the writer,
sidecar header (`codec`/`crf`), or the per-recording lock/unlock behaviour
changes. The CRF quality control now shows by default (H.264 is selected on tab
open) via the existing `_on_format_change()` call.

## Alternatives considered

- **Keep FFV1 default, select H.264 explicitly in `_format_var`** — would put the
  default second in the dropdown; reordering keeps "the default is index 0"
  simple and shows the default at the top.
- **Drop FFV1 entirely** — rejected; some analyses may still want bit-exact
  frames, and ADR-0013's rationale for keeping both still holds.

## Consequences

- New sessions record compressed `.mp4` unless the operator switches to lossless,
  cutting typical disk use by ~1-2 orders of magnitude.
- Anyone who *needs* bit-exact frames must now remember to pick lossless — the
  reverse of the old failure mode. Acceptable given how rarely lossless is used.
- Downstream tooling already handles both `.mkv` and `.mp4` (ADR-0013), so no
  reader change is needed.

## Evidence / links

- ADR-0013 (selectable lossless/lossy recording) — the per-recording choice and
  the ~250 GB/hr lossless figure this revises the default of.
- Resources repo `rig-hardware/camera/README.md` "Beam-LED clock-sync fiducials"
  (2026-09-22) — confirms H.264 bundles are adequate for clock sync.
