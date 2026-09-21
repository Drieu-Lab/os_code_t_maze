/*
 * cue-guided-task.ino - imperative shell for the cue-guided T-maze task.
 *
 * Architecture: functional core + imperative shell.
 *   - task_core.h holds the pure decision logic (Session, step(), no I/O).
 *   - This .ino reads hardware into Inputs, calls step(), and performs the
 *     returned Effects (DAC tone, TTL pulses, valves, serial logging).
 *
 * Pin map and tunables live in rig_config.h, a symlink to the canonical
 * ../common/rig_config.h (see docs/decisions/0006). Serial output follows
 * protocol v9: every line is "<ms>,<TYPE>,<payload...>". See ../PROTOCOL.md.
 */

#include "rig_config.h"
#include "sound.h"        // DAC tone engine + TTLPulse + tone TTLs (owns tone TTL timing)
#include "task_core.h"    // pure core

#include <string.h>
#include <stdio.h>        // snprintf for the non-blocking line builders (proto v9)

// Arduino Due console: the Programming port (Serial -> UART0 -> ATmega16U2).
// CONSOLE replaces Serial throughout. Set DUE_USE_NATIVE_USB 1 for the Native USB
// port (SerialUSB) instead - but note that path wedges ~1030 s after the host
// opens it and only a RESET clears it, which is why the console lives here.
// Upload with the "Arduino Due (Programming Port)" board entry.
// see docs/decisions/0065
#if defined(__SAM3X8E__)
#ifndef DUE_USE_NATIVE_USB
#define DUE_USE_NATIVE_USB 0
#endif
#if DUE_USE_NATIVE_USB
#define CONSOLE SerialUSB
#else
#define CONSOLE Serial
#endif
#else
#define CONSOLE Serial
#endif

// Compile timestamp, reported by the VERSION command alongside FW_GIT_SHA so a
// bundle records which firmware built it even when the SHA is left "unknown".
// see docs/decisions/0193
#define FW_BUILD (__DATE__ " " __TIME__)

// ---------------- Pin tables (from rig_config.h) ----------------
const int sensorPins[BEAM_COUNT] = SENSOR_PINS;   // order matches Beam enum
const int lickPins[3]            = LICK_PINS;     // display/monitor only
// Full 15-solenoid table [port][R1,R2,R3,DRAIN,RINSE]; ports 0=left 1=right
// 2=center(start). Same layout as tmaze_test so SOL,<port>,<sol> matches.
const int solenoidPins[3][SOLENOIDS_PER_PORT] = SOLENOID_TABLE;

// ---------------- Choice / return-arm TTLs (shell-owned) ----------------
// (tone on/off TTLs are owned by sound.h, driven next to the DAC ISR.)
TTLPulse ttlChoice    = {PIN_TTL_CHOICE,     0, TTL_PULSE_MS, false};
TTLPulse ttlReturnArm = {PIN_TTL_RETURN_ARM, 0, TTL_PULSE_MS, false};

// ---------------- Shell state ----------------
TaskConfig cfg;
Session    session;
int8_t     gSeq[MAX_TRIALS];

// Non-blocking 15-solenoid pulse manager. Each valve can be scheduled with an
// optional start delay (onAt) so a sequence like rinse-then-drain runs without
// ever blocking loop(). Mirrors tmaze_test's capability minus its delay().
unsigned long solOnAt[3][SOLENOIDS_PER_PORT];   // scheduled turn-on (millis); valid while pending
unsigned long solOffAt[3][SOLENOIDS_PER_PORT];  // scheduled turn-off (millis); valid while active/pending
bool          solPending[3][SOLENOIDS_PER_PORT]; // waiting for its delayed start
bool          solActive[3][SOLENOIDS_PER_PORT];  // currently energized

// Sensor / lick debounce state
bool          sensRaw[BEAM_COUNT], sensStable[BEAM_COUNT];
unsigned long sensLastChange[BEAM_COUNT];
bool          lickRaw[3], lickStable[3];
unsigned long lickLastChange[3];

// Serial command accumulator. Sized to hold the longest command line; the
// v6 host-owned cue command carries an explicit frequency, e.g.
// "CUE_ON,RETURN,20000" (19 chars), so 16 bytes is no longer enough.
// see docs/decisions/0046
char    cmdBuf[32];
uint8_t cmdLen = 0;

// ---------------- Serial output (protocol v9) ----------------
// A stalled host TCP client can back-pressure the broker's serial reader, which
// fills the Due UART TX ring (SERIAL_BUFFER_SIZE, 128 B); a plain CONSOLE.print
// then BLOCKS inside readInputs() and delays loop() - and with it valves/TTLs.
// v9 makes every hot-path log line NON-BLOCKING: it is built into a local buffer
// and written only if it fits the TX ring right now (availableForWrite); on a
// shortfall the WHOLE line is dropped and counted (never a half-written line).
// txDropped is reported by STATUS (as a trailing <ms>,TXDROP,<n> line). Boot /
// handshake / SEQ-dump lines below deliberately stay blocking: they run at setup
// with an empty buffer and are provenance we must not drop. see docs/decisions/0193 (R2)
unsigned long txDropped = 0;

// Write a freshly snprintf'd line if it fits the TX ring, else drop + count.
// `n` is snprintf()'s return; a (never-expected) truncation is clamped so we
// still emit a bounded prefix rather than read past the buffer. Never blocks.
static void writeLineOrDrop(char* buf, int cap, int n) {
  if (n < 0) return;
  if (n >= cap) n = cap - 1;                       // truncated: emit the prefix
  if (n <= CONSOLE.availableForWrite()) {
    CONSOLE.write((const uint8_t*)buf, (size_t)n);
  } else {
    txDropped++;
  }
}

void printSL(unsigned long ms, const char* type, int idx, int state) {
  char buf[24];
  writeLineOrDrop(buf, sizeof(buf),
                  snprintf(buf, sizeof(buf), "%lu,%s,%d,%d\n", ms, type, idx, state));
}

void printLog(unsigned long ms, const Effect& e) {
  char buf[64];
  int n = snprintf(buf, sizeof(buf), "%lu,%s", ms, e.type);
  if (n < 0) return;
  if (e.a != FX_NONE && n < (int)sizeof(buf)) n += snprintf(buf + n, sizeof(buf) - n, ",%ld", (long)e.a);
  if (e.b != FX_NONE && n < (int)sizeof(buf)) n += snprintf(buf + n, sizeof(buf) - n, ",%ld", (long)e.b);
  if (e.c != FX_NONE && n < (int)sizeof(buf)) n += snprintf(buf + n, sizeof(buf) - n, ",%ld", (long)e.c);
  if (e.detail && e.detail[0] && n < (int)sizeof(buf)) n += snprintf(buf + n, sizeof(buf) - n, ",%s", e.detail);
  // Trailing numeric field after the detail (e.g. REWARD,CENTER,<dur_ms>). see docs/decisions/0019
  if (e.tail != FX_NONE && n < (int)sizeof(buf)) n += snprintf(buf + n, sizeof(buf) - n, ",%ld", (long)e.tail);
  if (n < (int)sizeof(buf)) n += snprintf(buf + n, sizeof(buf) - n, "\n");
  writeLineOrDrop(buf, sizeof(buf), n);
}

// Firmware-clock TWIN of an Open Ephys TTL mark: <ms>,TTL,<name>,1 emitted on the
// pulse RISING edge, using the same millis() that started the pulse (converted to
// the session clock). Only the rising edge is logged - that is the ephys-relevant
// mark; the pulse width is TTL_PULSE_MS. Lets offline alignment match TTL lines to
// TTL pulses without inferring through beams. see docs/decisions/0193 (S7)
void logTTL(unsigned long ms, const char* name) {
  char buf[24];
  writeLineOrDrop(buf, sizeof(buf),
                  snprintf(buf, sizeof(buf), "%lu,TTL,%s,1\n", ms, name));
}

// VERSION command reply: proto version + firmware build identity (git SHA pasted
// into rig_config.h when known, else "unknown") + compile timestamp, so a bundle
// records exactly which firmware produced it. see docs/decisions/0193
void handleVersion() {
  unsigned long t = millis() - session.startMs;
  char buf[64];
  writeLineOrDrop(buf, sizeof(buf),
                  snprintf(buf, sizeof(buf), "%lu,VERSION,%d,%s,%s\n",
                           t, PROTO_VERSION, FW_GIT_SHA, FW_BUILD));
}

// ---------------- Solenoids (non-blocking) ----------------
// Energize solenoid [port][sol] for `dur` ms, optionally starting `startDelay` ms
// from now (0 = immediately). Re-firing a valve simply reschedules its off-time.
void openSolenoid(int port, int sol, unsigned long dur, unsigned long startDelay = 0) {
  if (port < 0 || port > 2 || sol < 0 || sol >= SOLENOIDS_PER_PORT) return;
  unsigned long now = millis();
  if (startDelay == 0) {
    digitalWrite(solenoidPins[port][sol], HIGH);
    solPending[port][sol] = false;
    solActive[port][sol]  = true;
    solOffAt[port][sol]   = now + dur;
  } else {
    solPending[port][sol] = true;
    solActive[port][sol]  = false;
    solOnAt[port][sol]    = now + startDelay;
    solOffAt[port][sol]   = now + startDelay + dur;
  }
}

void updateValves() {
  unsigned long now = millis();
  for (int p = 0; p < 3; p++) {
    for (int s = 0; s < SOLENOIDS_PER_PORT; s++) {
      if (solPending[p][s] && (long)(now - solOnAt[p][s]) >= 0) {
        digitalWrite(solenoidPins[p][s], HIGH);
        solPending[p][s] = false;
        solActive[p][s]  = true;
      }
      if (solActive[p][s] && (long)(now - solOffAt[p][s]) >= 0) {
        digitalWrite(solenoidPins[p][s], LOW);
        solActive[p][s] = false;
      }
    }
  }
}

// Manual solenoid fire: R<port><sol>[,<ms>]. Handled entirely shell-side and
// independent of session.active (so the buttons work in any mode, even after a
// session completes), mirroring tmaze_test but NON-BLOCKING. Emits a SOL ack so
// the Rig view flashes the exact marker, and for the reward spout also
// REWARD,<port>,MANUAL for the behavioral log. A bare R<port> fires the reward
// spout (REWARD_SOLENOID_INDEX). see docs/decisions/0010
void handleManualSolenoid(const char* b) {
  if (b[0] != 'R' || b[1] < '0' || b[1] > '2') return;
  int port = b[1] - '0';
  int sol  = REWARD_SOLENOID_INDEX;
  if (b[2] >= '0' && b[2] <= '4') sol = b[2] - '0';
  unsigned long dur = REWARD_PULSE_MS;
  const char* comma = strchr(b, ',');
  if (comma && comma[1]) {
    long v = 0;
    for (const char* p = comma + 1; *p >= '0' && *p <= '9'; p++) v = v * 10 + (*p - '0');
    if (v > 0) dur = (unsigned long)(v > 5000 ? 5000 : v);
  }
  openSolenoid(port, sol, dur);
  unsigned long t = millis() - session.startMs;
  char buf[40];
  // Trailing <dur_ms> lets the host map the pulse to a calibrated volume. see docs/decisions/0019
  writeLineOrDrop(buf, sizeof(buf),
                  snprintf(buf, sizeof(buf), "%lu,SOL,%d,%d,%lu\n", t, port, sol, dur));
  if (sol == REWARD_SOLENOID_INDEX) {
    writeLineOrDrop(buf, sizeof(buf),
                    snprintf(buf, sizeof(buf), "%lu,REWARD,%d,MANUAL,%lu\n", t, port, dur));
  }
}

// ---------------- Host-driven positional cue (protocol v5/v6) ----------------
// The HOST owns the ON/OFF decision from the rat's tracked position; the firmware
// just makes the sound and (via startTone/stopToneAsync) fires the TONE_ON/OFF
// TTLs. The CUE line reflects the ACTUAL acoustic state (emitted only on a real
// start/stop, never a no-op). see docs/decisions/0044
//
// Command grammar: "CUE_ON,<CHOICE|RETURN>[,<hz>]". WITHOUT <hz> (v5) the firmware
// resolves tone identity itself (CHOICE from the current trial's correctArm,
// RETURN a random hi/lo from the session PRNG). WITH an explicit <hz> (v6) the
// host supplies identity directly - used by the host-owned Phase 3, where the
// firmware trial FSM is off (Free Roam) so correctArm is unset. The <hz> is
// clamped to [CUE_HZ_MIN, CUE_HZ_MAX] so a bad value can't drive the DAC out of
// range. see docs/decisions/0046
void handleCueOn(const char* arg) {
  // Split "<kind>[,<hz>[,<gain>]]" into the kind token, an optional frequency,
  // and an optional per-cue volume percent (v7). Fields are positional.
  char kind[8] = {0};
  uint8_t k = 0;
  const char* p = arg;
  while (*p && *p != ',' && k < sizeof(kind) - 1) kind[k++] = *p++;
  kind[k] = '\0';
  long hz = 0;
  long gainPct = CUE_GAIN_PCT_DEFAULT;
  if (*p == ',') {
    const char* q = p + 1;
    while (*q >= '0' && *q <= '9') { hz = hz * 10 + (*q - '0'); q++; }
    if (*q == ',') {                      // optional volume percent (v7)
      gainPct = 0;
      for (q = q + 1; *q >= '0' && *q <= '9'; q++) gainPct = gainPct * 10 + (*q - '0');
    }
  }

  uint16_t freq;
  const char* type;
  if (!strcmp(kind, "CHOICE")) {
    freq = cueFor(session.trial.correctArm, cfg);   // -1 arm (no trial) -> low-tone fallback
    type = "CHOICE";
  } else if (!strcmp(kind, "RETURN")) {
    session.rng = lcg(session.rng);                 // advance the threaded PRNG
    bool high = ((session.rng >> 16) & 1u);
    freq = high ? cfg.highToneHz : cfg.lowToneHz;
    type = "RETURN";
  } else {
    return;                                          // unknown cue kind: ignore
  }
  // Explicit host-supplied identity (v6) overrides the firmware default, clamped
  // to the configured tone band. see docs/decisions/0046
  if (hz > 0) {
    if (hz < CUE_HZ_MIN) hz = CUE_HZ_MIN;
    if (hz > CUE_HZ_MAX) hz = CUE_HZ_MAX;
    freq = (uint16_t)hz;
  }
  // Volume percent -> Q8 gain, clamped so it can only attenuate (v7). Rounded so
  // 100 maps exactly to full scale (TONE_GAIN_UNITY). see docs/decisions/0125
  if (gainPct < CUE_GAIN_PCT_MIN) gainPct = CUE_GAIN_PCT_MIN;
  if (gainPct > CUE_GAIN_PCT_MAX) gainPct = CUE_GAIN_PCT_MAX;
  uint16_t gainQ8 = (uint16_t)((gainPct * TONE_GAIN_UNITY + 50) / 100);
  if (tonePlaying) return;                           // already sounding: don't relog / retrigger
  unsigned long raw = millis();
  startToneAsync((float)freq, gainQ8);               // also raises the TONE_ON TTL
  unsigned long t = raw - session.startMs;
  char buf[40];
  writeLineOrDrop(buf, sizeof(buf),
                  snprintf(buf, sizeof(buf), "%lu,CUE,ON,%s,%d,%ld\n", t, type, (int)freq, (long)gainPct));
  logTTL(t, "TONE_ON");                              // firmware-clock twin of the TONE_ON TTL
}

void handleCueOff() {
  if (!tonePlaying) return;                          // nothing playing: don't pulse a spurious TTL
  unsigned long raw = millis();
  stopToneAsync();                                   // also raises the TONE_OFF TTL
  unsigned long t = raw - session.startMs;
  char buf[24];
  writeLineOrDrop(buf, sizeof(buf), snprintf(buf, sizeof(buf), "%lu,CUE,OFF\n", t));
  logTTL(t, "TONE_OFF");                             // firmware-clock twin of the TONE_OFF TTL
}

// ---------------- Serial command channel ----------------
Command parseCommand(const char* b) {
  if (!strcmp(b, "ABORT") || !strcmp(b, "STOP")) return Command{Command::Abort, 0};
  if (!strcmp(b, "PING"))   return Command{Command::Ping, 0};
  if (!strcmp(b, "STATUS")) return Command{Command::Status, 0};
  // MODE,FREE | MODE,FULL - switch training mode (and (re)start the session)
  if (!strcmp(b, "MODE,FREE")) return Command{Command::SetMode, (int8_t)Mode::FreeRoam};
  if (!strcmp(b, "MODE,FULL")) return Command{Command::SetMode, (int8_t)Mode::FullTask};
  // R<port><sol>[,<ms>] is NOT a core command: it is handled shell-side in
  // drainSerial() via handleManualSolenoid().
  return Command{Command::None, 0};
}

Command drainSerial() {
  Command last = {Command::None, 0};
  while (CONSOLE.available()) {
    char c = CONSOLE.read();
    if (c == '\r') continue;
    if (c == '\n') {
      cmdBuf[cmdLen] = '\0';
      if (cmdLen > 0) {
        // Manual solenoid fires are a shell concern (session-independent); all
        // other lines parse into a core Command.
        if (cmdBuf[0] == 'R' && cmdBuf[1] >= '0' && cmdBuf[1] <= '2') {
          handleManualSolenoid(cmdBuf);
        } else if (!strncmp(cmdBuf, "CUE_ON,", 7)) {
          handleCueOn(cmdBuf + 7);                 // host-driven cue on (v5/v6: optional ,<hz>)
        } else if (!strcmp(cmdBuf, "CUE_OFF")) {
          handleCueOff();                          // host-driven cue off (v5)
        } else if (!strcmp(cmdBuf, "CUE,VISION")) {
          cfg.cueSourceVision = true;              // host drives the cue; suppress beam auto-tone
        } else if (!strcmp(cmdBuf, "CUE,BEAM")) {
          cfg.cueSourceVision = false;             // firmware beam-driven auto-tone (v4 behavior)
        } else if (!strcmp(cmdBuf, "VERSION")) {
          handleVersion();                         // proto + fw build identity (v9)
        } else {
          Command k = parseCommand(cmdBuf);
          if (k.kind != Command::None) last = k;
        }
      }
      cmdLen = 0;
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    } else {
      cmdLen = 0;  // overrun: drop the line
    }
  }
  return last;
}

// ---------------- Inputs: debounce + edges + commands ----------------
Inputs readInputs() {
  Inputs in;
  unsigned long now = millis();
  unsigned long t = now - session.startMs;
  in.now = now;

  // Body beams via the NOYIYO opto (schematic "Normal Low / Actuated High"): at
  // the Due pin a broken beam reads HIGH and rest reads LOW, so `raw` is true on
  // a break. beam[i] is a RISING-edge (break) flag for the core. Corrected in
  // proto v8 - the read was `== LOW`, which reported the RESTING state as active
  // and pulsed the FSM/TTL on beam clear. see docs/decisions/0164
  for (int i = 0; i < BEAM_COUNT; i++) {
    bool raw = (digitalRead(sensorPins[i]) == HIGH);
    if (raw != sensRaw[i]) { sensRaw[i] = raw; sensLastChange[i] = now; }
    bool rising = false;
    if (raw != sensStable[i] && (now - sensLastChange[i]) >= SENSOR_DEBOUNCE_MS) {
      sensStable[i] = raw;
      printSL(t, "S", i, raw ? 1 : 0);
      if (raw) rising = true;
    }
    in.beam[i] = rising;
  }

  // Lick detectors (D12DAB6 via INPUT_PULLUP): the Due pin idles HIGH and is
  // pulled LOW on tongue contact (at the amp's current LO/DO setting), so `raw`
  // is true on contact. Monitor/display only (not fed to core). Corrected in
  // proto v8 - the read was `== HIGH`, which reported idle as active. see docs/decisions/0164
  for (int i = 0; i < 3; i++) {
    bool raw = (digitalRead(lickPins[i]) == LOW);
    if (raw != lickRaw[i]) { lickRaw[i] = raw; lickLastChange[i] = now; }
    if (raw != lickStable[i] && (now - lickLastChange[i]) >= SENSOR_DEBOUNCE_MS) {
      lickStable[i] = raw;
      printSL(t, "L", i, raw ? 1 : 0);
    }
  }

  in.tonePlaying = tonePlaying;
  in.cmd         = drainSerial();
  return in;
}

// ---------------- Apply described effects to hardware ----------------
void applyEffects(const Effects& fx) {
  unsigned long t = millis() - session.startMs;
  for (uint8_t i = 0; i < fx.n; i++) {
    const Effect& e = fx.items[i];
    switch (e.tag) {
      case FxTag::StartTone: {
        unsigned long raw = millis();
        startToneAsync((float)e.a);                // also raises the TONE_ON TTL
        logTTL(raw - session.startMs, "TONE_ON");  // firmware-clock twin
        break;
      }
      case FxTag::StopTone: {
        unsigned long raw = millis();
        stopToneAsync();                           // also raises the TONE_OFF TTL
        logTTL(raw - session.startMs, "TONE_OFF"); // firmware-clock twin
        break;
      }
      case FxTag::PulseChoice: {
        // One millis() for BOTH the pulse start and its serial twin (shared t, S7).
        unsigned long raw = millis();
        ttlChoice.startTime = raw; ttlChoice.active = true;
        digitalWrite(ttlChoice.pin, HIGH);
        logTTL(raw - session.startMs, "CHOICE");
        break;
      }
      case FxTag::PulseReturn: {
        unsigned long raw = millis();
        ttlReturnArm.startTime = raw; ttlReturnArm.active = true;
        digitalWrite(ttlReturnArm.pin, HIGH);
        logTTL(raw - session.startMs, "RETURN");
        break;
      }
      case FxTag::OpenValve:
        // Automatic task reward: the configured reward spout (R2) of the port.
        // Drive from cfg.rewardPulseMs so the fired duration matches the value the
        // core logs on the REWARD,CENTER line (both == REWARD_PULSE_MS). see docs/decisions/0019
        openSolenoid((int)e.a, REWARD_SOLENOID_INDEX, cfg.rewardPulseMs);
        break;
      case FxTag::CleanWell: {
        // Auto well-clean at trial start: rinse the central well now, then drain
        // after the rinse (non-blocking). Emit a SOL ack for each so the Rig view
        // flashes and the clean is in the log. e.a=rinse ms, e.b=drain ms.
        const int port = (int)V_START;   // central/start well
        openSolenoid(port, RINSE_SOLENOID_INDEX, (unsigned long)e.a, 0);
        openSolenoid(port, DRAIN_SOLENOID_INDEX, (unsigned long)e.b, (unsigned long)e.a);
        // Trailing <dur_ms> on each SOL line (rinse=e.a, drain=e.b). see docs/decisions/0019
        char buf[40];
        writeLineOrDrop(buf, sizeof(buf),
                        snprintf(buf, sizeof(buf), "%lu,SOL,%d,%d,%lu\n",
                                 t, port, RINSE_SOLENOID_INDEX, (unsigned long)e.a));
        writeLineOrDrop(buf, sizeof(buf),
                        snprintf(buf, sizeof(buf), "%lu,SOL,%d,%d,%lu\n",
                                 t, port, DRAIN_SOLENOID_INDEX, (unsigned long)e.b));
        break;
      }
      case FxTag::Log:
        printLog(t, e);
        break;
    }
  }
}

// ---------------- Config + seeding + startup dump ----------------
TaskConfig makeConfig() {
  TaskConfig c;
  c.mode               = DEFAULT_MODE;
  c.highToneIsLeft     = HIGH_TONE_IS_LEFT;
  c.startRewardEnabled = START_REWARD_ENABLED;
  c.maxTrials          = MAX_TRIALS;
  c.maxTimeMs          = MAX_TIME_MS;
  c.blockSize          = BLOCK_SIZE;
  c.maxSameRun         = MAX_SAME_RUN;
  c.autoDrainOnTrial   = AUTO_DRAIN_ON_TRIAL;
  c.rinseMs            = WELL_RINSE_MS;
  c.drainMs            = WELL_DRAIN_MS;
  c.rewardPulseMs      = REWARD_PULSE_MS;   // logged on REWARD,CENTER for host volume calc
  c.highToneHz         = TONE_HIGH_HZ;
  c.lowToneHz          = TONE_LOW_HZ;
  c.cueSourceVision    = DEFAULT_CUE_SOURCE_VISION;  // host-driven cue off by default. see docs/decisions/0044
  return c;
}

uint32_t makeSeed() {
  // Floating analog pin -> per-session entropy. CONFIRM A0 is unconnected.
  uint32_t s = 0;
  for (int i = 0; i < 32; i++) s = (s << 1) ^ (uint32_t)analogRead(A0);
  if (s == 0) s = 0xA5A5A5A5u;
  return s;
}

void logTrialSequence() {
  CONSOLE.print("0,SEQ_START,"); CONSOLE.println(cfg.maxTrials);
  for (int i = 0; i < cfg.maxTrials; i++) {
    CONSOLE.print("0,SEQ,"); CONSOLE.print(i + 1);
    CONSOLE.print(','); CONSOLE.println(gSeq[i] == 0 ? "LEFT" : "RIGHT");
  }
  CONSOLE.println("0,SEQ_END");
}

// Emit a session-boundary MODE marker (ms = 0 by convention, like the handshake).
void logModeLine(const char* name) {
  CONSOLE.print("0,MODE,"); CONSOLE.println(name);
}

// ---------------- Mode / session lifecycle ----------------
// Both helpers (re)initialize the session clock; switching mode at runtime via
// the MODE command starts a fresh session. FreeRoam has no trial sequence; the
// trial state machine is gated off in task_core's step(). see docs/decisions/0009
void startFreeRoam() {
  cfg.mode = Mode::FreeRoam;
  uint32_t seed = makeSeed();
  session = initialSession(millis(), seed);
  logModeLine("FREEROAM");
  session.startMs = millis();
}

// DEPRECATED (proto v9): the firmware's own trial FSM. Unused since the
// vision-driven, host-owned Phase 3 (ADR-0046) runs the task over Free Roam and
// only actuates through the firmware; the Full-task edges are beam-driven and
// polarity-sensitive (ADR-0164). Kept so old MODE,FULL commands don't break;
// scheduled for removal - see docs/future-work.md FW-0068. Do not build on it.
void startFullTask() {
  cfg.mode = Mode::FullTask;
  uint32_t seed = makeSeed();
  session = initialSession(millis(), seed);
  session.rng = generateSequence(gSeq, cfg, seed);
  CONSOLE.print("0,SEED,"); CONSOLE.println(seed);
  logTrialSequence();
  logModeLine("FULLTASK");
  session.startMs = millis();
}

// ---------------- Setup ----------------
void setup() {
  CONSOLE.begin(115200);
#if defined(__SAM3X8E__) && DUE_USE_NATIVE_USB
  while (!SerialUSB) { delay(10); }  // wait for the native USB CDC host to attach
#endif

  for (int i = 0; i < BEAM_COUNT; i++) {
    pinMode(sensorPins[i], INPUT_PULLUP);
    sensRaw[i] = (digitalRead(sensorPins[i]) == HIGH);  // break = HIGH (proto v8; see docs/decisions/0164)
    sensStable[i] = sensRaw[i];
    sensLastChange[i] = millis();
  }
  for (int i = 0; i < 3; i++) {
    pinMode(lickPins[i], INPUT_PULLUP);
    lickRaw[i] = (digitalRead(lickPins[i]) == LOW);  // contact = LOW (proto v8; see docs/decisions/0164)
    lickStable[i] = lickRaw[i];
    lickLastChange[i] = millis();
  }
  for (int p = 0; p < 3; p++) {
    for (int s = 0; s < SOLENOIDS_PER_PORT; s++) {
      pinMode(solenoidPins[p][s], OUTPUT);
      digitalWrite(solenoidPins[p][s], LOW);
      solPending[p][s] = false;
      solActive[p][s]  = false;
    }
  }

  pinMode(ttlChoice.pin,    OUTPUT); digitalWrite(ttlChoice.pin,    LOW);
  pinMode(ttlReturnArm.pin, OUTPUT); digitalWrite(ttlReturnArm.pin, LOW);
  pinMode(ttlToneOn.pin,    OUTPUT); digitalWrite(ttlToneOn.pin,    LOW);
  pinMode(ttlToneOff.pin,   OUTPUT); digitalWrite(ttlToneOff.pin,   LOW);

  setupSound();   // DAC + timer ISR

  cfg = makeConfig();

  // Startup protocol lines (ms = 0 by convention before the session clock starts)
  CONSOLE.print("0,PROTO,");  CONSOLE.println(PROTO_VERSION);
  CONSOLE.print("0,ANIMAL,"); CONSOLE.println(ANIMAL_ID);

  // Boot into the configured default mode. FreeRoam is the safe default: no
  // trials run until the experimenter selects Full task (MODE,FULL), so a
  // session never auto-starts on the wrong animal. see docs/decisions/0009
  if (cfg.mode == Mode::FullTask) startFullTask();
  else                            startFreeRoam();
}

// ---------------- Loop ----------------
void loop() {
  // Actuation FIRST: service pending valve-offs and TTL pulse-ends at the TOP of
  // every pass, before readInputs() (which drains serial and may write log lines).
  // A slow/blocked host link can back up those writes; doing the timers first means
  // a physical valve or an Open Ephys TTL edge is never delayed by more than one
  // loop pass. Repeated at the bottom so pulses started THIS pass are serviced too.
  // see docs/decisions/0193 (R2)
  updateTTLPulse(ttlToneOn);
  updateTTLPulse(ttlToneOff);
  updateTTLPulse(ttlChoice);
  updateTTLPulse(ttlReturnArm);
  updateValves();

  Inputs in = readInputs();   // also drains serial + prints S,/L, edges

  // Mode switch is a shell concern (it re-seeds, regenerates the sequence, and
  // re-dumps the handshake), so it is handled here rather than in the pure core.
  if (in.cmd.kind == Command::SetMode) {
    // A mode switch starts a fresh session; silence any tone the host (or a v4
    // beam cue) left playing so a new session never begins mid-tone. see docs/decisions/0044
    if (tonePlaying) stopToneAsync();
    if (in.cmd.arg == (int8_t)Mode::FullTask) startFullTask();
    else                                      startFreeRoam();
  } else {
    // Always step, even when the session is INACTIVE: step() services the
    // host-control commands (PING -> PONG, STATUS) regardless of session.active,
    // so the broker's liveness probe keeps working after a session completes.
    // With the session idle the trial FSM stays off (step() returns right after
    // the command block), so this is a cheap no-op when nothing is asked.
    // see docs/decisions/0123
    Effects fx; fx.n = 0;
    session = step(session, in, cfg, gSeq, fx);
    applyEffects(fx);
  }

  // A STATUS query also reports the non-blocking-write drop count as a trailing
  // shell line (kept separate from the pure core's STATUS effect). see docs/decisions/0193
  if (in.cmd.kind == Command::Status) {
    char buf[24];
    writeLineOrDrop(buf, sizeof(buf),
                    snprintf(buf, sizeof(buf), "%lu,TXDROP,%lu\n",
                             millis() - session.startMs, txDropped));
  }

  // Host-owned Phase 3 (docs/decisions/0046): with the trial FSM off (Free Roam)
  // the HOST owns the choice/return decisions, but the hardware event marks for
  // Open Ephys must still fire in real time. Pulse the choice / return-arm TTLs
  // directly on their beam edges here so neural alignment is preserved while the
  // FSM (which fires these via Effects in Full task) is inactive. No double-fire:
  // in Free Roam the FSM never reaches AwaitChoice/AwaitReturn.
  if (cfg.mode == Mode::FreeRoam) {
    if (in.beam[B_LCHOICE] || in.beam[B_RCHOICE]) {
      unsigned long raw = millis();               // shared t for the pulse + its twin (S7)
      ttlChoice.startTime = raw; ttlChoice.active = true;
      digitalWrite(ttlChoice.pin, HIGH);
      logTTL(raw - session.startMs, "CHOICE");
    }
    if (in.beam[B_LRETURN] || in.beam[B_RRETURN]) {
      unsigned long raw = millis();
      ttlReturnArm.startTime = raw; ttlReturnArm.active = true;
      digitalWrite(ttlReturnArm.pin, HIGH);
      logTTL(raw - session.startMs, "RETURN");
    }
  }

  updateTTLPulse(ttlToneOn);
  updateTTLPulse(ttlToneOff);
  updateTTLPulse(ttlChoice);
  updateTTLPulse(ttlReturnArm);
  updateValves();
}
