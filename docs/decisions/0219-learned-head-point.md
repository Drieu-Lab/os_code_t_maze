# ADR 0219: Track the head with a small learned network, leap-guarded and smoothed only while still

- **Status:** Accepted
- **Date:** 2026-10-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/vision/head_learned.py` (`HeadNet`, `TARGETS`,
  `head_direction_deg`), `host/tmaze/core/head_filter.py` (`HeadFilter`,
  `HeadFilterParams`), `host/tmaze/tools/head_point_dataset.py`,
  `host/tmaze/tools/train_head_net.py`, `host/tmaze/tools/head_point_eval.py`;
  the live tracker wiring and recording format follow in later changes
- **Supersedes / Superseded by:** —

## Context

The tracker reports the rat's position as the centre of mass (CoM) of its
blob. For hooded (black-headed) Long-Evans rats on the black rig, the head is
often missing from the blob, so the CoM sits mid-body: on the hand-labelled
DeepLabCut frames it is a median 12.6 cm from the head centre, and nearer the
tail than the neck on 75% of them. Zone entries and the cue trigger are
therefore late and body-centred. The lab also wants the head *direction* for
analysis. DeepLabCut-Live (DLC-Live) was the stated long-term goal.

The candidates were compared on the 9 hand-labelled sessions of the DLC
project `drieu_rat_model-Ravi-2026-10-07` (20 frames each, three rats) and,
frame by frame, against DLC's predictions on two whole rat-600 videos
(74,086 and 77,494 frames), using `head_point_eval`. Speed was measured on the
rig PC (i9-14900K, RTX 4060 Ti).

## Decision

1. **Head point = a small convolutional network** (see
   [glossary](../glossary.md#convolutional-neural-network-cnn)) run on a 64 x 64
   crop around the CoM (grey image, difference from the background, lightly
   cleaned blob mask; `head_learned.crop_input`). It predicts six points: head
   centre (mean of nose and ears, as DLC labels define it), neck, tail base,
   nose, right ear, left ear. It is trained in the `DEEPLABCUT` env on DLC's
   predictions (every 2nd frame with >= 2 head parts at likelihood >= 0.6,
   skipping 2 s around hand-labelled frames so those stay unseen), exported
   to [ONNX](../glossary.md#onnx) and run on the rig with OpenCV's `cv2.dnn`,
   so the rig needs no new package. Mirrored training images swap the ear
   labels. The model's crop settings and target list are saved beside it and
   checked at load, so a mismatched model is refused, not misread.
2. **Head direction = neck -> head centre**, in degrees in image axes (0 =
   image right, 90 = image down; see [glossary](../glossary.md#head-direction)).
3. **A filter between the network and what is recorded** (`HeadFilter`):
   - *Leap guard:* a head centre more than `max_step_px = 40` full-frame px
     (about 9 cm in one 40 ms frame, 2.3 m/s, faster than a rat moves) from
     the last accepted raw head is held as invalid until `confirm_frames = 3`
     frames in a row agree within `agree_px = 15`. The check uses the last
     *raw* head, not the smoothed one, so smoothing lag cannot trigger it.
   - *Still-only smoothing:* points are blended (`still_alpha = 0.5`) only
     while the raw head moves less than `still_px = 3` px per frame; a
     moving head is not smoothed.
   - The direction is computed from the smoothed neck and head centre.
   - After `max_gap_frames = 5` frames with no prediction it starts fresh.

## Alternatives considered

Accuracy on the hand-labelled frames (median distance to the head centre;
wrong end = nearer the tail base than the neck):

| Method | Median | p90 | Wrong end | Time per frame |
| --- | --- | --- | --- | --- |
| CoM (current) | 12.6 cm | 17.4 cm | 75% | — |
| Shape rule (skeleton + HeadTracker, `head_point`) | 2.7 cm | 7.9 cm | 5% | ~1-2 ms |
| Ridge regression on shape features | 3.6 cm | 9.3 cm | 7% | < 0.1 ms |
| **Network** | **1.7-1.9 cm** | **4.1-4.6 cm** | **1%** | **0.3-0.6 ms** |
| DLC-Live, full size, GPU | ~1 cm (DLC's own test) | | | 70 ms |

- **Keep the CoM.** Rejected: wrong end on most frames.
- **The shape rule** (medial axis of the blob plus motion and continuity
  votes). Usable, and needs no training, but its error tail is long and its
  point jumps 85-103 times a minute over a whole video, against 0.3-0.4 for
  the network. It stays as the comparison baseline in `head_point_eval`.
- **Ridge regression** on the shape rule's measurements. Rejected: it can
  only rescale the shape rule's answer; no better than the rule itself.
- **DLC-Live now.** Rejected for the live tracker: the lab's ResNet-50
  multi-animal model takes 671 ms per frame on the CPU and 70 ms on the GPU
  at full size (14 fps, under the camera's 25 fps). Sizes that fit in 40 ms
  lose the head on 40-100% of frames because the model was trained at full
  size. Half precision crashed in DLC-Live 1.1.0's multi-animal predictor,
  and its identity predictor had to be registered by hand. A smaller
  single-animal DLC model might fit; that is future work.
- **Neck -> nose as the head direction.** Same median error (10.5 vs 11.1
  deg against the labelled neck -> nose), but off by more than 45 deg on 11%
  of frames instead of 4%: the nose is the hardest part to place on a black
  head. Ear midpoint -> nose was worse (p90 57 deg).
- **Fixed smoothing of every frame.** Rejected: it makes the point trail a
  running rat. With a weight of 0.6 the p90 error against DLC rose from 9.5
  to 13-14 px, and when the guard compared against the smoothed point, the
  lag alone held 6-8% of frames as leaps (0.7% without smoothing).

## Consequences

- On the whole rat-600 videos the filtered head point is valid on 99.3% of
  predicted frames, a median 4.8-4.9 px (1.1 cm) from DLC's head centre
  (p90 9.4-9.6 px), with 0.48 px of jitter while still (DLC's own: 0.65).
  The direction is a median 12-13 deg from DLC's, off by more than 45 deg on
  3-4% of frames, with about one sudden flip a minute, as often as DLC's own.
  These videos are the network's training videos, so these figures are
  optimistic; the hand-labelled figures above are the fair test.
- The network has only seen rat 600. On rats 601 / 602 it is close
  (median 1.6 / 2.0 cm vs 1.5 cm), with rat 602 weakest (one wrong-end
  frame in 44; direction median 16.5 deg vs 11-12). Retrain on all three
  rats when their DLC predictions arrive; the recorded model file name keeps
  old and new sessions distinguishable.
- The model is a trained file that must ship with the code (`host/head_models/`);
  changing the crop or the target list means retraining.
- The filter constants are in full-frame pixels at the current camera
  resolution and 25 fps; a different camera, crop or frame rate needs them
  rechecked with `head_point_eval`.
- This ADR does not move the task onto the head point: zones, cue and reward
  still use the CoM until a separate position-source decision.

## Evidence / links

- Commits `a7174f7`, `3cf9716`, `db5fb36`, `03d1aad` (shape rule and
  evaluator), `ef39636`, `4dc9f87` (learned models, dataset, trainer),
  `9f15067` (nose / ears, head direction), `4c81499` (whole-video direction
  scoring).
- Run outputs (outside the repos) in
  `t-maze_recordings/_head_point_eval/`: `learned_labelled/` (per-session
  table), `video_hd/a`, `video_hd/b` (whole videos), `models_hd/` (the
  trained model), `dlclive_bench.py` (the DLC-Live timing script).
