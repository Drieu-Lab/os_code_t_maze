# task_core host tests

`task_core.h` is pure (only `<stdint.h>`, no Arduino), so it can be compiled and
run on a PC. These tests exercise the trial state machine without any hardware.

## Run

Linux / macOS:

```
./run_tests.sh
```

Windows (from a shell with g++ on PATH, e.g. MSYS2/MinGW):

```
run_tests.bat
```

Or directly:

```
g++ -std=c++11 -I.. test_task_core.cpp -o test_task_core
./test_task_core
```

Success prints `ALL TESTS PASSED` and exits 0; a failed `assert` aborts.

## What is covered

- `cueFor()` high/low-tone mapping for both `highToneIsLeft` values.
- Balanced (10L/10R) per-block sequence generation.
- A full correct trial: entry -> cue -> exit -> choice -> reward -> return -> advance.
- Incorrect choice yields no reward.
- Simultaneous left+right choice beams resolve to exactly one choice (double-eval regression).
- `ABORT` ends the session, after which `step()` is a no-op.
- Session timeout and max-trials both end the session.
- Exhaustive check that no `(phase x inputs x command)` combination overflows `FX_CAP`.
