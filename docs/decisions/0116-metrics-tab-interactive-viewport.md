# ADR 0116: Metrics-tab interactive time viewport (follow-unless-zoomed)

- **Status:** Accepted
- **Date:** 2026-08-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/tmaze/core/phase3/trace_viewport.py` +
  `host/tests/test_trace_viewport.py`, `host/tmaze/ui/phase3_traces_view.py`
  (viewport controls replace the "last N min" clamp), `host/tmaze/ui/tooltip_text.py`,
  `PROGRAMS.md`
- **Supersedes / Superseded by:** refines ADR-0114 (the "last N min" x-window)

## Context

The live Metrics tab (ADR-0114) plotted the session's rolling traces on a shared
wall-clock x-axis and offered a single "Last N min" spinbox that clamped the view
to the most recent N minutes. That is enough to watch the live edge, but it can't
show an earlier stretch of the session (you can't scroll back), and it can't show
a WIDER arc than the current data — which the upcoming past-session reference
overlay (a "ghost" trace from a prior, possibly longer, session) needs: the whole
reference arc must be scrollable into view even when it runs past the current
session's clock. So the fixed clamp has to become a real, navigable window.

## Decision

Replace the "last N min" clamp with a **zoomable/scrollable viewport**: the
visible x-window is `width` (the zoom) positioned by a scroll `start` (the pan),
optionally pinned to the newest data (`follow`).

1. **Pure helper `trace_viewport.compute_viewport(total_min, width_min,
   start_min, follow) -> (xlo, xhi, scroll_lo, scroll_hi)`** does all the
   arithmetic: it clamps `start` to `[0, total-width]`, treats a blank/0/oversized
   width as "whole session" (thumb fills the bar), pins to the right edge when
   `follow`, and returns both the matplotlib x-limits and the scrollbar thumb
   fraction. Stdlib only, headlessly tested — the Tk shell stays thin (ADR-0083).

2. **View controls (`phase3_traces_view`)**: the "Last N min" spinbox becomes a
   **"Width (min)"** spinbox (blank/0 = whole session), with a horizontal
   `ttk.Scrollbar` under the canvas and a **"Latest"** button. The viewport state
   (`_view_width` via the control, `_view_start`, `_follow_latest`, and the
   per-redraw `_total_min`) drives `axes[0].set_xlim(...)` every redraw — the
   x-axis is never re-autoscaled — so a manual zoom/pan survives the live
   `progress_signature`-gated redraws instead of snapping back. All subplots keep
   `sharex=True`, so the one scrollbar pans them together.

3. **Follow-unless-zoomed**: the panel starts following the live edge. **Panning**
   (dragging the scrollbar) is a manual take-over and turns follow OFF, so the
   plot stops chasing new trials while you inspect an earlier stretch; **"Latest"**
   turns it back ON and snaps to the right edge. While following, the resolved
   left edge is remembered into `_view_start`, so the first drag resumes from
   where the eye already is.

4. **Changing WIDTH keeps the current follow state** (it does NOT force follow
   off). Zooming while following stays pinned to the live edge — the intuitive
   live behaviour — rather than jumping to the start of the session. This is a
   deliberate reading of "follow-unless-zoomed": only *panning away* counts as
   leaving the live edge; changing the zoom level does not.

## Alternatives considered

- **Keep the "last N min" clamp, add a second "offset" spinbox.** Two coupled
  numeric boxes are a clumsier pan than a scrollbar (no thumb showing where you
  are in the session) and still don't express "whole session" cleanly. Rejected.
- **matplotlib's built-in pan/zoom toolbar (`NavigationToolbar2Tk`).** It fights
  the follow-latest auto-redraw (each redraw would reset its view), exposes
  y-panning we don't want, and gives no clean "jump to live edge". A purpose-built
  width+scrollbar+Latest trio is smaller and matches the one axis we care about.
- **Changing width forces follow off (the literal plan wording).** Rejected: on a
  live session, zooming in would jump the view to minute 0 instead of the newest
  trials — the opposite of intent. Only panning leaves the live edge (decision 4).

## Consequences

- The full ghost arc of a longer reference session becomes scrollable once the
  overlay lands (ADR for that change extends the viewport's `total` to
  `max(current, reference)`).
- The viewport is pure view state: it feeds nothing into `build_traces` and can
  never perturb a run; the panel remains a read-only snapshot consumer (ADR-0050).
- One more tiny pure module to keep in step, but the follow/clamp arithmetic now
  lives in one headlessly-tested place instead of inline in the Tk redraw.

## Evidence / links

- `host/tests/test_trace_viewport.py`: whole-session (zoomed-out) case, narrow
  window panned by a scroll offset, start clamping (both ends), follow pinning to
  the right edge and tracking a growing session, and the empty-data unit-window
  fallback.
- Refines ADR-0114 (live Metrics tab); builds on ADR-0083 (pure-core/thin-shell)
  and ADR-0050 (published snapshot). Prerequisite for the past-session reference
  overlay (next ADR).
