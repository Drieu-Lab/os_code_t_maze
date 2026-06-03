/*
 * test_task_core.cpp - host (PC) unit tests for the pure task core.
 *
 * task_core.h has no Arduino dependency, so it compiles and runs natively.
 *
 * Build & run (from this directory):
 *     g++ -std=c++11 -I.. test_task_core.cpp -o test_task_core
 *     ./test_task_core            (Windows: test_task_core.exe)
 *
 * Exit code 0 and "ALL TESTS PASSED" on success; assert() aborts on failure.
 */

#include "../task_core.h"

#include <cassert>
#include <cstdio>
#include <cstring>

/* ------------------------------ helpers -------------------------------- */
static TaskConfig mkcfg() {
  TaskConfig c;
  c.highToneIsLeft = true;
  c.startRewardEnabled = false;
  c.maxTrials = 20;
  c.maxTimeMs = 3600000UL;
  c.blockSize = 20;
  c.highToneHz = 8000;
  c.lowToneHz  = 4000;
  return c;
}

static Inputs mkin(uint32_t now) {
  Inputs in;
  in.now = now;
  for (int i = 0; i < BEAM_COUNT; i++) in.beam[i] = false;
  in.tonePlaying = false;
  in.cmd = Command{ Command::None, 0 };
  return in;
}

static int countTag(const Effects& fx, FxTag t) {
  int c = 0;
  for (int i = 0; i < fx.n; i++) if (fx.items[i].tag == t) c++;
  return c;
}

static bool hasLog(const Effects& fx, const char* type) {
  for (int i = 0; i < fx.n; i++)
    if (fx.items[i].tag == FxTag::Log && fx.items[i].type && std::strcmp(fx.items[i].type, type) == 0)
      return true;
  return false;
}

static const Effect* findLog(const Effects& fx, const char* type) {
  for (int i = 0; i < fx.n; i++)
    if (fx.items[i].tag == FxTag::Log && fx.items[i].type && std::strcmp(fx.items[i].type, type) == 0)
      return &fx.items[i];
  return nullptr;
}

/* ------------------------------- tests --------------------------------- */
static void test_cueFor() {
  TaskConfig c = mkcfg();
  c.highToneIsLeft = true;
  assert(cueFor(0, c) == 8000);   /* correct=LEFT  -> high */
  assert(cueFor(1, c) == 4000);   /* correct=RIGHT -> low  */
  c.highToneIsLeft = false;
  assert(cueFor(0, c) == 4000);   /* correct=LEFT  -> low  */
  assert(cueFor(1, c) == 8000);   /* correct=RIGHT -> high */
  printf("  ok: cueFor mapping\n");
}

static void test_sequence_balanced() {
  TaskConfig c = mkcfg();
  int8_t seq[20];
  generateSequence(seq, c, 12345u);
  int left = 0, right = 0;
  for (int i = 0; i < 20; i++) { assert(seq[i] == 0 || seq[i] == 1); (seq[i] == 0 ? left : right)++; }
  assert(left == 10 && right == 10);     /* balanced block */
  printf("  ok: sequence is balanced (10L/10R)\n");
}

static void test_full_correct_trial() {
  TaskConfig c = mkcfg();
  int8_t seq[20];
  for (int i = 0; i < 20; i++) seq[i] = 0;   /* all LEFT correct */

  Session s = initialSession(0, 99u);

  /* enter -> CueRunning, TRIAL,1,LEFT */
  Inputs in = mkin(10);
  in.beam[B_ENTRY] = true;
  Effects fx{};
  s = step(s, in, c, seq, fx);
  assert(s.trial.phase == Phase::CueRunning);
  const Effect* tr = findLog(fx, "TRIAL");
  assert(tr && tr->a == 1 && std::strcmp(tr->detail, "LEFT") == 0);

  /* cue running, tone not yet playing -> StartTone */
  in = mkin(20);
  fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(countTag(fx, FxTag::StartTone) == 1);

  /* exit beam, tone playing -> StopTone, AwaitChoice */
  in = mkin(30); in.beam[B_EXIT] = true; in.tonePlaying = true;
  fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(s.trial.phase == Phase::AwaitChoice);
  assert(countTag(fx, FxTag::StopTone) == 1);

  /* left choice (correct) -> CHOICE/OUTCOME + central reward at the choice-arm
     beam (v1 trigger point). */
  in = mkin(40); in.beam[B_LCHOICE] = true;
  fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(s.trial.phase == Phase::AwaitReturn);
  assert(s.trial.choiceArm == 0);
  assert(hasLog(fx, "CHOICE"));
  assert(std::strcmp(findLog(fx, "OUTCOME")->detail, "CORRECT") == 0);
  assert(countTag(fx, FxTag::OpenValve) == 1);    /* reward at the central port */
  assert(std::strcmp(findLog(fx, "REWARD")->detail, "CENTER") == 0);

  /* return -> RETURN_ARM only (reward already dispensed), advance trial */
  in = mkin(50); in.beam[B_LRETURN] = true;
  fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(s.trial.phase == Phase::Idle);
  assert(s.trial.index == 1 && s.trial.count == 1);
  assert(hasLog(fx, "RETURN_ARM"));
  assert(countTag(fx, FxTag::OpenValve) == 0);    /* no second reward on return */
  assert(!hasLog(fx, "REWARD"));
  printf("  ok: full correct trial flow + central reward at choice + advance\n");
}

static void test_incorrect_no_reward() {
  TaskConfig c = mkcfg();
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;  /* LEFT correct */
  Session s = initialSession(0, 7u);
  Inputs in = mkin(10); in.beam[B_ENTRY] = true; Effects fx{};
  s = step(s, in, c, seq, fx);                 /* CueRunning */
  in = mkin(20); in.beam[B_EXIT] = true; fx = Effects{};
  s = step(s, in, c, seq, fx);                 /* AwaitChoice */
  in = mkin(30); in.beam[B_RCHOICE] = true; fx = Effects{};   /* wrong arm */
  s = step(s, in, c, seq, fx);
  assert(std::strcmp(findLog(fx, "OUTCOME")->detail, "INCORRECT") == 0);
  assert(countTag(fx, FxTag::OpenValve) == 0);
  assert(!hasLog(fx, "REWARD"));

  /* return after an incorrect choice (startRewardEnabled=false) -> still no
     reward at the central port */
  in = mkin(40); in.beam[B_RRETURN] = true; fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(hasLog(fx, "RETURN_ARM"));
  assert(countTag(fx, FxTag::OpenValve) == 0);
  assert(!hasLog(fx, "REWARD"));
  printf("  ok: incorrect choice -> no reward (choice or center)\n");
}

static void test_shaping_rewards_incorrect_at_center() {
  TaskConfig c = mkcfg();
  c.startRewardEnabled = true;             /* shaping: reward even when wrong */
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;  /* LEFT correct */
  Session s = initialSession(0, 7u);
  Inputs in = mkin(10); in.beam[B_ENTRY] = true; Effects fx{};
  s = step(s, in, c, seq, fx);                 /* CueRunning */
  in = mkin(20); in.beam[B_EXIT] = true; fx = Effects{};
  s = step(s, in, c, seq, fx);                 /* AwaitChoice */
  in = mkin(30); in.beam[B_RCHOICE] = true; fx = Effects{};   /* wrong arm */
  s = step(s, in, c, seq, fx);
  assert(std::strcmp(findLog(fx, "OUTCOME")->detail, "INCORRECT") == 0);
  /* central reward dispensed anyway at the choice-arm beam (shaping) */
  assert(countTag(fx, FxTag::OpenValve) == 1);
  assert(std::strcmp(findLog(fx, "REWARD")->detail, "CENTER") == 0);
  /* return -> no second reward */
  in = mkin(40); in.beam[B_RRETURN] = true; fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(countTag(fx, FxTag::OpenValve) == 0);
  assert(!hasLog(fx, "REWARD"));
  printf("  ok: startRewardEnabled rewards incorrect trials at center (shaping)\n");
}

static void test_simultaneous_choice_resolves_once() {
  TaskConfig c = mkcfg();
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;
  Session s = initialSession(0, 1u);
  Inputs in = mkin(10); in.beam[B_ENTRY] = true; Effects fx{};
  s = step(s, in, c, seq, fx);
  in = mkin(20); in.beam[B_EXIT] = true; fx = Effects{};
  s = step(s, in, c, seq, fx);                 /* AwaitChoice */

  in = mkin(30); in.beam[B_LCHOICE] = true; in.beam[B_RCHOICE] = true;  /* BOTH */
  fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(countTag(fx, FxTag::PulseChoice) == 1);   /* exactly one resolution */
  assert(hasLog(fx, "CHOICE"));
  assert(hasLog(fx, "OUTCOME"));
  assert(s.trial.choiceArm == 0);                  /* left wins the tie */
  printf("  ok: simultaneous L+R resolves to exactly one choice\n");
}

static void test_abort() {
  TaskConfig c = mkcfg();
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;
  Session s = initialSession(0, 1u);
  Inputs in = mkin(100); in.cmd = Command{ Command::Abort, 0 };
  Effects fx{};
  s = step(s, in, c, seq, fx);
  assert(!s.active);
  assert(countTag(fx, FxTag::StopTone) == 1);
  assert(hasLog(fx, "SESSION_COMPLETE"));
  /* once inactive, step is a no-op with no effects */
  Inputs in2 = mkin(110); in2.beam[B_ENTRY] = true; Effects fx2{};
  s = step(s, in2, c, seq, fx2);
  assert(fx2.n == 0 && s.trial.phase == Phase::Done);
  printf("  ok: ABORT ends session, then no-op\n");
}

static void test_timeout() {
  TaskConfig c = mkcfg();
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;
  Session s = initialSession(0, 1u);
  Inputs in = mkin(c.maxTimeMs + 1);
  Effects fx{};
  s = step(s, in, c, seq, fx);
  assert(!s.active);
  assert(hasLog(fx, "SESSION_COMPLETE"));
  printf("  ok: timeout ends session\n");
}

static void test_max_trials() {
  TaskConfig c = mkcfg();
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;
  Session s = initialSession(0, 1u);
  s.trial.index = c.maxTrials;          /* already at the limit */
  Inputs in = mkin(10); in.beam[B_ENTRY] = true;
  Effects fx{};
  s = step(s, in, c, seq, fx);
  assert(!s.active);
  assert(hasLog(fx, "SESSION_COMPLETE"));
  printf("  ok: max-trials ends session on next entry\n");
}

/* Exhaustive proof that no reachable (phase, inputs, command) overflows FX_CAP. */
static void test_no_overflow_exhaustive() {
  TaskConfig c = mkcfg();
  c.startRewardEnabled = true;          /* worst case: extra start reward */
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;

  Phase phases[] = { Phase::Idle, Phase::CueRunning, Phase::AwaitChoice, Phase::AwaitReturn, Phase::Done };
  Command::Kind cmds[] = { Command::None, Command::Abort, Command::Flush, Command::Ping, Command::Status };

  unsigned long checked = 0;
  for (Phase ph : phases)
    for (int8_t arm = 0; arm <= 1; arm++)
      for (int tone = 0; tone < 2; tone++)
        for (Command::Kind ck : cmds)
          for (uint8_t bits = 0; bits < (1u << BEAM_COUNT); bits++) {
            Session s = initialSession(0, 1u);
            s.trial.phase = ph;
            s.trial.correctArm = arm;
            s.trial.index = 3;
            Inputs in = mkin(10);
            for (int i = 0; i < BEAM_COUNT; i++) in.beam[i] = (bits >> i) & 1;
            in.tonePlaying = tone;
            in.cmd = Command{ ck, V_LEFT };
            Effects fx{};
            step(s, in, c, seq, fx);
            assert(fx.n < FX_CAP);              /* never saturates */
            assert(!hasLog(fx, "FX_OVERFLOW")); /* marker never emitted */
            checked++;
          }
  printf("  ok: no overflow across %lu (phase x inputs x cmd) cases\n", checked);
}

int main() {
  printf("Running task_core tests...\n");
  test_cueFor();
  test_sequence_balanced();
  test_full_correct_trial();
  test_incorrect_no_reward();
  test_shaping_rewards_incorrect_at_center();
  test_simultaneous_choice_resolves_once();
  test_abort();
  test_timeout();
  test_max_trials();
  test_no_overflow_exhaustive();
  printf("ALL TESTS PASSED\n");
  return 0;
}
