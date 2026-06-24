# Decision log (ADRs)

This folder holds **Architecture Decision Records** — short, numbered,
immutable notes that capture *why* a non-obvious choice was made, not just
*what* changed. Git history already records the what/when/who; ADRs record the
reasoning, the alternatives considered, and the evidence, so a decision can be
understood (and re-justified or revisited) long after it was made.

On a behavioral rig this matters: timing constants, thresholds, reward volumes,
and protocol framing have scientific consequences, and the justification for a
value is often more important than the value itself.

## When to write an ADR

Write one when the *reasoning* isn't self-evident from the diff, e.g.:

- A protocol change (framing, message format, version bump).
- A timing / threshold / volume constant whose value is a deliberate choice.
- An architectural decision (process split, ownership of a resource, transport).
- Anything where a future reader would reasonably ask "why was it done this way?"

Skip ADRs for obvious, mechanical, or purely cosmetic changes.

## How to add one

1. Copy [`template.md`](template.md) to `NNNN-short-title.md`, where `NNNN` is
   the next zero-padded number (e.g. `0004-...`).
2. Fill in every section. Be concrete: name the constant and its file, link the
   commit/PR, and point to any bench data or scope photos (these may live in the
   `os_code_t_maze_resources` repo).
3. Set **Status** to `Accepted` once the change lands.
4. Add a row to the index below.
5. Reference the ADR from the code (`// see docs/decisions/0004`) and from the
   commit/PR body (`Refs ADR-0004`).

## Immutability

ADRs are append-only. Don't rewrite the reasoning in an existing ADR. If a
decision changes, write a **new** ADR, set the old one's status to
`Superseded`, and cross-link the two via the *Supersedes / Superseded by* field.
The chain of records is the audit trail.

## Index

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-serial-broker-tcp-fanout.md) | Use a single-owner serial broker with localhost TCP fan-out | Accepted | 2026-06-01 |
| [0002](0002-transparent-line-passthrough.md) | Keep the broker a transparent verbatim line passthrough | Accepted | 2026-06-01 |
| [0003](0003-broker-only-clients-no-serial-fallback.md) | Make GUI clients broker-only (no direct-serial fallback) | Accepted | 2026-06-01 |
| [0004](0004-launcher-external-conda-activation.md) | Start the rig via start_rig.bat, with conda activation done externally | Accepted | 2026-06-01 |
| [0005](0005-per-sketch-rig-config-copy.md) | Duplicate rig_config.h per sketch instead of a shared common/ header | Superseded | 2026-06-03 |
| [0006](0006-symlink-rig-config-per-sketch.md) | Symlink the shared rig_config.h into each sketch folder | Accepted | 2026-06-03 |
| [0007](0007-reward-at-central-port.md) | Deliver reward at the central port (v1 cue-guided task) | Accepted | 2026-06-03 |
| [0008](0008-wavetable-tone-isr.md) | Generate the tone cue from a wavetable, not sin() in the ISR | Accepted | 2026-06-03 |
| [0009](0009-runtime-training-modes-and-logging.md) | Runtime training modes (Free Roam / Full task), anti-bias sequencing, persistent logger | Accepted | 2026-06-23 |
| [0010](0010-manual-solenoid-control-in-task.md) | Full manual solenoid control + auto well-clean at trial start in cue-guided-task | Accepted | 2026-06-24 |
