# ADR 0179: Retire rig_logger from the session launch (bundle self-captures)

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/start_rig.bat` (drops the `rig_logger` launch + the rat-id prompt), `PROGRAMS.md` (rig_logger reframed as an optional hand-run backup; launcher note), `host/README.md` (manual-start list), `docs/future-work.md` (FW-0067)
- **Supersedes / Superseded by:** —

## Context

`rig_logger.py` is a headless broker client that records EVERY firmware line to
`host/data/<animal>_<time>.csv`. Since the rig's early days it was launched
automatically by `start_rig.bat`, and the launcher prompted for a rat id purely
to name that file (`--animal`). It served two jobs at once:

1. **The record** - the only complete verbatim serial log of a session.
2. **The live monitor** - the operator watched its console scroll to confirm the
   animal was licking the central well (drinking) during a run.

ADR-0178 made the session bundle capture the COMPLETE verbatim stream itself
(`serial/serial_<id>.csv`, host-receipt-stamped and fsynced). That removes job
(1): the always-on external logger is now redundant for the *record* - the
bundle is self-contained. Job (2), the live console, has no in-GUI replacement
yet.

Keeping `rig_logger` in the launch after 4a means: a second process writing a
duplicate full log to `host/data/` (an out-of-bundle file), a rat-id prompt that
blocks the launcher on every start solely to name that now-redundant file, and
one more window in the `RIG_SID` "Quit rig" set (ADR-0016).

## Decision

**`rig_logger` is no longer part of the session launch.** `start_rig.bat` starts
the broker + lick tuner + experiment control only; the "Enter rat id for the log
filename" prompt is removed with it (its only consumer was the logger's
`--animal`). `rig_logger` stays a first-class, hand-runnable tool
(`python -m tmaze.io.rig_logger [--animal <id>]`) - an optional standalone backup
/ diagnostic - documented as such in `PROGRAMS.md` and `host/README.md`.

- **Live monitoring in the meantime:** `lick_tuner` (still launched) shows lick
  edges, so the operator is not blind to licking while the dedicated trace is
  built. A proper at-a-glance central-well lick trace in the main GUI is captured
  as **FW-0067** (the deferred "open design item" from the cluster-4 plan).

## Alternatives considered

- **Keep launching `rig_logger` until the GUI lick trace exists.** Rejected
  (operator decision, 2026-09-19): the bundle already captures the full record,
  and `lick_tuner` covers live licks in the interim, so keeping the redundant
  duplicate log + the blocking prompt buys little. FW-0067 tracks the real
  replacement.
- **Drop only the rat-id prompt, keep the logger launch.** Rejected: the prompt
  exists solely to name the logger file; keeping the logger keeps the duplicate
  out-of-bundle log this ADR is removing.
- **Delete `rig_logger` entirely.** Rejected: it is still useful by hand (a
  no-GUI capture, a cross-check against the bundle, bring-up), and other docs /
  the mapping runbook still reference it as an optional `--serial` source for
  pre-0178 bundles.

## Consequences

- One-click start no longer blocks on a prompt and spawns one fewer window; no
  duplicate full log lands in `host/data/` during a normal session.
- The complete serial record still exists - inside the bundle (ADR-0178) - so
  nothing about the *data* is lost by dropping the launch.
- A temporary monitoring gap (no scrolling console) is bridged by `lick_tuner`
  until FW-0067 lands.
- Shell/docs-only change; no core or test changes. `rig_logger` itself is
  untouched and still works when run by hand.

## Evidence / links

- Follows `docs/decisions/0178` (bundle captures the full serial stream);
  cluster-4 plan Increment 4c ("`start_rig.bat` drops the animal prompt +
  rig_logger launch").
- `docs/decisions/0016` (RIG_SID window tagging for "Quit rig"), `0009`
  (`rig_logger`), `0056` (single-parse fan-out the future trace subscribes to).
- Deferred replacement: `docs/future-work.md` FW-0067 (live central-well lick
  trace in the GUI).
- Code/docs: `host/start_rig.bat`, `PROGRAMS.md`, `host/README.md`.
