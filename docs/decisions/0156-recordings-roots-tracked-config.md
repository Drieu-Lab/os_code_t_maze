# ADR 0156: Make the recordings roots tracked project config

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/recordings_roots.json` (new, tracked), `host/tmaze/paths.py`
  (`read/write_recordings_roots_config`, `scan_primary_root`,
  `archives_env_override`, `phase3_recordings_archives`,
  `phase3_recordings_roots`), `host/tmaze/ui/records_manager.py` (Browse /
  Archives write the config; one-time pref migration; Generate passes no
  `--root`), `host/tmaze/tools/build_session_registry.py` (docs),
  `host/tests/test_paths_roots.py`
- **Supersedes / Superseded by:** — (extends ADR-0088 and ADR-0113)

## Context

The experiment's session bundles live in more than one place: the rig PC's
recordings folder (39 bundles) and a second drive the older sessions were moved
to when the disk filled, `Z:\Ravi\phase3_recordings` (73 bundles). ADR-0113
introduced the idea of *archive roots* scanned in addition to the primary, but
left WHERE that list lives split three ways:

- the command-line registry tool read the `TMAZE_RECORDINGS_ARCHIVES` env var;
- the Records Manager kept its own `recordings_root` / `recordings_archives` in
  the git-ignored `host/records_manager.json` and passed them as `--root`;
- the past-session reader (ADR-0117) used the env var path.

So on the same machine a GUI "Generate files" and a terminal
`python -m tmaze.tools.build_session_registry` could scan DIFFERENT folder sets,
and the registry would silently shrink or grow depending on which was run last.
The 2026-09-11 records review found exactly that: the committed registry indexed
a subset of the ~110 real sessions because the archive drive was configured in
neither place. Worse, the pref file is git-ignored, so a fresh clone or a second
lab machine had no way to learn that Z: exists.

The set of folders holding the experiment's history is a fact about the
*project*, not a per-machine preference. It belongs beside the code, versioned,
so every tool and every machine reads the same list.

## Decision

1. **A tracked config file, `host/recordings_roots.json`**, is the single source
   of the scan roots:

   ```json
   {"schema_version": 1, "primary": null, "archives": ["Z:\\Ravi\\phase3_recordings"]}
   ```

   `primary` is the folder scanned first; `null` means "the folder the rig
   writes to" (`tmaze.paths.phase3_recordings_root`, which stays a per-machine
   setting per ADR-0151), so the scan follows the rig by default and only a
   deliberate override freezes a path. `archives` are the read-only folders older
   sessions were moved to. A `_schema` note in the file explains all this and
   records the **drive-letter fragility**: `Z:\...` only resolves on a machine
   with that letter mapped; a UNC path (`\\server\share\...`) is accepted and
   sturdier. We keep the drive-letter form for now because that is how the drive
   is mounted on the rig PC; the note is there so whoever hits "0 sessions on Z:"
   knows why.

2. **`tmaze.paths` reads it** (`read_recordings_roots_config`, best-effort:
   absent/malformed = no archives) and **every tool resolves the same list** via
   the existing `phase3_recordings_roots()` = `[scan_primary_root(), *archives]`,
   de-duplicated. The Records Manager's Generate passes **no** `--root` any more,
   so a GUI build and a CLI build are guaranteed identical.

3. **The env var becomes an override, not the store.** `TMAZE_RECORDINGS_ARCHIVES`,
   when *set* (even to blank = "no archives"), replaces the config's list for that
   process only - for tests and for a machine that genuinely differs. The GUI
   shows "[archives from TMAZE_RECORDINGS_ARCHIVES]" in its recordings bar when
   that is happening, so an operator is never puzzled by an edit that seems not
   to take.

4. **The GUI edits the file.** Browse... writes `primary` (stored as `null` when
   the pick IS the rig's write folder), Archives... writes `archives`; both via
   `write_recordings_roots_config`, an atomic write that preserves the `_schema`
   note. The status line reminds the operator to commit the file with the code.
   A one-time migration at startup moves any old `recordings_root` /
   `recordings_archives` pref keys into the config (only into empty slots, so a
   committed config is never overwritten by a stale pref) and deletes the keys.

## Alternatives considered

- **Keep the env var as the store and document it.** Rejected: an env var is
  invisible, per-user, per-shell; it is what let the GUI and CLI drift, and a
  new clone cannot discover it.
- **Keep the pref file, make the CLI read it too.** Rejected: still git-ignored,
  so the archive location is lost on a fresh clone or a second machine; and
  "which machine's pref is right" has no answer.
- **Auto-discover roots** (scan all drives for `phase3_recordings` folders).
  Rejected: slow, surprising (a backup copy would be indexed twice), and it
  hides the decision instead of recording it.
- **Store a UNC path now.** Deferred: the share's UNC name was not verified from
  the rig PC at the time; switching is a one-line edit to the file when it is.

## Consequences

- Fresh clones and other lab machines get the archive list for free; a change
  to where sessions live is a reviewed commit, not a lost setting.
- The GUI now writes a TRACKED file. That is deliberate (the same reasoning as
  ADR-0155's Commit records), but it means an un-committed edit to
  `recordings_roots.json` can sit in the working tree; the status line says so.
- `phase3_recordings_roots()` now includes Z: by default, so tools that used to
  see only the rig folder (registry build, past-session reader) see the whole
  history. The next registry build will roughly TRIPLE its row count; Phase 3b/3c
  of the hardening plan add the shrink guard and provenance header so a build
  that *loses* sessions is refused rather than silently accepted.
- The offline analysis repo (`drieu_lab_analysis`, `phase3_format`) still
  resolves roots from the env vars only; it does not read this file (it must not
  import `tmaze`). Until it grows a reader for the same JSON, analysis runs on a
  machine without the env var see only the rig folder - noted for the
  data-contract catch-up in Phase 3c (`docs/records-schema.md`).
- Revisit if the roots ever differ legitimately per machine (e.g. a laptop with
  its own partial copy); a per-machine *additional* list could then be layered
  on top of the tracked one.

## Evidence / links

- Bundle counts at decision time: `C:\Users\Celine\Documents\t-maze_recordings\phase3_recordings`
  39 folders, `Z:\Ravi\phase3_recordings` 73 folders (2 of the 112 are the
  ignored junk sessions of ADR-0097).
- ADR-0088 (scan/output split), ADR-0113 (archive roots), ADR-0117
  (past-session reader), ADR-0151 (rig write folder as a machine pref),
  ADR-0155 (records history; the review that surfaced the drift).
- Records Manager hardening plan, Phase 3a.
