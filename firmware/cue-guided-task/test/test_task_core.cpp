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
  c.mode = Mode::FullTask;
  c.highToneIsLeft = true;
  c.startRewardEnabled = false;
  c.maxTrials = 20;
  c.maxTimeMs = 3600000UL;
  c.blockSize = 20;
  c.maxSameRun = 3;
  c.autoDrainOnTrial = false;   /* most tests don't exercise the auto well-clean */
  c.rinseMs = 150;
  c.drainMs = 300;
  c.rewardPulseMs = 300;        /* logged on REWARD,CENTER (see docs/decisions/0019) */
  c.highToneHz = 8000;
  c.lowToneHz  = 4000;
  c.cueSourceVision = false;    /* BEAM (v4) auto-tone by default; see docs/decisions/0044 */
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
  assert(findLog(fx, "REWARD")->tail == (int32_t)c.rewardPulseMs);  /* dur logged for host volume calc */

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

static void test_cue_source_vision_suppresses_autotone() {
  /* Under CUE,VISION the firmware must NOT auto-start/stop the tone in
     CueRunning (the host drives it), but the trial FSM must still advance on the
     beams exactly as in BEAM mode. see docs/decisions/0044 */
  TaskConfig c = mkcfg();
  c.cueSourceVision = true;
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;   /* all LEFT */
  Session s = initialSession(0, 42u);

  /* entry -> CueRunning (unchanged) */
  Inputs in = mkin(10); in.beam[B_ENTRY] = true; Effects fx{};
  s = step(s, in, c, seq, fx);
  assert(s.trial.phase == Phase::CueRunning);

  /* in CueRunning with no tone playing: BEAM mode would StartTone; VISION must not */
  in = mkin(20); fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(countTag(fx, FxTag::StartTone) == 0);

  /* exit beam while a (host-started) tone is playing: VISION must NOT StopTone
     (host owns off), but the phase still advances */
  in = mkin(30); in.beam[B_EXIT] = true; in.tonePlaying = true; fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(s.trial.phase == Phase::AwaitChoice);
  assert(countTag(fx, FxTag::StopTone) == 0);

  /* ABORT still force-stops the tone even under VISION (safety) */
  in = mkin(40); in.cmd = Command{ Command::Abort, 0 }; fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(countTag(fx, FxTag::StopTone) == 1);
  assert(!s.active);
  printf("  ok: CUE,VISION suppresses the beam auto-tone; FSM + abort unchanged\n");
}

static void test_free_roam() {
  TaskConfig c = mkcfg();
  c.mode = Mode::FreeRoam;
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;
  Session s = initialSession(0, 5u);

  /* entry beam must NOT start a trial or a tone in free roam */
  Inputs in = mkin(10); in.beam[B_ENTRY] = true; Effects fx{};
  s = step(s, in, c, seq, fx);
  assert(s.trial.phase == Phase::Idle);
  assert(!hasLog(fx, "TRIAL"));
  assert(countTag(fx, FxTag::StartTone) == 0);

  /* choice beams do nothing either */
  in = mkin(20); in.beam[B_LCHOICE] = true; fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(s.trial.phase == Phase::Idle);
  assert(!hasLog(fx, "CHOICE"));

  /* the core dispenses no automatic reward in free roam (manual reward is a
     shell-side solenoid fire, R<port>, not a core command) */
  assert(countTag(fx, FxTag::OpenValve) == 0);

  /* free roam never times out */
  in = mkin(c.maxTimeMs + 1); fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(s.active);
  assert(!hasLog(fx, "SESSION_COMPLETE"));
  printf("  ok: free roam = no trials/tone/auto-reward, no timeout\n");
}

static void test_anti_bias_run_length() {
  TaskConfig c = mkcfg();
  c.maxTrials = 200; c.blockSize = 20; c.maxSameRun = 3;
  int8_t seq[200];
  uint32_t r1 = generateSequence(seq, c, 24680u);

  /* never more than 3 consecutive same-side trials */
  assert(maxRunOk(seq, 200, 3));

  /* balance is still exact within each block */
  for (int off = 0; off < 200; off += 20) {
    int l = 0, rr = 0;
    for (int i = 0; i < 20; i++) (seq[off + i] == 0 ? l : rr)++;
    assert(l == 10 && rr == 10);
  }

  /* deterministic from the seed */
  int8_t seq2[200];
  uint32_t r2 = generateSequence(seq2, c, 24680u);
  assert(r1 == r2);
  for (int i = 0; i < 200; i++) assert(seq[i] == seq2[i]);
  printf("  ok: anti-bias caps runs at 3, keeps block balance, deterministic\n");
}

static void test_session_summary() {
  TaskConfig c = mkcfg();
  c.maxTrials = 2; c.blockSize = 2;
  int8_t seq[2] = { 0, 0 };   /* both LEFT correct */
  Session s = initialSession(0, 3u);

  /* trial 1: correct (chose LEFT) */
  Inputs in = mkin(10); in.beam[B_ENTRY] = true; Effects fx{}; s = step(s, in, c, seq, fx);
  in = mkin(20); in.beam[B_EXIT] = true; in.tonePlaying = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  in = mkin(30); in.beam[B_LCHOICE] = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  in = mkin(40); in.beam[B_LRETURN] = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  assert(s.correctCount == 1 && s.trial.count == 1 && s.active);

  /* trial 2: incorrect (chose RIGHT); returning on the final trial ends it */
  in = mkin(50); in.beam[B_ENTRY] = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  in = mkin(60); in.beam[B_EXIT] = true; in.tonePlaying = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  in = mkin(70); in.beam[B_RCHOICE] = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  in = mkin(80); in.beam[B_RRETURN] = true; fx = Effects{}; s = step(s, in, c, seq, fx);

  assert(!s.active);
  const Effect* sum = findLog(fx, "SESSION_SUMMARY");
  assert(sum && sum->a == 2 && sum->b == 1 && sum->c == 1);   /* trials, correct, incorrect */
  assert(hasLog(fx, "SESSION_COMPLETE"));
  printf("  ok: SESSION_SUMMARY reports trials/correct/incorrect at completion\n");
}

static void test_auto_drain() {
  /* helper: run a full correct LEFT trial from Idle back to Idle */
  TaskConfig c = mkcfg();
  c.autoDrainOnTrial = true; c.maxTrials = 4; c.blockSize = 4;
  int8_t seq[4] = { 0, 0, 0, 0 };   /* all LEFT correct */
  Session s = initialSession(0, 9u);

  /* trial 1 (index 0): entry starts the trial but emits NO CleanWell (first trial) */
  Inputs in = mkin(10); in.beam[B_ENTRY] = true; Effects fx{};
  s = step(s, in, c, seq, fx);
  assert(hasLog(fx, "TRIAL"));
  assert(countTag(fx, FxTag::CleanWell) == 0);

  /* finish trial 1 */
  in = mkin(20); in.beam[B_EXIT] = true; in.tonePlaying = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  in = mkin(30); in.beam[B_LCHOICE] = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  in = mkin(40); in.beam[B_LRETURN] = true; fx = Effects{}; s = step(s, in, c, seq, fx);
  assert(s.trial.index == 1 && s.trial.phase == Phase::Idle);

  /* trial 2 (index 1): entry now emits a CleanWell carrying the configured durations */
  in = mkin(50); in.beam[B_ENTRY] = true; fx = Effects{};
  s = step(s, in, c, seq, fx);
  assert(hasLog(fx, "TRIAL"));
  assert(countTag(fx, FxTag::CleanWell) == 1);
  const Effect* cw = nullptr;
  for (int i = 0; i < fx.n; i++) if (fx.items[i].tag == FxTag::CleanWell) cw = &fx.items[i];
  assert(cw && cw->a == (int32_t)c.rinseMs && cw->b == (int32_t)c.drainMs);

  /* with the toggle off, no CleanWell even on a later trial */
  TaskConfig c2 = mkcfg();
  c2.autoDrainOnTrial = false; c2.maxTrials = 4; c2.blockSize = 4;
  Session s2 = initialSession(0, 9u);
  Inputs i2 = mkin(10); i2.beam[B_ENTRY] = true; Effects f2{}; s2 = step(s2, i2, c2, seq, f2);
  i2 = mkin(20); i2.beam[B_EXIT] = true; i2.tonePlaying = true; f2 = Effects{}; s2 = step(s2, i2, c2, seq, f2);
  i2 = mkin(30); i2.beam[B_LCHOICE] = true; f2 = Effects{}; s2 = step(s2, i2, c2, seq, f2);
  i2 = mkin(40); i2.beam[B_LRETURN] = true; f2 = Effects{}; s2 = step(s2, i2, c2, seq, f2);
  i2 = mkin(50); i2.beam[B_ENTRY] = true; f2 = Effects{}; s2 = step(s2, i2, c2, seq, f2);
  assert(countTag(f2, FxTag::CleanWell) == 0);
  printf("  ok: auto well-clean fires at trial start (not the first), gated by the toggle\n");
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

/* Host-control commands (PING/STATUS) must still be answered after the session
 * ends, so the broker's liveness probe keeps working post-session. ABORT on an
 * already-idle session is a no-op (no second SESSION_COMPLETE). see docs/decisions/0123 */
static void test_commands_serviced_when_inactive() {
  TaskConfig c = mkcfg();
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;
  Session s = initialSession(0, 1u);
  Inputs abort = mkin(100); abort.cmd = Command{ Command::Abort, 0 };
  Effects fx{}; s = step(s, abort, c, seq, fx);
  assert(!s.active);

  /* PING while inactive -> PONG (and the trial FSM stays off). */
  Inputs ping = mkin(110); ping.cmd = Command{ Command::Ping, 0 };
  Effects fxp{}; s = step(s, ping, c, seq, fxp);
  assert(hasLog(fxp, "PONG"));
  assert(!s.active && s.trial.phase == Phase::Done);

  /* STATUS while inactive -> STATUS reply reporting active flag "0". */
  Inputs status = mkin(120); status.cmd = Command{ Command::Status, 0 };
  Effects fxs{}; s = step(s, status, c, seq, fxs);
  const Effect* st = findLog(fxs, "STATUS");
  assert(st != nullptr);
  assert(st->detail && std::strcmp(st->detail, "0") == 0);   /* not active */

  /* ABORT on an already-idle session is a no-op: no repeat SESSION_COMPLETE. */
  Inputs abort2 = mkin(130); abort2.cmd = Command{ Command::Abort, 0 };
  Effects fxa{}; s = step(s, abort2, c, seq, fxa);
  assert(!hasLog(fxa, "SESSION_COMPLETE"));
  assert(countTag(fxa, FxTag::StopTone) == 0);
  printf("  ok: PING/STATUS answered when inactive; ABORT idle is a no-op\n");
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
  c.autoDrainOnTrial = true;            /* worst case: extra CleanWell at trial start */
  int8_t seq[20]; for (int i = 0; i < 20; i++) seq[i] = 0;

  Phase phases[] = { Phase::Idle, Phase::CueRunning, Phase::AwaitChoice, Phase::AwaitReturn, Phase::Done };
  Command::Kind cmds[] = { Command::None, Command::Abort, Command::Ping, Command::Status };

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
  test_cue_source_vision_suppresses_autotone();
  test_free_roam();
  test_anti_bias_run_length();
  test_session_summary();
  test_auto_drain();
  test_abort();
  test_commands_serviced_when_inactive();
  test_timeout();
  test_max_trials();
  test_no_overflow_exhaustive();
  printf("ALL TESTS PASSED\n");
  return 0;
}
