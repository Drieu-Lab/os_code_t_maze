"""tests - the headless unit-test suite for the tmaze package (ADR-0083).

Every module imports the code under test via its installed package path
(``import tmaze...``), so the suite needs ``pip install -e .`` once per machine
and runs from any directory. Discover it from ``host/``:

    python -m unittest discover -s tests -t .

or run one module: ``python -m unittest tests.test_protocol``.
"""
