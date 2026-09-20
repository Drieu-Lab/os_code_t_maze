# firmware - Arduino sketches

Sketches that run on the Arduino Due. Each sketch lives in its own folder named
to match the `.ino` file, as the Arduino IDE requires.

- `cue-guided-task/` - the behavioral task firmware (auditory-cue T-maze).
  Uses `sound.h` for a DAC-based constant tone; emits trial/sensor/reward
  events over Serial and TTL pulses to Open Ephys. Real sessions run in **Free
  Roam** with the task owned by the host (vision-driven Phase 3, ADR-0046); the
  firmware's own **Full-task** FSM (`MODE,FULL`) is **deprecated** and scheduled
  for removal at the next reflash (see `PROTOCOL.md` and future-work FW-0068).
- `tmaze_test/` - hardware bring-up / diagnostic sketch. Reports body- and
  lick-sensor edges over Serial and accepts simple `R<port><sol>` commands to
  pulse solenoids; pairs with the `host/` tools for rig validation.

Open the desired `.ino` in the Arduino IDE, select the Arduino Due board and
its serial port, then upload.
