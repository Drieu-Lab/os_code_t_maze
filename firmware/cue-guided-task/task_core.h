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

/* Valve indices: order MUST match VALVE_PINS in rig_config.h. */
enum Valve : uint8_t { V_LEFT = 0, V_RIGHT = 1, V_START = 2 };

/* ------------------------------- Values --------------------------------- */
enum class Phase : uint8_t { Idle, CueRunning, AwaitChoice, AwaitReturn, Done };

struct Trial {
  Phase    phase;
  int8_t   correctArm;   /* 0=L, 1=R, -1 none */
  int8_t   choiceArm;    /* -1 until chosen   */
  uint16_t index;        /* position in the sequence */
  uint16_t count;        /* completed trials  */
};

struct Command {
  enum Kind : uint8_t { None, Abort, Flush, Ping, Status } kind;
  int8_t arg;            /* Flush: valve/port index */
};

struct Session {
  Trial    trial;
  uint32_t startMs;
  bool     active;
  uint32_t rng;          /* threaded PRNG state */
};

struct Inputs {
  uint32_t now;
  bool     beam[BEAM_COUNT];  /* debounced RISING-edge flags (see shell) */
  bool     tonePlaying;
  Command  cmd;
};

struct TaskConfig {
  bool     highToneIsLeft;
  bool     startRewardEnabled;
  uint16_t maxTrials;
  uint32_t maxTimeMs;
  uint16_t blockSize;        /* must divide maxTrials; half L / half R */
  uint16_t highToneHz;
  uint16_t lowToneHz;
};

/* ------------------------------- Effects -------------------------------- */
enum class FxTag : uint8_t { StartTone, StopTone, PulseChoice, PulseReturn, OpenValve, Log };

struct Effect {
  FxTag       tag;
  int32_t     a;        /* StartTone: freq; OpenValve: port; Log: numeric field 1 */
  int32_t     b;        /* Log: numeric field 2 */
  const char* type;     /* Log: line TYPE token */
  const char* detail;   /* Log: trailing detail token ("" if none) */
};

static const uint8_t FX_CAP = 12;
struct Effects { Effect items[FX_CAP]; uint8_t n; };

/* Bounded emit: reserve the last slot for a one-shot overflow marker so the
 * buffer can never overrun and an overflow is visible in the serial log. */
inline void emit(Effects& fx, const Effect& e) {
  if (fx.n < FX_CAP - 1) {
    fx.items[fx.n++] = e;
  } else if (fx.n == FX_CAP - 1) {
    Effect over = { FxTag::Log, FX_NONE, FX_NONE, "FX_OVERFLOW", "" };
    fx.items[fx.n] = over;
    fx.n = FX_CAP;          /* saturate; further emits are no-ops */
  }
}

/* ---------------------------- Effect builders --------------------------- */
inline Effect startTone(uint16_t freq) { return Effect{ FxTag::StartTone, (int32_t)freq, FX_NONE, "", "" }; }
inline Effect stopTone()           { return Effect{ FxTag::StopTone, FX_NONE, FX_NONE, "", "" }; }
inline Effect pulseChoice()        { return Effect{ FxTag::PulseChoice, FX_NONE, FX_NONE, "", "" }; }
inline Effect pulseReturn()        { return Effect{ FxTag::PulseReturn, FX_NONE, FX_NONE, "", "" }; }
inline Effect openValve(int port)  { return Effect{ FxTag::OpenValve, (int32_t)port, FX_NONE, "", "" }; }
inline Effect logEv(const char* type, const char* detail)               { return Effect{ FxTag::Log, FX_NONE, FX_NONE, type, detail }; }
inline Effect logN1(const char* type, int32_t a, const char* detail)    { return Effect{ FxTag::Log, a, FX_NONE, type, detail }; }
inline Effect logN2(const char* type, int32_t a, int32_t b, const char* detail) { return Effect{ FxTag::Log, a, b, type, detail }; }

/* ----------------------------- Pure helpers ----------------------------- */
inline uint32_t lcg(uint32_t s) { return s * 1664525u + 1013904223u; }

inline uint16_t cueFor(int8_t correctArm, const TaskConfig& c) {
  /* high tone when the correct arm matches the high-tone side */
  bool high = ((correctArm == 0) == c.highToneIsLeft);
  return high ? c.highToneHz : c.lowToneHz;
}

/* Fill seq[0..maxTrials) with balanced, per-block-shuffled L/R using a threaded
 * PRNG; returns the advanced rng state. maxTrials should be a multiple of
 * blockSize (any remainder is left as 0=LEFT). */
inline uint32_t generateSequence(int8_t* seq, const TaskConfig& c, uint32_t rng) {
  const uint16_t n = c.maxTrials;
  const uint16_t block = c.blockSize;
  const uint16_t half = block / 2;
  for (uint16_t i = 0; i < n; i++) seq[i] = 0;
  for (uint16_t off = 0; off + block <= n; off += block) {
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
  }
  return rng;
}

inline Session initialSession(uint32_t now, uint32_t seed) {
  Session s;
  s.trial = Trial{ Phase::Idle, -1, -1, 0, 0 };
  s.startMs = now;
  s.active = true;
  s.rng = seed;
  return s;
}

/* ----------------------------- Transition ------------------------------- */
inline Session step(const Session& s, const Inputs& in, const TaskConfig& cfg,
                    const int8_t* seq, Effects& fx) {
  Session n = s;
  if (!s.active) return n;

  /* --- Commands (host control) --- */
  if (in.cmd.kind == Command::Abort) {
    n.active = false;
    n.trial.phase = Phase::Done;
    emit(fx, stopTone());
    emit(fx, logEv("SESSION_COMPLETE", ""));
    return n;
  }
  if (in.cmd.kind == Command::Ping) {
    emit(fx, logEv("PONG", ""));
  } else if (in.cmd.kind == Command::Status) {
    emit(fx, logN2("STATUS", (int32_t)s.trial.count, (int32_t)s.trial.index, s.active ? "1" : "0"));
  } else if (in.cmd.kind == Command::Flush) {
    emit(fx, openValve(in.cmd.arg));   /* orthogonal manual flush */
  }

  /* --- Session timeout --- */
  if (in.now - s.startMs >= cfg.maxTimeMs) {
    n.active = false;
    n.trial.phase = Phase::Done;
    emit(fx, stopTone());
    emit(fx, logEv("SESSION_COMPLETE", ""));
    return n;
  }

  /* --- Trial state machine --- */
  switch (s.trial.phase) {
    case Phase::Idle:
      if (in.beam[B_ENTRY]) {
        if (s.trial.index >= cfg.maxTrials) {
          n.active = false;
          n.trial.phase = Phase::Done;
          emit(fx, logEv("SESSION_COMPLETE", ""));
          break;
        }
        n.trial.correctArm = seq[s.trial.index];
        n.trial.choiceArm = -1;
        n.trial.phase = Phase::CueRunning;
        emit(fx, logN1("TRIAL", (int32_t)(s.trial.index + 1),
                       n.trial.correctArm == 0 ? "LEFT" : "RIGHT"));
      }
      break;

    case Phase::CueRunning:
      if (in.beam[B_EXIT]) {
        if (in.tonePlaying) emit(fx, stopTone());
        n.trial.phase = Phase::AwaitChoice;
      } else if (!in.tonePlaying) {
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
        if (correct) {
          emit(fx, openValve(arm));
          emit(fx, logEv("REWARD", arm == 0 ? "LEFT" : "RIGHT"));
        }
      }
      break;

    case Phase::AwaitReturn:
      if (in.beam[B_LRETURN] || in.beam[B_RRETURN]) {
        emit(fx, pulseReturn());
        emit(fx, logEv("RETURN_ARM", "ENTRY"));
        if (cfg.startRewardEnabled) {
          emit(fx, openValve(V_START));
          emit(fx, logEv("REWARD", "START"));
        }
        n.trial = Trial{ Phase::Idle, -1, -1,
                         (uint16_t)(s.trial.index + 1), (uint16_t)(s.trial.count + 1) };
        if (n.trial.index >= cfg.maxTrials) {
          n.active = false;
          n.trial.phase = Phase::Done;
          emit(fx, logEv("SESSION_COMPLETE", ""));
        }
      }
      break;

    case Phase::Done:
      break;
  }
  return n;
}

#endif /* TASK_CORE_H */
