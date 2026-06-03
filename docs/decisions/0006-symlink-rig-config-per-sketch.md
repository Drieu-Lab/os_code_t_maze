# ADR 0006: Symlink the shared rig_config.h into each sketch folder

- **Status:** Accepted
- **Date:** 2026-06-03
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/common/rig_config.h` (canonical), `firmware/cue-guided-task/rig_config.h` (symlink), `firmware/tests/tmaze_test/rig_config.h` (symlink), repo `core.symlinks` setting
- **Supersedes / Superseded by:** Supersedes [ADR-0005](0005-per-sketch-rig-config-copy.md)

## Context

ADR-0005 made each sketch keep its own byte-identical copy of `rig_config.h`
because Arduino's GUI build copies only the sketch folder into its temp build
directory and cannot follow a `../common/...` include out of it. That fixed the
compile error but gave up the single source of truth: the pin map / tunables
lived in two files that could silently drift — a real hazard for wiring and
timing constants on a behavioral rig.

We want one editable file again, while still letting each sketch include a plain
`"rig_config.h"` so it compiles in the stock Arduino IDE.

## Decision

Keep one real file, `firmware/common/rig_config.h`, and place a **symlink**
named `rig_config.h` in each sketch folder pointing at it:

- `firmware/cue-guided-task/rig_config.h`   -> `..\common\rig_config.h`
- `firmware/tests/tmaze_test/rig_config.h`  -> `..\..\common\rig_config.h`

Relative targets keep the links portable. The Arduino build follows the link
when copying the sketch, so each sketch still includes a local `"rig_config.h"`.
The repo sets `core.symlinks = true`; git stores the links with mode `120000`
(verified), so a clone recreates them rather than checking out path-text files.

## Alternatives considered

- **Per-sketch duplicate copies (ADR-0005)** — compiles with zero setup, but two
  files drift; rejected now that single source is restorable via symlinks.
- **Package `common/` as an Arduino library (`#include <rig_config.h>`)** — keeps
  one source but needs a per-machine install into the libraries folder that
  isn't versioned with the sketches; rejected as heavier than a symlink.
- **Sync script copying the canonical header** — keeps one editable source but
  adds a mandatory, forgettable pre-compile step; rejected.

## Consequences

- One editable file; edits propagate to every sketch automatically — no drift.
- Per-machine requirement: creating/checking out the symlinks needs **Developer
  Mode** (or admin) on Windows. On this machine, `New-Item -ItemType
  SymbolicLink` (PowerShell 5.1) still demanded admin even with Developer Mode;
  `cmd /c mklink` succeeded unprivileged. Use `mklink` to recreate links.
- Fragility on fresh clones: if a machine lacks Developer Mode/privileges or has
  `core.symlinks=false`, git checks the link out as a small text file containing
  the target path and the Arduino build then fails to find the header. Recovery:
  enable Developer Mode, `git config core.symlinks true`, and re-checkout (or
  recreate the links with `mklink`).
- If this friction proves too costly on lab PCs, revisit by superseding this ADR
  (e.g. back to copies or to a library).

## Evidence / links

- Symlink capability check: Developer Mode flag
  `AllowDevelopmentWithoutDevLicense = 1`; `cmd /c mklink` returned success while
  PowerShell `New-Item -ItemType SymbolicLink` failed with "Administrator
  privilege required".
- `git ls-files --stage` shows both `rig_config.h` links at mode `120000`.
- Refs ADR-0006. Supersedes ADR-0005.
