@echo off
REM ============================================================================
REM start_rig.bat  -  Launch the T-maze rig: serial broker + lick tuner + control GUI.
REM   (rig_logger is no longer launched here - the session bundle now captures the
REM    full serial stream itself; see docs/decisions/0178, 0179. Run rig_logger by
REM    hand if you want the standalone backup log.)
REM
REM   PREREQUISITE: run this from an Anaconda Prompt with the rig's conda env
REM                 already activated, e.g.
REM                     conda activate drieu_t_maze
REM                     start_rig.bat
REM                 The three windows it spawns inherit this shell's PATH, so
REM                 they all use the env's python.exe (with pyserial, matplotlib,
REM                 etc. already installed).
REM
REM   - The broker owns COM3 (the Due's Programming port; see docs/decisions/0065)
REM     and exposes it on TCP localhost:5555.
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
if "%RIG_PORT%"=="" set "RIG_PORT=COM3"

REM ---- Tag every program we launch with one shared session id ----------------
REM Each python process below is started with "-X rigsid=%RIG_SID%". That option
REM is harmless (Python ignores unknown -X options and it never reaches argv) but
REM it shows up in the process command line, so the "Quit rig" button in
REM Experiment Control can find and close exactly the windows this launcher
REM opened (broker, lick tuner, control GUI) and nothing else.
REM See docs/decisions/0016.
set "RIG_SID=rig_%RANDOM%_%RANDOM%_%RANDOM%"

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

REM The broker/logger are launched as "python -m tmaze.io.<name>", so the tmaze
REM package must be installed in this env. Catch a missing install here too.
python -c "import tmaze" 1>nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: the 'tmaze' package is not installed in the current python.
    echo.
    echo Install it once per machine, from the repo root:
    echo     pip install -e .
    echo.
    pause
    exit /b 1
)

REM ---- Launch broker, then clients after a brief delay so the TCP port is up ----
REM Everything now launches as a package module (python -m tmaze.<layer>.<name>),
REM so the tmaze package must be installed once per machine (pip install -e . from
REM the repo root) and the commands work regardless of the current directory.
start "Rig Broker" cmd /k python -X rigsid=%RIG_SID% -m tmaze.io.rig_broker --port %RIG_PORT%
timeout /t 2 /nobreak >nul

REM start "Maze GUI"   python -m tmaze.ui.maze_gui
start "Lick Tuner" python -X rigsid=%RIG_SID% -m tmaze.ui.lick_tuner
start "Experiment Control" python -X rigsid=%RIG_SID% -m tmaze.ui.experiment_control

endlocal
