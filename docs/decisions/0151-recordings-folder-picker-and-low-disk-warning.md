# ADR 0151: Operator-pickable recordings folder + low-disk warning on the Session panel

- **Status:** Accepted
- **Date:** 2026-09-09
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/paths.py` (new `rig_prefs.json` accessors + `camera_output_dir` precedence); `host/tmaze/core/disk_space.py` (new pure helper); `host/tmaze/ui/session_controller.py` (disk readout + Storage dialog); `.gitignore`; tests `host/tests/test_paths.py`, `host/tests/test_disk_space.py`
- **Supersedes / Superseded by:** — (extends ADR-0088; builds on ADR-0049/0073/0079)

## Context

The rig writes every camera video and per-session Phase 3 bundle under one
recordings root (`<base>/phase3_recordings/<session_id>/...`; ADR-0049), and that
base is the single source of truth resolved by `tmaze.paths.camera_output_dir()`
(ADR-0088). Two operator-facing gaps remained:

- **No visibility into disk space.** Sessions record video, so the drive fills
  over a run of days. When it fills, writes fail (surfaced only after the fact by
  ADR-0121's write-failure banner). The operator had no *forward* warning that the
  drive was getting close to full before pressing Start.
- **No in-rig way to change where recordings go.** The base could only be moved by
  setting the `TMAZE_RECORDINGS_DIR` env var or editing the machine default in
  `paths.py`. The Records Manager has a persisted folder picker (ADR-0088), but it
  only sets that tool's *scan* root, not where `experiment_control` *writes* new
  sessions.

Both belong on the Rig-view Session panel, which is deliberately kept compact
because the Rig view already runs at the monitor's full height (ADR-0073/0079).

## Decision

Add a single new row to the Session panel (`SessionControlView`): an
**always-visible free-space readout** for the recordings drive plus a
**"Storage..." button** opening a small popup to change the recordings base
folder and the warning threshold.

- **Persisted rig prefs, one file, one writer.** A new git-ignored
  `host/rig_prefs.json` holds two keys, read/written through pure `tmaze.paths`
  (`read_prefs`/`write_prefs` merge-writer + typed accessors): `recordings_base`
  (the rig recordings base folder) and `low_disk_warn_gb` (the free-space warning
  threshold in GB). Kept in `paths` (not a UI module) so the single-source path
  resolver and the Session panel share one file, and headless tools read the same
  choice.
- **Recordings-base precedence: GUI pref > `TMAZE_RECORDINGS_DIR` env > machine
  default.** `camera_output_dir()` now consults `recordings_base_pref()` first, so
  a picked folder flows to the camera, the session bundles, and the offline tools
  (all already source from `paths`) on the next launch with no new hardcoded path.
  This mirrors the Records Manager's pref-first precedence (ADR-0088); the env var
  stays useful for tests/other machines that have no pref file.
- **Warning threshold operator-configurable, default 40 GB.** The default lives as
  the named constant `paths.DEFAULT_LOW_DISK_WARN_GB = 40.0` (documented here, so
  no magic number); the operator can change it in the Storage dialog. The readout
  turns red at/below the threshold.
- **Pure decision, thin shell.** `tmaze.core.disk_space.disk_status(free_bytes,
  warn_gb)` (stdlib-only, headless-tested) turns a raw free-byte count + threshold
  into the label text + is-low flag (unmeasurable drive -> "unknown", counts as
  low); the Tk shell measures via `shutil.disk_usage` on the nearest existing
  parent of the recordings root and paints the label, on a slow 30 s self-poll
  that runs even when no session is active.
- **Folder change is next-session, not mid-session.** Saving a new folder is
  refused while a session is active (bundles are mid-write); otherwise the live
  panel's `_recordings_root` is repointed immediately so the *next* session uses
  it without a restart. A threshold change is always allowed.

## Alternatives considered

- **Warn-only readout (show nothing until low).** Rejected: the operator asked for
  an always-visible figure so they can judge headroom before a long session, not
  just a last-minute alarm.
- **Fixed 40 GB threshold constant.** Rejected in favour of configurable: drive
  sizes and session lengths vary, and the value is cheap to persist next to the
  folder pref. 40 GB stays the default.
- **Reuse `records_manager.json` for the base.** Rejected: that pref stores the
  Records Manager *scan* root (a `phase3_recordings` folder), a different quantity
  from the *base*; overloading it would blur the two. A dedicated `rig_prefs.json`
  with a merge-writer keeps both keys independent.
- **Env-first precedence (env > pref).** Rejected for consistency with ADR-0088
  (GUI pref wins), so an operator's explicit in-app choice isn't silently
  overridden by a leftover env var; the env var still works when no pref is set,
  which is what tests rely on.
- **A full-width warning banner (like the camera).** Rejected: too heavy for the
  compact Session panel; a colored label matches the Records Manager welfare-flag
  pattern and adds only one row (ADR-0073/0079).

## Consequences

- The operator sees free space at a glance and gets a red warning before the drive
  fills, complementing ADR-0121's after-the-fact write-failure banner.
- Moving recordings no longer needs an env var or a code edit; the choice persists
  and, because everything sources from `paths`, the camera and offline tools follow
  on the next launch (the live session panel follows immediately for the next run).
- A mid-session folder change is deliberately blocked, so a bundle can't be split
  across drives.
- `paths.camera_output_dir()` is no longer a pure function of the environment (it
  reads a file). Tests isolate this by redirecting `paths.prefs_path()` to a temp
  file; the file is git-ignored and absent in CI, so env-based behaviour is
  unchanged there.

## Evidence / links

- Code: `host/tmaze/paths.py` (`prefs_path`/`read_prefs`/`write_prefs`,
  `recordings_base_pref`/`set_recordings_base`, `low_disk_warn_gb`/
  `set_low_disk_warn_gb`, `camera_output_dir` precedence), `host/tmaze/core/
  disk_space.py`, `host/tmaze/ui/session_controller.py` (`_update_disk_free`,
  `_schedule_disk_poll`, `_open_storage_dialog`).
- Tests: `host/tests/test_paths.py` (pref > env > default precedence + threshold
  round-trip), `host/tests/test_disk_space.py` (label/threshold/unknown).
- Extends ADR-0088 (single-source recordings root); builds on ADR-0049 (per-session
  tree), ADR-0073/0079 (compact full-height Rig view), ADR-0121 (write-failure
  banner).
