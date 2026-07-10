# docs

## Guides

- [`how-tracking-and-rules-work.md`](how-tracking-and-rules-work.md) - a
  plain-language, no-coding walkthrough of the camera pipeline: the blob
  tracker, the maze-state estimator, the task-event layer, and the rules
  engine, and how they chain together.
- [`maze_state_validation.md`](maze_state_validation.md) - the beam-vs-vision
  agreement / latency validation harness for the maze-state estimator.

## Notes

- [`notes/`](notes/) - the human-owned **capture inbox** for code bugs and ideas
  (`Bugs.md`, `Ideas.md`); openable as an Obsidian vault. Raw jottings that get
  triaged into [`future-work.md`](future-work.md) / ADRs (see ADR-0033). Distinct
  from the curated backlog in [`future-work.md`](future-work.md).

## Architecture diagrams

A Graphviz view of how the rig's programs interact at runtime (the closest
thing this repo has to `rqt_graph`, since it isn't a ROS system).

## Files

- `architecture.dot` - the graph source (nodes = processes/devices, edges =
  serial / TCP / function-call / TTL links). Edit this to keep the diagram
  current.
- `render_graph.py` - renders `architecture.dot` to an image.

## Install Graphviz

In your conda env (e.g. `drieu_t_maze`), the easiest path gets both the
Graphviz binaries and the Python bindings:

```
conda install -c conda-forge graphviz python-graphviz
```

(Equivalently: install the Graphviz CLI so `dot` is on PATH, or
`pip install graphviz` alongside a system Graphviz install.)

## Render

```
python docs/render_graph.py                 # -> docs/architecture.png
python docs/render_graph.py --format svg    # -> docs/architecture.svg
python docs/render_graph.py --open          # render then open the image
```

The script uses the `dot` CLI if it's on PATH, otherwise falls back to the
`graphviz` Python package; if neither is found it prints install instructions.

## Legend

- **ellipse** - a running process / node
- **3D box** - a hardware device (not a process)
- **solid orange edge** - USB serial link (protocol v2, newline-delimited ASCII)
- **bold blue edge** - TCP fan-out on `localhost:5555` (the telemetry "bus")
- **dashed edge** - in-process Python function call (or a command back to the Due)
- **dotted edge** - hardware TTL signal to Open Ephys (parallel to the serial wire)
