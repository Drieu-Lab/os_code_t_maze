# firmware - Arduino sketches

Sketches that run on the Arduino Due. Each sketch lives in its own folder named
to match the `.ino` file, as the Arduino IDE requires.

- `cue-guided-task/` - the behavioral task firmware (auditory-cue T-maze).
  Uses `sound.h` for DAC-based frequency sweeps; emits trial/sensor/reward
  events over Serial and TTL pulses to Open Ephys.
- `tmaze_test/` - hardware bring-up / diagnostic sketch. Reports body- and
  lick-sensor edges over Serial and accepts simple `R<port><sol>` commands to
  pulse solenoids; pairs with the `host/` tools for rig validation.

Open the desired `.ino` in the Arduino IDE, select the Arduino Due board and
its serial port, then upload.
