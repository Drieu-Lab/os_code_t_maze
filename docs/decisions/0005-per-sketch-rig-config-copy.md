# ADR 0005: Duplicate rig_config.h per sketch instead of a shared common/ header

- **Status:** Superseded
- **Date:** 2026-06-03
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/cue-guided-task.ino`, `firmware/cue-guided-task/sound.h`, `firmware/cue-guided-task/rig_config.h`, `firmware/tests/tmaze_test/tmaze_test.ino`, `firmware/tests/tmaze_test/rig_config.h` (removes `firmware/common/rig_config.h`)
- **Supersedes / Superseded by:** Superseded by [ADR-0006](0006-symlink-rig-config-per-sketch.md)

## Context

The rig wiring and task tunables lived in a single shared header,
`firmware/common/rig_config.h`, included from each sketch with a relative parent
path (`#include "../common/rig_config.h"` and `"../../common/rig_config.h"`).
This was intended as a single source of truth across the two sketches.

It does not compile in the Arduino IDE. The IDE/`arduino-cli` build copies only
the sketch folder into a temporary build directory and resolves includes from
there, so a `..` path that escapes the sketch folder points at a directory that
does not exist in the build tree:

```
cue-guided-task.ino:14:34: fatal error: ../common/rig_config.h: No such file or directory
```

The team's workflow is GUI-based ("Open the desired `.ino` in the Arduino IDE …
then upload", `firmware/README.md`), so build-time include flags are not a
convenient option.

## Decision

Drop `firmware/common/` and keep one `rig_config.h` inside each sketch folder,
included as `#include "rig_config.h"`:

- `firmware/cue-guided-task/rig_config.h`
- `firmware/tests/tmaze_test/rig_config.h`

The two copies are byte-identical (aside from a "(this file)" marker in the
header comment) and must be kept in sync by hand. Each copy's header comment
states this and points back to this ADR.

## Alternatives considered

- **Symlink the canonical header into each sketch folder** — preserves a true
  single source, but on Windows requires Developer Mode/admin to create and is
  fragile across fresh clones (`core.symlinks`); rejected for operator
  friction on the rig PC.
- **Package `common/` as an Arduino library (`#include <rig_config.h>`)** —
  preserves a single source, but needs a per-machine install into the Arduino
  libraries folder that isn't version-controlled with the sketches; rejected as
  too much setup for a two-sketch project.
- **Sync script that copies the canonical header into each sketch** — keeps one
  editable source, but adds a mandatory pre-compile step that's easy to forget;
  rejected in favor of the simplest thing that compiles in the IDE today.

## Consequences

- Sketches compile in the stock Arduino IDE with no per-machine setup.
- We give up the single-source-of-truth guarantee: the pin map / tunables now
  exist in two files that can drift. Mitigated only by the in-file warnings and
  this ADR. Any wiring or tunable change must be applied to **both** copies.
- If duplication becomes a maintenance problem, revisit with the library or
  symlink approach and supersede this ADR.

## Evidence / links

- Compile error reported on `firmware/cue-guided-task/cue-guided-task.ino:14`.
- Arduino build copies only the sketch folder, so `..` includes can't escape it.
- Refs ADR-0005.
