"""core - pure, stdlib-only, headless-testable decision logic.

What belongs here: modules with no Tkinter / OpenCV / numpy / pyserial imports,
each covered by a ``test_*.py``. Cores must not import from ``io``, ``vision``,
or ``ui`` (the boundary is checked by the architecture test in tests/).
"""
