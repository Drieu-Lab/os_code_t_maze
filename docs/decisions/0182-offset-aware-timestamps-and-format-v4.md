# ADR 0182: Offset-aware host timestamps + coordinated format v4

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/timeutil.py` (new), `host/tmaze/core/session/session_log.py` / `session_config.py` / `session_summary.py`, `host/tmaze/ui/session_controller.py` / `phase3_controller.py` / `camera_view.py`, `host/tmaze/io/rig_logger.py`, `host/tmaze/core/records/records_changelog.py`, `host/tmaze/tools/build_session_registry.py`, `host/tmaze/core/phase3/phase3_log.py` / `excursion_log.py` / `bundle_format.py` (`FORMAT_VERSION` 3→4), the golden fixture, and the analysis mirror (`drieu_lab_analysis`: `trajectories.parse_iso`, `phase3_format.FORMAT_VERSION`, `bundle_data_dictionary`)
- **Supersedes / Superseded by:** — (builds on ADR-0124 one-anchor clocks, ADR-0128 iso↔host_unix_s, ADR-0164 firmware_proto/sensor_polarity, ADR-0181 camera clock facts)

## Context

Every host-side ISO 8601 timestamp the rig wrote used `datetime.now().isoformat()`
— a **naive** local wall-clock string with no UTC offset (review finding **S9**).
That is only unambiguous on a machine in the rig's own timezone. The video
sidecar's `host_unix_s` is POSIX epoch seconds (an absolute instant); to align a
CSV event with a frame the analysis converts the ISO string with `.timestamp()`,
and on a **naive** datetime that conversion is interpreted in the **reader**
machine's local zone. A bundle copied off-rig and analysed elsewhere then
mis-aligns by the zone offset — silently.

Three smaller bundle-fidelity gaps were queued behind the same format bump:

- **P3** — the trial CSV recorded the nominal cue tones (`high_tone_hz`/`low_tone_hz`)
  but not the tones *as played* (the commanded Hz + gain % resolved from
  `cue_calibration.json`), so a reader couldn't tell what actually came out of the
  speaker.
- **L9** — the excursions CSV lacked the `session_t0` header block the trial CSV
  carries, so it didn't self-anchor to the shared session zero.
- **S5/S6** — the format manifest had no prose warning a reader off the two easy
  camera-clock mistakes (trusting the mp4's nominal timestamps; treating a sidecar
  `track_*` row as computed on that frame), and the sidecar had no way to measure
  grab→write processing latency.

## Decision

- **One offset-aware stamp helper.** New `tmaze/core/timeutil.py` exposes
  `now_iso(timespec="milliseconds")` = `datetime.now().astimezone().isoformat(...)`,
  which attaches the host's current UTC offset (incl. DST). **Every** host ISO stamp
  now routes through it: manifest `t0`/`t_end` (`clock_snapshot`), trial + excursion
  `*_iso`, the serial receipt stamp (`fromtimestamp(recv_wall).astimezone()`), the
  summary/config `generated_iso`, notes, `rig_logger`, the records change log, and the
  session-registry `built_at`. The stamps now read e.g. `2026-09-19T13:12:00.123-04:00`.
- **P3 — cue-calibration header line.** `phase3_controller._resolve_cue_calibration()`
  resolves the HIGH/LOW commanded Hz + gain % from `cue_calibration.json` and passes
  them into `Phase3Log`, which writes
  `# cue_calibration high_cmd_hz=… high_gain_pct=… low_cmd_hz=… low_gain_pct=…`.
- **L9 — excursions anchor.** `ExcursionLog` takes `session_t0` and writes the same
  `# session_t0_iso/_wall/_mono` block as the trial CSV.
- **S5/S6 — manifest notes + sidecar column.** `bundle_format` gains a `stream_notes`
  block (camera_video nominal-rate caveat; sidecar `track_*` join caveat; grab-vs-write
  `host_unix_s`), and the sidecar gains a `host_unix_s_written` column (wall time at
  row write) so grab→write latency is measurable against the grab-time `host_unix_s`.
- **`FORMAT_VERSION` 3 → 4.** (The 2→3 step was consumed by ADR-0164.) The analysis
  mirror bumps its guard to 4 and gains `trajectories.parse_iso(series, assume_tz=
  "America/New_York")`: v4+ offset-aware stamps parse to the true instant natively;
  **pre-v4 naive** stamps are localised to the **rig** zone (default JHU/Baltimore,
  overridable) rather than the reader's local zone. The data dictionary's unit token
  changes `iso8601_local` → `iso8601_offset`. The golden fixture is regenerated with
  offset-aware stamps (Jan 2026 → `-05:00` EST) and the P3 header line.

## Alternatives considered

- **Convert to UTC (`…Z`).** — Offset-aware local is more faithful to how the rig
  operator reads the clock (and DST is captured in the offset); UTC would be equally
  unambiguous but loses the local reading. `astimezone()` keeps both.
- **Store timezone only in the manifest, leave stamps naive.** — A single manifest
  field can't disambiguate per-stamp DST transitions across a long session and is easy
  to miss; making each stamp self-describing is robust and local to one helper.
- **Default `assume_tz` to the reader's local zone.** — That reproduces the S9 bug for
  old bundles analysed off-rig. Localising to the RIG zone is the correct fixed point;
  it's a per-call override for the rare non-Baltimore rig.
- **Bump `format_version` per sub-change (P3, L9, S5/S6).** — They are all additive and
  land together; one coordinated bump keeps the mirror in step with a single guard change.

## Consequences

Bundles are now timezone-portable: an event ISO stamp maps to one absolute instant no
matter where it's analysed, so cross-stream (video ↔ serial ↔ ephys) alignment is
correct off-rig. Trial CSVs record the tones as played; excursions self-anchor; the
manifest warns readers off the camera-clock pitfalls; grab→write latency is measurable.
Costs: the analysis mirror needs the IANA tz db (stdlib `zoneinfo` + `tzdata` on Windows)
to localise **old** naive bundles — absent it, `parse_iso` degrades to the pre-v4
reader-local behaviour for those bundles only (v4+ stamps never need it). Existing
change-log / registry rows are untouched (append-only); only new rows carry the offset.

## Evidence / links

- Review finding S9 (+ P3/L9/S5/S6) and Increment 3e in
  `sensor_polarity_hand_check_5b3798c1.plan.md`.
- Clocks contract: ADR-0124; iso↔host_unix_s: ADR-0128; camera clock facts: ADR-0181.
- Analysis mirror: `drieu_lab_analysis/src/tmaze_analysis/trajectories.py::parse_iso`,
  `phase3_format.FORMAT_VERSION == 4`.
- Tests: `host/tests/test_session_phase3_wiring.py` (offset-aware serial stamp),
  `host/tests/test_records_changelog.py`, analysis
  `tests/test_analysis_trajectories.py::TestIsoToUnix` (offset-aware + naive→rig-zone).
