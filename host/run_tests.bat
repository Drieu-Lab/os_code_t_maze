@echo off
REM ============================================================================
REM run_tests.bat  -  Run every host unit test in one command.
REM
REM   Discovers and runs all test_*.py modules under host\tests\ (the pure-core
REM   suites: protocol, maze_state, task_events, rules_engine, well_reward,
REM   tracker_model, ...). Run it from an Anaconda Prompt with the rig's conda env
REM   activated so numpy/OpenCV are importable (tracker/model tests skip
REM   gracefully when OpenCV is absent):
REM       conda activate drieu_t_maze
REM       run_tests.bat
REM
REM   The tests import the installed package (import tmaze...), so it must be
REM   installed once per machine (pip install -e . from the repo root; ADR-0083).
REM
REM   Firmware logic has its own host-compiled tests; see
REM   ../firmware/cue-guided-task/test/README.md.
REM ============================================================================

cd /d "%~dp0"

REM The tests import the installed package (import tmaze...), so it must be
REM installed in this env once per machine (pip install -e . from the repo root).
python -c "import tmaze" 1>nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: the 'tmaze' package is not installed in the current python.
    echo Install it once per machine, from the repo root:
    echo     pip install -e .
    echo.
    pause
    exit /b 1
)

python -m unittest discover -s tests -t . -p "test_*.py" -v
