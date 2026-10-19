# head_models

The trained head network the live tracker uses when head tracking is switched
on (`tmaze.vision.head_live`; why and how: `docs/decisions/0219`).

- `head_net.onnx` - the small network (CNN, run with OpenCV; see
  [`docs/glossary.md`](../../docs/glossary.md)). Predicts head centre, neck,
  tail base, nose and both ears from a crop around the tracked blob.
- `head_net.json` - the crop settings it was trained with (the rig refuses a
  model whose settings don't match the code) plus its training record:
  `trained` date, the `sessions` it learned from, and the training settings.

Current model: rat 600 only (sessions `600_20260914_130417`,
`600_20260915_162930`), trained 2026-10-18.

To replace it, train with `tmaze.tools.train_head_net` (see `PROGRAMS.md`) and
copy the new `head_net.onnx` + `head_net.json` here together. The loaded
model's identity (file name, SHA-256 fingerprint, training date + sessions) is
available as `LiveHead.model_info`, so a session can record which model it ran
with and older sessions stay traceable after a swap.
