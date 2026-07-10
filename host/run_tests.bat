@echo off
REM ============================================================================
REM run_tests.bat  -  Run every host unit test in one command.
REM
REM   Discovers and runs all test_*.py modules in this folder (the pure-core
REM   suites: protocol, maze_state, task_events, rules_engine, well_reward,
REM   tracker_model). Run it from an Anaconda Prompt with the rig's conda env
REM   activated so numpy/OpenCV are importable (tracker/model tests skip
REM   gracefully when OpenCV is absent):
REM       conda activate drieu_t_maze
REM       run_tests.bat
REM
REM   Firmware logic has its own host-compiled tests; see
REM   ../firmware/cue-guided-task/test/README.md.
REM ============================================================================

cd /d "%~dp0"
python -m unittest discover -p "test_*.py" -v
