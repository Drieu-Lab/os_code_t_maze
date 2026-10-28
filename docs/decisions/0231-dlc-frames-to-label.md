# ADR 0231: Which frames to label for the next DeepLabCut retraining (`dlc_pick_frames`)

- **Status:** Accepted
- **Date:** 2026-10-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/dlc_frame_pick.py` (new), `host/tmaze/tools/dlc_pick_frames.py`
  (new), `host/tmaze/core/dlc_head.py` (the head file's `join_key` header text)
- **Supersedes / Superseded by:** — (reads the live head file of
  [ADR-0224](0224-dlc-live-head-preview.md); the model it feeds is the one of
  [ADR-0227](0227-offline-dlc-over-all-sessions.md) / [ADR-0230](0230-dlc-head-as-task-position.md))

## Context

New lighting went in on 2026-10-27 (two lamps added at the reward-well end, the
junction-end lamps turned down). Comparing the live DeepLabCut answers of one
session per rat per day (10-22, 10-23, 10-26 old lighting; 10-27 new):

- Rats 600 and 601 lost 7-8 points of head-centre likelihood >= 0.6 at the
  well side (97-98% -> 90-91%), and 600 also in the return arms
  (about 89% -> 67%, few frames). The day-to-day spread before was 1-2 points.
- Only the head parts fell (nose, ears, head centre); neck, spine and tail
  were unchanged. So the model still finds the rat but not its head.
- Rat 602 was unchanged or better. An earlier reading that 602 often had its
  head > 100 px from the blob centre (13-22% of frames) was a join error:
  `cam_block_id` restarts at 0 after 65535, 602's sessions are 92-96k frames,
  and joining on the raw id paired frames ~44 min apart. Joined on the
  unwrapped id, 602 is at 0.01-0.03%, like the others.

A small retraining (iteration 2) is planned once 2-3 sessions have run under
the final lighting; it then needs frames to label.

## Decision

`python -m tmaze.tools.dlc_pick_frames` picks them from the live head file:

- **Candidate frame**: the blob tracker is locked on (`TRACKING`), the head
  centre is below 0.6, and at least one body part (neck, mid-spine, tail
  base) is at or above 0.6 - the failure the lighting caused.
- **Not** "lowest likelihood first" over all frames: a first trial on
  `600_20261027_152320` picked frames where no part was found at all, and
  those were an operator's arm at the well and the rat being lifted out.
- **Regions** from the blob centre and the session's `zones.json`: default the
  well side (HUB) and the return arms, where the drop was; 5 per region per
  session, lowest head likelihood first, at least 2 s apart.
- **Output**: PNGs named as DeepLabCut names labelled frames
  (`img<frame index>.png`, padded to the video's frame-count digits) plus
  `picks.csv`. It does not write into the DLC project: adding the frames and
  the video to the project is a deliberate step of the retraining.
- The two files are joined on `cam_block_id` with restarts counted
  (`unwrap_block_ids`); the head file's header now says so.

## Alternatives considered

- **DeepLabCut's own `extract_outlier_frames`**: it needs DeepLabCut's
  analysis output for each video (the offline full model, not the live answers
  that showed the drop) and doesn't know the maze regions.
- **Random frames in the regions**: most would be frames the model already gets
  right.
- **Pick from the offline `dlc_best_iter1` results**: those come from a
  different input (the full frame, not the 448 px crop the live model sees);
  the drop was measured on the live answers.

## Consequences

- About 10 frames per session; 2-3 sessions per rat give the 20-30 per rat
  planned for labelling.
- A frame whose body was also missed is never picked, so a failure that loses
  the whole rat would need another rule.
- After retraining, the offline pass is rerun as `dlc_best_iter2` (the
  version guard of ADR-0227 refuses the old name).

## Evidence / links

- Analysis scripts (outside the repos):
  `t-maze_recordings/_dlc_lighting_eval/compare.py` (join fixed to count the
  restarts), `head_far_602.py`, `head_far_602_crop.py`, `check_wrap.py`.
- Trial run, 2026-10-28, `600_20261027_152320`: 10 frames in 20 s; head centre
  0.00 with mid-spine 0.90-0.99 on all; e.g. frame 1938 is the rat at the well,
  head down against the black floor.
- Tests: `host/tests/test_dlc_frame_pick.py`.
