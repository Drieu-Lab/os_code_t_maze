@echo off
REM ============================================================================
REM start_rig.bat  -  Launch the T-maze rig: serial broker + maze GUI + lick tuner.
REM
REM   PREREQUISITE: run this from an Anaconda Prompt with the rig's conda env
REM                 already activated, e.g.
REM                     conda activate drieu_t_maze
REM                     start_rig.bat
REM                 The three windows it spawns inherit this shell's PATH, so
REM                 they all use the env's python.exe (with pyserial, matplotlib,
REM                 etc. already installed).
REM
REM   - The broker owns COM4 and exposes it on TCP localhost:5555.
REM   - The two GUIs are TCP clients of the broker, so both can run at once.
REM
REM   Override the serial port from the command line if needed, e.g.
REM       start_rig.bat COM5
REM
REM   Launcher + external conda activation rationale: see docs/decisions/0004
REM ============================================================================

setlocal
cd /d "%~dp0"

set "RIG_PORT=%~1"
if "%RIG_PORT%"=="" set "RIG_PORT=COM4"

REM ---- Pre-flight: confirm required packages are importable in this shell ----
REM Catches the "forgot to activate the conda env" case here, in one place,
REM rather than letting all three windows pop and fail individually.
python -c "import serial, matplotlib" 1>nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: cannot import 'serial' and/or 'matplotlib' with the current python.
    echo.
    echo This usually means the conda env is not activated in this shell.
    echo From an Anaconda Prompt, run:
    echo     conda activate drieu_t_maze
    echo     start_rig.bat
    echo.
    pause
    exit /b 1
)

REM ---- Launch broker, then GUIs after a brief delay so the TCP port is up ----
start "Rig Broker" cmd /k python rig_broker.py --port %RIG_PORT%
timeout /t 2 /nobreak >nul

REM start "Maze GUI"   python maze_gui.py
REM start "Lick Tuner" python lick_tuner.py
start "Experiment Control" python experiment_control.py

endlocal
