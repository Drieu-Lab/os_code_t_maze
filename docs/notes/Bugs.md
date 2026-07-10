# Bugs

Quick capture of things that are broken or wrong. One bullet each; rough is fine.
See [`README.md`](README.md) for how these get triaged.

Format: `- #YYMMDD_ShortName <what's wrong> [where, if known]`

Assign a unique `#YYMMDD_ShortName` handle once, date-first (e.g.
`#260710_DoubleReward`). The date prefix is the creation date, so no separate
date column is needed. Don't rename a handle after you've referenced it elsewhere
(commits, `future-work.md`) so those references stay valid.

## Open

<!-- Add newest at the top. Example:
- #260710_LickTunerFreeze Lick tuner trace freezes for ~1 s when the Rules tab arms [host/lick_tuner.py?]
-->

- #260710_SessionTimer The behavioral stats needs a session timer (start time, stop time, duration)

## Fixed (pending cleanup)

<!-- Move here once fixed; note the commit/ADR, then delete on the next pass. -->

