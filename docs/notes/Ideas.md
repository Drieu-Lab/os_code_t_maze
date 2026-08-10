# Ideas / future work

Quick capture of things to add or improve later. One bullet each; rough is fine.
See [`README.md`](README.md) for how these get triaged (real deferred code
changes graduate to [`../future-work.md`](../future-work.md)).

Format: `- #YYMMDD_ShortName <the idea> [area, if known]`

Assign a unique `#YYMMDD_ShortName` handle once, date-first (e.g.
`#260710_PerArmReward`). The date prefix is the creation date, so no separate
date column is needed. Don't rename a handle after you've referenced it elsewhere.

## Inbox

<!-- Add newest at the top. Example:
- #260710_PerArmReward Show reward count per arm on the Rig view tab [host/behavior_view.py?]
-->
- #260810_SolenoidVolumeCurve Measure a solenoid activation-duration vs volume-dispensed tuning curve, and characterize the central lick port specifically. Today reward_volume.py holds a single bench point (REWARD_UL_CALIBRATION = {300 ms: 60 uL}) and, with one point, assumes volume is proportional to duration (origin-anchored line) - the code comment already flags this as "roughly - but not exactly - linear". Take multiple (pulse_ms -> measured uL) points, add them as calibration rows (or fit a curve) so the assumption is data-backed rather than extrapolated. [reward calibration; host/tmaze/core/reward/reward_volume.py, ADR-0019]
- #260810_MultisensoryCue Trigger cues multisensorily using body sensors + camera/zone detection together (rather than one modality alone). Related characterization: measure the latencies between (a) body-sensor trigger, (b) first frame the animal enters the zone limits, (c) when it's determined to be "In Zone", and (d) when the cue is triggered ON/OFF. [cue triggering; relates to bug #260720_TrackingDelays and FW-0014]

## Promoted (pending cleanup)

<!-- Move here once it becomes an FW-NNNN entry; note the id, then delete on the next pass. -->
