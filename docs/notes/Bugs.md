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
- #260720_TrackingDelays Rat tracking gets less reliable (more often loses tracking confidence) and/delayed (the determination of when a rat enters a zone is variable; physically the rat could be well into the zone before the determination is made that animal has entered it; this will affect the spatial consistency of when audio cues are presented if a cue is to be triggered when the rat enters a zone) if the blocker is in view and being tracked. This reliability/delay issues also exacerbate if I start video recording and also once the upgrade to the multi-blob based rat tracking was implemented.
  - [investigation 2026-07-21] Not one cause but three: (1) tracker per-frame CPU on the grab thread - blocker+clustering was doing a redundant second detect+cluster pass every frame the tape is visible (worse *even far from the blocker*), dropping source frames -> broken association -> coasting; **reduced** via single-pass containment-drop (ADR-0039) + a `track: avg/peak ms` readout in the Camera tab to watch it. (2) Recording competes for CPU cores (H.264 all-cores) - see FW-0013 (NVENC/core cap). (3) Zone-entry lateness is mostly architectural (poll interval x min_dwell_ticks floor, ADR-0025), not CPU - see FW-0014. Structural root (tracking inline on the grab thread) tracked in FW-0012. Keep open pending on-rig `track_ms`/`src-drop` data.
  - [update 2026-07-22] Structural root (FW-0012) **implemented**: tracking moved off the grab thread onto a newest-frame worker + publisher (ADR-0040). On-rig confirms the decoupling - with the blocker tracked and recording on, per-frame `process()` runs ~3.2->4.4 ms but `src-drop` stays flat while a new `track-skip` counter absorbs the rare worker lag (~0.1% of ~10.5k frames), so tracking cost no longer drops source frames. Remaining facets stay open: recording CPU contention (FW-0013 - deferred, ample headroom at current load) and the architectural zone-entry latency floor (FW-0014). Still pending `maze_state_validate.py` latency/jitter numbers before closing the "delayed zone-entry" facet.
- #260720_BehaviorStatError 
- #260720_DataLogging 
- #260720_DataVideoSync 
## Fixed (pending cleanup)

<!-- Move here once fixed; note the commit/ADR, then delete on the next pass. -->

