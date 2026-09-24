# ADR 0200: Fill format.json's firmware proto from a version reply that arrives after Start

- **Status:** Accepted
- **Date:** 2026-09-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/bundle_format.py` (new
  `backfill_firmware_proto`), `host/tmaze/core/session/session_config.py` (new
  `update_format_manifest`), `host/tmaze/ui/session_controller.py` (`apply` ->
  new `_backfill_format_manifest`; remembers the session's `format.json` path),
  tests in `test_bundle_format`, `test_session_config`, `test_session_phase3_wiring`
- **Supersedes / Superseded by:** —

## Context

Each bundle's `config/format.json` records `firmware_proto` (the firmware serial
protocol version) and the `sensor_polarity` it implies: whether a beam/lick `1`
means "broken/contact" (proto >= 8, `corrected`) or "resting" (older firmware,
`legacy_inverted`) (ADR-0164). The offline sync and the analysis read it to
interpret the S/L events (ADR-0199).

It is written once, at Session Start, from the last version the GUI has seen. The
GUI rarely has one at that moment:

- The Due only prints `0,PROTO,<n>` when it boots, which happens when the broker
  first opens the serial port (ADR-0194). The GUI connects to the broker over TCP
  afterwards and misses that line, unless the Due happens to reboot while it is
  connected.
- The GUI sends `VERSION` at Start (ADR-0193), but the reply
  `<ms>,VERSION,<proto>,<sha>,<build>` arrives a few ms later, after
  `format.json` is already on disk. `_backfill_firmware_provenance` folds that
  reply into the session manifest's provenance block, but nothing updated
  `format.json`.

So 24 of the 30 bundles recorded after the 2026-09-15 reflash say
`sensor_polarity: "unknown"` although the firmware answered, and the sync has to
infer the polarity from the event log (ADR-0199).

## Decision

When the firmware version becomes known during an active session, from the
`VERSION` reply or a late `0,PROTO` line, rewrite the session's `format.json`
with it, the same way the manifest provenance is already patched.

- **Pure rule** (`bundle_format.backfill_firmware_proto(manifest, proto)`): return
  a copy with `firmware_proto` and the derived `sensor_polarity` filled in, or
  `None` for no change. It only fills a manifest whose `firmware_proto` is null. A
  recorded value is never overwritten.
- **File write** (`session_config.update_format_manifest(path, patch)`): read,
  patch, write atomically (temporary file + rename). Best-effort: a missing or
  unreadable file is left alone and nothing raises. `format.json` is not in the
  config snapshot's SHA-256 index, so the rewrite leaves that integrity record valid.
- **Controller** (`_backfill_format_manifest`): runs on every `PROTO`/`VERSION`
  line, but only while a session is active. The path is cleared at each Start
  and set when the snapshot is written, so a failed snapshot can't point it at
  the previous session.

No format change: the fields and their meaning are the same, so `FORMAT_VERSION`
stays 4 and the analysis mirror needs nothing. `format.json` does not record
*when* it learned the proto. The manifest's `firmware_proto_seen` and the serial
log's `VERSION` line already show that.

## Alternatives considered

- **Wait for the reply before writing `format.json`.** Rejected: Start would
  block on the serial link, and pre-v9 firmware never replies, so it would need
  a timeout on every Start.
- **Regenerate the whole file with `format_manifest(firmware_proto=...)`.**
  Rejected: it would silently rewrite everything else too if the code changed
  mid-session. Patching just the two fields keeps the Start-time contract.
- **Add a `firmware_proto_source` field.** Rejected for now: a format change
  (version bump plus analysis mirror) for information the manifest and serial
  log already carry.
- **Fix only offline** (the sync's polarity inference, ADR-0199). That stays as
  the fallback for old bundles, but new bundles should declare their polarity.

## Consequences

- New sessions on v9 firmware record `firmware_proto: 9` / `corrected` in
  `format.json`, so the sync uses the declared polarity instead of inferring it.
- Bundles already recorded are unchanged. They keep `unknown`, and the sync's
  inference still covers them.
- Pre-v9 firmware never replies to `VERSION`; its bundles still say `unknown`
  unless a boot handshake was seen, exactly as before.
- If the Due reboots after Stop, the closed bundle is not touched.

## Evidence / links

- The gap: `session_controller._write_config_snapshot` writes `format.json` from
  `self._firmware_proto` before `_mark_session_start` sends `VERSION`;
  `_backfill_firmware_provenance` patched only the manifest.
- Count of `unknown` bundles: ADR-0199 survey (24 of 30 since 2026-09-15).
- Tests: `test_bundle_format.TestBackfillFirmwareProto`,
  `test_session_config` (`update_format_manifest` rewrite / no-op / missing file,
  index unchanged), `test_session_phase3_wiring.test_version_reply_after_start_fills_format_json`
  (real Start: `unknown` -> reply -> `corrected`; a PROTO after Stop leaves it).
- Related: ADR-0164 (polarity provenance), ADR-0193 (VERSION), ADR-0194
  (reset only on first serial open), ADR-0199 (sync reads per-bundle polarity).
