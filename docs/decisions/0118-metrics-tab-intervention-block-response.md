# ADR 0118: Metrics-tab intervention-block highlight + in-block response overlay

- **Status:** Accepted
- **Date:** 2026-08-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_traces.py` (`BlockSpan` gains an
  in-block response trajectory), `host/tmaze/ui/phase3_traces_view.py` (kind-coded
  block shading, in-block lines, colour key, In-block toggle),
  `host/tmaze/ui/tooltip_text.py`, `host/tests/test_phase3_traces.py`, `PROGRAMS.md`,
  `docs/glossary.md`
- **Supersedes / Superseded by:** refines ADR-0114 (the live traces) and
  ADR-0090 (the debias intervention blocks it visualises)

## Context

On the live Metrics tab (ADR-0114) the accuracy, strategy, and bias traces are
computed only over **out-of-block** completed trials — a trial is excluded while
`sched_state == INTERVENTION` (a corrective debias/alternation block; ADR-0090)
or it is an unrewarded probe (ADR-0115). This is deliberate: those blocks *force*
the arm, so folding them into the free-behaviour rates would corrupt them. The
side effect is a **gap** in those traces across (and for up to a full rolling
window after) every block — the ITI trace, which has no such gate, stays
continuous through it. An operator watching a real session read the gap as "what
happened here?" and, seeing only a single faint purple shade, couldn't tell a
debias block from an alternation one, nor how the animal actually responded while
coerced. The forced-choice trials *are* recorded (`chosen_arm`, `outcome`,
`block_kind`); we just weren't surfacing them.

## Decision

Surface the block episode without polluting the free-behaviour lines:

1. **In-block response is computed in the pure core, per block.** `BlockSpan`
   (in `phase3_traces.py`) now carries, alongside `start_t/end_t/kind`, the
   in-block trajectory: `xs` (each in-block trial's x) plus **cumulative-from-
   block-start** `compliance` (running fraction that went to the *forced* arm —
   in a block the forced arm is the scored `correct_arm`, so a correct trial ==
   complied) and `pleft` (running in-block P(left)). Cumulative, not windowed
   against the free series, because a coerced block is a self-contained episode.

2. **The view overlays that response, colour-coded by block KIND.** Each block is
   shaded behind the traces in a per-kind colour (`side_bias` → blueviolet,
   `alternation` → dark orange; unrewarded probe keeps its teal), with a small
   colour **key** on the top subplot (a second legend, so it doesn't replace the
   trace legend). An **In-block** toggle overlays the response line — cumulative
   compliance on the Accuracy subplot, in-block P(left) on the Strategy subplot —
   one segment per block in the block's colour. The free-choice lines still gap
   over the block on purpose. A **reference (ghost)** session's blocks are shaded
   with a **hatch** instead of a solid fill, so past episodes read distinctly.

## Alternatives considered

- **Fill the gap by including in-block trials in the existing accuracy/strategy
  lines.** Rejected: it conflates forced and free behaviour and silently corrupts
  the very measures the operator reads for learning — the exclusion is the point
  (ADR-0090). The gap is information ("free behaviour undefined here").
- **A single per-block summary marker (like the probe diamonds) instead of a
  line.** Rejected as the primary form (the operator asked for the *response*, a
  trajectory) — though the cumulative line degrades gracefully to a couple of
  points for very short blocks.
- **Keep one shade colour, just make it darker.** Rejected: it can't distinguish
  a debias block from an alternation block, which is exactly what the operator
  needed to see.
- **Compute the in-block rates in the view.** Rejected: the view only has the
  reduced `TraceResult`; putting the arithmetic in the pure core keeps it
  headlessly testable and consistent with the rest of `build_traces`.

## Consequences

- The Metrics tab now reads block episodes at a glance: which kind, how long, and
  whether the animal complied / which way it was pushed — while the free-behaviour
  lines stay honest (still gapping).
- `BlockSpan` grew three fields (defaulted, so existing readers/tests are
  unaffected); the extra per-trial work is O(block length) and trivial.
- Colour is now load-bearing on this tab; the key documents the mapping. If more
  block kinds are added (e.g. a future automatic probe trigger), extend
  `_BLOCK_KIND_COLORS`/`_LABELS`.
- Ghost hatching assumes at most one reference overlay; if multiple ghosts are
  ever supported the hatch alone won't disambiguate them.

## Evidence / links

- Core + view + tooltip + tests in this change; unit tests
  `test_block_carries_inblock_response_trajectory` /
  `test_block_trajectory_empty_when_no_block` in `host/tests/test_phase3_traces.py`.
- Builds on ADR-0114 (live traces), ADR-0090 (debias blocks), ADR-0115 (probe
  blocks), ADR-0116 (viewport), ADR-0117 (reference overlay).
