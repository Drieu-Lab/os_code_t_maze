@echo off
cd /d "%~dp0"
where g++ >nul 2>nul
if errorlevel 1 (
    echo ERROR: g++ not found on PATH. Install MinGW/MSYS2 or run on a machine with g++.
    exit /b 1
)
g++ -std=c++11 -Wall -Wextra -I.. test_task_core.cpp -o test_task_core.exe
if errorlevel 1 exit /b 1
test_task_core.exe
