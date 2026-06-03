#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
g++ -std=c++11 -Wall -Wextra -I.. test_task_core.cpp -o test_task_core
./test_task_core
