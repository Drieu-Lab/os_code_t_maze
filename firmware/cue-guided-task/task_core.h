#ifndef TASK_CORE_H
#define TASK_CORE_H

/*
 * task_core.h - pure functional core of the cue-guided T-maze task.
 *
 * This header depends ONLY on <stdint.h> (no Arduino.h, no globals, no I/O,
 * no millis()). It models the task as an immutable Session value plus a pure
 * transition:
 *
 *     Session step(const Session&, const Inputs&, const TaskConfig&,
 *                  const int8_t* seq, Effects& fx);
 *
 * step() returns the next Session and appends described Effects to `fx`; the
 * imperative shell (the .ino) reads inputs and performs the effects. Because
 * nothing here touches hardware, the whole core compiles and unit-tests on a
 * PC (see test/test_task_core.cpp).
 */

#include <stdint.h>

/* Sentinel meaning "this numeric log field is absent" (so the shell can format
 * variable-arity lines like `ms,CHOICE,LEFT` vs `ms,TRIAL,3,LEFT`). */
static const int32_t FX_NONE = (int32_t)0x80000000; /* INT32_MIN */

/* ----------------------------- Index enums ------------------------------ */
/* Beam indices: order MUST match SENSOR_PINS in rig_config.h. */
enum Beam : uint8_t {
  B_ENTRY = 0, B_EXIT = 1,
  B_LCHOICE = 2, B_RCHOICE = 3,
  B_LRETURN = 4, B_RRETURN = 5,
  BEAM_COUNT = 6
};

/* Valve indices: order MUST match VALVE_PINS in rig_config.h.
 * V_START is the central/start port; in the v1 cue-guided task it is the reward
 * port (dispensed at center on a correct choice). see docs/decisions/0007 */
enum Valve : uint8_t { V_LEFT = 0, V_RIGHT = 1, V_START = 2 };
static const Valve V_REWARD = V_START;   /* v1 reward port alias (central) */

/* ------------------------------- Values --------------------------------- */
enum class Phase : uint8_t { Idle, CueRunning, AwaitChoice, AwaitReturn, Done };

/* Training mode. FreeRoam: no cues, no trials, no automatic reward - the animal
 * roams and the experimenter refills the central well with a manual solenoid
 * fire (handled shell-side; see docs/decisions/0010); the trial state machine is
 * skipped. FullTask: the cued two-alternative task. */
enum class Mode : uint8_t { FreeRoam, FullTask };

struct Trial {
  Phase    phase;
  int8_t   correctArm;   /* 0=L, 1=R, -1 none */
  int8_t   choiceArm;    /* -1 until chosen   */
  uint16_t index;        /* position in the sequence */
  uint16_t count;        /* completed trials  */
};

struct Command {
  enum Kind : uint8_t { None, Abort, Ping, Status, SetMode } kind;
  int8_t arg;            /* SetMode: Mode value */
};

struct Session {
  Trial    trial;
  uint32_t startMs;
  bool     active;
  uint32_t rng;          /* threaded PRNG state */
  uint16_t correctCount; /* completed trials scored CORRECT (FullTask) */
};

struct Inputs {
  uint32_t now;
  bool     beam[BEAM_COUNT];  /* debounced RISING-edge flags (see shell) */
  bool     tonePlaying;
  Command  cmd;
};

struct TaskConfig {
  Mode     mode;
  bool     highToneIsLeft;
  bool     startRewardEnabled;
  uint16_t maxTrials;
  uint32_t maxTimeMs;
  uint16_t blockSize;        /* must divide maxTrials; half L / half R */
  uint8_t  maxSameRun;       /* anti-bias: max consecutive same-side trials (0=off) */
  bool     autoDrainOnTrial; /* rinse+drain the central well at each trial start */
  uint16_t rinseMs;          /* auto well-clean: fresh-water rinse duration (ms) */
  uint16_t drainMs;          /* auto well-clean: drain duration after the rinse (ms) */
  uint16_t rewardPulseMs;    /* automatic reward valve-open time (ms); = REWARD_PULSE_MS.
                              * Logged on the REWARD,CENTER line so the host can map the
                              * pulse to a calibrated dispensed volume. see docs/decisions/0019 */
  uint16_t highToneHz;
  uint16_t lowToneHz;
  bool     cueSourceVision;  /* true: HOST drives the cue tone (CUE_ON/CUE_OFF) and the
                              * firmware suppresses its Phase::CueRunning auto-tone, so only
                              * one thing drives the DAC. false: v4 beam-driven auto-tone.
                              * see docs/decisions/0044 */
};

/* ------------------------------- Effects -------------------------------- */
enum class FxTag : uint8_t { StartTone, StopTone, PulseChoice, PulseReturn, OpenValve, CleanWell, Log };

struct Effect {
  FxTag       tag;
  int32_t     a;        /* StartTone: freq; OpenValve: port; CleanWell: rinse ms; Log: numeric field 1 */
  int32_t     b;        /* CleanWell: drain ms; Log: numeric field 2 */
  int32_t     c;        /* Log: numeric field 3 */
  const char* type;     /* Log: line TYPE token */
  const char* detail;   /* Log: detail token printed after the numeric fields ("" if none) */
  int32_t     tail;     /* Log: numeric field printed AFTER detail (e.g. trailing duration ms); FX_NONE if absent. see docs/decisions/0019 */
};

static const uint8_t FX_CAP = 12;
struct Effects { Effect items[FX_CAP]; uint8_t n; };

/* Bounded emit: reserve the last slot for a one-shot overflow marker so the
 * buffer can never overrun and an overflow is visible in the serial log. */
inline void emit(Effects& fx, const Effect& e) {
  if (fx.n < FX_CAP - 1) {
    fx.items[fx.n++] = e;
  } else if (fx.n == FX_CAP - 1) {
    Effect over = { FxTag::Log, FX_NONE, FX_NONE, FX_NONE, "FX_OVERFLOW", "", FX_NONE };
    fx.items[fx.n] = over;
    fx.n = FX_CAP;          /* saturate; further emits are no-ops */
  }
}

/* ---------------------------- Effect builders --------------------------- */
inline Effect startTone(uint16_t freq) { return Effect{ FxTag::StartTone, (int32_t)freq, FX_NONE, FX_NONE, "", "", FX_NONE }; }
inline Effect stopTone()           { return Effect{ FxTag::StopTone, FX_NONE, FX_NONE, FX_NONE, "", "", FX_NONE }; }
inline Effect pulseChoice()        { return Effect{ FxTag::PulseChoice, FX_NONE, FX_NONE, FX_NONE, "", "", FX_NONE }; }
inline Effect pulseReturn()        { return Effect{ FxTag::PulseReturn, FX_NONE, FX_NONE, FX_NONE, "", "", FX_NONE }; }
inline Effect openValve(int port)  { return Effect{ FxTag::OpenValve, (int32_t)port, FX_NONE, FX_NONE, "", "", FX_NONE }; }
inline Effect cleanWell(uint16_t rinseMs, uint16_t drainMs) { return Effect{ FxTag::CleanWell, (int32_t)rinseMs, (int32_t)drainMs, FX_NONE, "", "", FX_NONE }; }
inline Effect logEv(const char* type, const char* detail)               { return Effect{ FxTag::Log, FX_NONE, FX_NONE, FX_NONE, type, detail, FX_NONE }; }
/* logEvDur: like logEv but appends a trailing numeric field after the detail
 * (e.g. "REWARD,CENTER,<dur_ms>"). see docs/decisions/0019 */
inline Effect logEvDur(const char* type, const char* detail, int32_t tail)  { return Effect{ FxTag::Log, FX_NONE, FX_NONE, FX_NONE, type, detail, tail }; }
inline Effect logN1(const char* type, int32_t a, const char* detail)    { return Effect{ FxTag::Log, a, FX_NONE, FX_NONE, type, detail, FX_NONE }; }
inline Effect logN2(const char* type, int32_t a, int32_t b, const char* detail) { return Effect{ FxTag::Log, a, b, FX_NONE, type, detail, FX_NONE }; }
inline Effect logN3(const char* type, int32_t a, int32_t b, int32_t c, const char* detail) { return Effect{ FxTag::Log, a, b, c, type, detail, FX_NONE }; }

/* ----------------------------- Pure helpers ----------------------------- */
inline uint32_t lcg(uint32_t s) { return s * 1664525u + 1013904223u; }

inline uint16_t cueFor(int8_t correctArm, const TaskConfig& c) {
  /* high tone when the correct arm matches the high-tone side */
  bool high = ((correctArm == 0) == c.highToneIsLeft);
  return high ? c.highToneHz : c.lowToneHz;
}

/* True if seq[0..n) never has more than maxRun consecutive identical sides.
 * maxRun == 0 disables the check (always true). */
inline bool maxRunOk(const int8_t* seq, uint16_t n, uint8_t maxRun) {
  if (maxRun == 0 || n < 2) return true;
  uint8_t run = 1;
  for (uint16_t i = 1; i < n; i++) {
    if (seq[i] == seq[i - 1]) { if (++run > maxRun) return false; }
    else run = 1;
  }
  return true;
}

/* Fill seq[0..maxTrials) with balanced, per-block-shuffled L/R using a threaded
 * PRNG; returns the advanced rng state. maxTrials should be a multiple of
 * blockSize (any remainder is left as 0=LEFT).
 *
 * Anti-bias: each block keeps an exact L/R balance, but a block whose shuffle
 * would create a same-side run longer than c.maxSameRun (counting across the
 * boundary with the already-fixed previous blocks) is re-rolled, up to
 * MAX_ATTEMPTS. This discourages the side-bias rats develop on long repeats
 * (cf. Holleman et al. 2019, Sci Rep 9:12589) while staying deterministic from
 * the seed and host-testable. The last attempt is accepted unconditionally so
 * generation always terminates. */
inline uint32_t generateSequence(int8_t* seq, const TaskConfig& c, uint32_t rng) {
  const uint16_t n = c.maxTrials;
  const uint16_t block = c.blockSize;
  const uint16_t half = block / 2;
  const uint16_t MAX_ATTEMPTS = 200;
  for (uint16_t i = 0; i < n; i++) seq[i] = 0;
  for (uint16_t off = 0; off + block <= n; off += block) {
    for (uint16_t attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      for (uint16_t i = 0; i < half; i++) {
        seq[off + i] = 0;          /* left  */
        seq[off + i + half] = 1;   /* right */
      }
      for (int i = block - 1; i > 0; i--) {  /* Fisher-Yates within the block */
        rng = lcg(rng);
        uint32_t j = (rng >> 16) % (uint32_t)(i + 1);
        int8_t t = seq[off + i];
        seq[off + i] = seq[off + j];
        seq[off + j] = t;
      }
      /* validate the prefix so far (catches runs spanning the block boundary) */
      if (maxRunOk(seq, (uint16_t)(off + block), c.maxSameRun)) break;
    }
  }
  return rng;
}

inline Session initialSession(uint32_t now, uint32_t seed) {
  Session s;
  s.trial = Trial{ Phase::Idle, -1, -1, 0, 0 };
  s.startMs = now;
  s.active = true;
  s.rng = seed;
  s.correctCount = 0;
  return s;
}

/* End the session: append SESSION_SUMMARY (FullTask only; counts are meaningless
 * in FreeRoam) then SESSION_COMPLETE, and mark the session done. */
inline void completeSession(Session& n, const TaskConfig& cfg, Effects& fx) {
  n.active = false;
  n.trial.phase = Phase::Done;
  if (cfg.mode == Mode::FullTask) {
    int32_t trials = (int32_t)n.trial.count;
    int32_t correct = (int32_t)n.correctCount;
    emit(fx, logN3("SESSION_SUMMARY", trials, correct, trials - correct, ""));
  }
  emit(fx, logEv("SESSION_COMPLETE", ""));
}

/* ----------------------------- Transition ------------------------------- */
inline Session step(const Session& s, const Inputs& in, const TaskConfig& cfg,
                    const int8_t* seq, Effects& fx) {
  Session n = s;
  if (!s.active) return n;

  /* --- Commands (host control) --- */
  if (in.cmd.kind == Command::Abort) {
    emit(fx, stopTone());
    completeSession(n, cfg, fx);
    return n;
  }
  if (in.cmd.kind == Command::Ping) {
    emit(fx, logEv("PONG", ""));
  } else if (in.cmd.kind == Command::Status) {
    emit(fx, logN2("STATUS", (int32_t)s.trial.count, (int32_t)s.trial.index, s.active ? "1" : "0"));
  }

  /* --- Free Roam: no trials, no cues, no timeout. Commands above still apply
   * (ping / status / abort); the trial state machine is off. Manual reward in
   * FreeRoam is a shell-side solenoid fire (R<port>), not a core command. --- */
  if (cfg.mode == Mode::FreeRoam) return n;

  /* --- Session timeout (FullTask only) --- */
  if (in.now - s.startMs >= cfg.maxTimeMs) {
    emit(fx, stopTone());
    completeSession(n, cfg, fx);
    return n;
  }

  /* --- Trial state machine --- */
  switch (s.trial.phase) {
    case Phase::Idle:
      if (in.beam[B_ENTRY]) {
        if (s.trial.index >= cfg.maxTrials) {
          completeSession(n, cfg, fx);
          break;
        }
        n.trial.correctArm = seq[s.trial.index];
        n.trial.choiceArm = -1;
        n.trial.phase = Phase::CueRunning;
        emit(fx, logN1("TRIAL", (int32_t)(s.trial.index + 1),
                       n.trial.correctArm == 0 ? "LEFT" : "RIGHT"));
        /* Auto well-clean: the animal has just left the central well (a return
         * beam preceded this entry, so the FSM already encodes the
         * return-arm -> central-entry direction). Rinse then drain so the next
         * reward is fresh. Skip the first trial (index 0): nothing to clear and
         * no preceding return. see docs/decisions/0010 */
        if (cfg.autoDrainOnTrial && s.trial.index > 0)
          emit(fx, cleanWell(cfg.rinseMs, cfg.drainMs));
      }
      break;

    case Phase::CueRunning:
      /* Cue tone: BEAM source (v4) plays it while between ENTRY and EXIT and
       * stops it at EXIT. VISION source leaves the DAC entirely to the host
       * (CUE_ON/CUE_OFF); the FSM only advances the phase on the EXIT beam so
       * scoring/reward/TTLs are untouched. see docs/decisions/0044 */
      if (in.beam[B_EXIT]) {
        if (!cfg.cueSourceVision && in.tonePlaying) emit(fx, stopTone());
        n.trial.phase = Phase::AwaitChoice;
      } else if (!cfg.cueSourceVision && !in.tonePlaying) {
        emit(fx, startTone(cueFor(s.trial.correctArm, cfg)));
      }
      break;

    case Phase::AwaitChoice:
      if (in.beam[B_LCHOICE] || in.beam[B_RCHOICE]) {
        int8_t arm = in.beam[B_LCHOICE] ? 0 : 1;   /* single resolution: no double-eval */
        bool correct = (arm == s.trial.correctArm);
        n.trial.choiceArm = arm;
        n.trial.phase = Phase::AwaitReturn;
        emit(fx, pulseChoice());
        emit(fx, logEv("CHOICE", arm == 0 ? "LEFT" : "RIGHT"));
        emit(fx, logEv("OUTCOME", correct ? "CORRECT" : "INCORRECT"));
        /* v1 reward model: the trigger is the choice-arm beam. Reward is
         * dispensed to the central port (V_REWARD) on a correct choice;
         * startRewardEnabled additionally rewards incorrect trials (shaping).
         * The rat collects it at center on its way back. see docs/decisions/0007 */
        if (correct || cfg.startRewardEnabled) {
          emit(fx, openValve(V_REWARD));
          /* Append the reward pulse duration so the host maps it to a calibrated
           * dispensed volume (REWARD,CENTER,<dur_ms>). see docs/decisions/0019 */
          emit(fx, logEvDur("REWARD", "CENTER", (int32_t)cfg.rewardPulseMs));
        }
      }
      break;

    case Phase::AwaitReturn:
      if (in.beam[B_LRETURN] || in.beam[B_RRETURN]) {
        emit(fx, pulseReturn());
        emit(fx, logEv("RETURN_ARM", "ENTRY"));
        /* score the just-completed trial (choiceArm is set by AwaitChoice) */
        if (s.trial.choiceArm == s.trial.correctArm) n.correctCount = s.correctCount + 1;
        n.trial = Trial{ Phase::Idle, -1, -1,
                         (uint16_t)(s.trial.index + 1), (uint16_t)(s.trial.count + 1) };
        if (n.trial.index >= cfg.maxTrials) {
          completeSession(n, cfg, fx);
        }
      }
      break;

    case Phase::Done:
      break;
  }
  return n;
}

#endif /* TASK_CORE_H */
