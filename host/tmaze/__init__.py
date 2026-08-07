"""tmaze - PC-side control & monitoring tools for the T-maze rig.

The former flat ``host/`` scripts, organized into layers that mirror the
"pure core + thin shell" convention (see CONTRIBUTING.md):

- ``core``   - pure, stdlib-only, headless-testable decision logic.
- ``io``     - serial broker / TCP client / process helpers (hardware transport).
- ``vision`` - OpenCV/numpy tracking + calibration.
- ``ui``     - Tkinter shells (the experiment-control tabs and widgets).
- ``tools``  - standalone runnable utilities.

See docs/decisions for the ADR that records this layout and its placement rule.
"""
