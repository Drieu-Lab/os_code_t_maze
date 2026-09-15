#ifndef RIG_CONFIG_H
#define RIG_CONFIG_H

/*
 * rig_config.h - single source of truth for the T-maze rig wiring and the
 * cue-guided-task tunables.
 *
 * This is the ONE real file. Each sketch folder contains a symlink named
 * rig_config.h that points here, so the sketches can include a plain
 * "rig_config.h" (Arduino's GUI build copies only the sketch folder and can't
 * follow a "../" include out of it):
 *
 *   firmware/cue-guided-task/rig_config.h      -> ../common/rig_config.h
 *   firmware/tests/tmaze_test/rig_config.h     -> ../../common/rig_config.h
 *
 * Edit only this file; both sketches see the change automatically. Recreating
 * the symlinks requires Developer Mode (or admin) on Windows and
 * `git config core.symlinks true`. See docs/decisions/0006 (supersedes 0005).
 *
 * This header is plain preprocessor + integer constants only (no Arduino types),
 * so the pure task core can stay hardware-agnostic and host-testable.
 *
 * Lines tagged CONFIRM are best-effort values derived from tmaze_test or chosen
 * from free pins; verify them against the physical rig before running an animal.
 */

/* ============================ Physical wiring ============================ */
/* Body-beam sensors: Banner VS2 (NPN) -> NOYIYO opto (schematic "Normal Low /
 * Actuated High") -> Due INPUT_PULLUP, so at the Due pin a broken beam reads
 * HIGH and rest reads LOW. Pins from tmaze_test. see docs/decisions/0164 */
#define PIN_BEAM_LEFT        39 /* 39 */
#define PIN_BEAM_CENTER      42 /* 42 */
#define PIN_BEAM_RIGHT       43 /* 43 */
#define PIN_BEAM_RCHOICE     41 /* CONFIRM */
#define PIN_BEAM_CENTER_END  38 /* 38 */
#define PIN_BEAM_LCHOICE     40 /* 40 */

/* Lick detectors: Banner D12DAB6 fiber amp -> Due INPUT_PULLUP; the pin idles
 * HIGH and is pulled LOW on tongue contact (at the amp's current LO/DO setting).
 * Raw, index-ordered pins; the L,i serial protocol and monitor GUIs use this
 * 0..2 order. see docs/decisions/0164 */
#define PIN_LICK_0  37
#define PIN_LICK_1  36
#define PIN_LICK_2  35

/* --- Lick ports: geometric labels (parallel to the beam labels above) ------
 * Physical pin<->port mapping VERIFIED on the rig 2026-07-01 (lick each port,
 * watch which pin/L,i fires in lick_tuner): LEFT = pin 37 (L0), RIGHT = pin 36
 * (L1), CENTER/start = pin 35 (L2). This corrects the earlier assumption that
 * center == pin 37 / L0. see docs/decisions/0022 */
#define PIN_LICK_LEFT    PIN_LICK_0   /* pin 37 = L0 (verified) */
#define PIN_LICK_RIGHT   PIN_LICK_1   /* pin 36 = L1 (verified) */
#define PIN_LICK_CENTER  PIN_LICK_2   /* pin 35 = L2 (center/start, verified) */

/* Lick-port task roles. v1 cue-guided task: reward is dispensed to the CENTRAL
 * port on a correct choice (triggered at the choice-arm beam) and the rat
 * collects it at center; the left/right lick ports have no task role
 * (monitor-only). The reward is actually dispensed by the central valve
 * (V_START / PIN_VALVE_START); PIN_LICK_REWARD names the co-located lick
 * detector for monitoring and possible future lick-gating of reward.
 * This mapping may change in a different task. see docs/decisions/0007 */
#define PIN_LICK_REWARD  PIN_LICK_CENTER   /* = pin 35 = L2 (verified) */

/* Reward solenoids. Each reward port (left/right/center) carries five valves:
 * three reward spouts (R1,R2,R3), a DRAIN, and a RINSE. The per-pin
 * geometric+function labels below are the single source of the pin numbers
 * (parallel to the beam/lick labels above); SOLENOID_TABLE and the reward-valve
 * pins are derived from them. ports: 0=left 1=right 2=center(start).
 * CONFIRM the physical port and the R/DRAIN/RINSE assignment on the rig: fire
 * R<port><sol> (tmaze_test / experiment_control) and watch which valve clicks. */
#define SOLENOIDS_PER_PORT 5

#define PIN_SOL_LEFT_R1      23  /* CONFIRM */
#define PIN_SOL_LEFT_R2      24  /* CONFIRM */
#define PIN_SOL_LEFT_R3      25  /* CONFIRM */
#define PIN_SOL_LEFT_DRAIN   26  /* CONFIRM */
#define PIN_SOL_LEFT_RINSE   44  /* CONFIRM */

#define PIN_SOL_RIGHT_R1     27  /* CONFIRM */
#define PIN_SOL_RIGHT_R2     28  /* CONFIRM */
#define PIN_SOL_RIGHT_R3     29  /* CONFIRM */
#define PIN_SOL_RIGHT_DRAIN  30  /* CONFIRM */
#define PIN_SOL_RIGHT_RINSE  45  /* CONFIRM */

#define PIN_SOL_CENTER_R1    31  /* CONFIRM (center == start port) */
#define PIN_SOL_CENTER_R2    32  /* CONFIRM */
#define PIN_SOL_CENTER_R3    33  /* CONFIRM */
#define PIN_SOL_CENTER_DRAIN 34  /* CONFIRM */
#define PIN_SOL_CENTER_RINSE 46  /* CONFIRM */

/* [port][R1,R2,R3,DRAIN,RINSE]; the column index matches the SOL,<port>,<sol>
 * serial index (sol 0-2 reward, 3 drain, 4 rinse). */
#define SOLENOID_TABLE { \
  { PIN_SOL_LEFT_R1,   PIN_SOL_LEFT_R2,   PIN_SOL_LEFT_R3,   PIN_SOL_LEFT_DRAIN,   PIN_SOL_LEFT_RINSE   }, \
  { PIN_SOL_RIGHT_R1,  PIN_SOL_RIGHT_R2,  PIN_SOL_RIGHT_R3,  PIN_SOL_RIGHT_DRAIN,  PIN_SOL_RIGHT_RINSE  }, \
  { PIN_SOL_CENTER_R1, PIN_SOL_CENTER_R2, PIN_SOL_CENTER_R3, PIN_SOL_CENTER_DRAIN, PIN_SOL_CENTER_RINSE } }

/* Which solenoid index (column) per port the task pulses as the reward spout. */
#define REWARD_SOLENOID_INDEX 1          /* 1 == R2 (v1 reward spout) */
/* DRAIN/RINSE column indices (match the SOLENOID_TABLE layout above). Used by the
 * auto well-clean (rinse-then-drain at trial start). see docs/decisions/0010 */
#define DRAIN_SOLENOID_INDEX  3          /* 3 == DRAIN */
#define RINSE_SOLENOID_INDEX  4          /* 4 == RINSE */

/* ======================= Open Ephys TTL outputs ========================= */
/* Free pins (not used by beams/licks/solenoids). CONFIRM each is wired to Open Ephys. */
#define PIN_TTL_CHOICE      4            /* CONFIRM wired */
#define PIN_TTL_RETURN_ARM  5            /* CONFIRM wired */
#define PIN_TTL_TONE_ON     6            /* CONFIRM wired */
#define PIN_TTL_TONE_OFF    7            /* CONFIRM wired */

/* ============== Task logical sensor roles -> physical beams ==============
 * The order of the Beam enum in task_core.h is:
 *   0 ENTRY, 1 EXIT, 2 LCHOICE, 3 RCHOICE, 4 LRETURN, 5 RRETURN
 * SENSOR_PINS below must be listed in that exact order. */
/* Role->pin mapping verified on the rig with tmaze_test + rig_view: breaking a
 * physical beam lights the geometrically-correct marker. The PIN_BEAM_* NAMES do
 * not reflect physical position (they were labeled by slot order at wiring time),
 * so the aliases below look mismatched but the resulting pin NUMBERS are correct.
 * Physical truth: ENTRY=39 EXIT=42 LCHOICE=43 RCHOICE=41 LRETURN=38 RRETURN=40. */
#define PIN_S_CENTRAL_ENTRY  PIN_BEAM_LEFT         /* pin 39 - verified */
#define PIN_S_CENTRAL_EXIT   PIN_BEAM_CENTER       /* pin 42 - verified */
#define PIN_S_LEFT_CHOICE    PIN_BEAM_RIGHT        /* pin 43 - verified */
#define PIN_S_RIGHT_CHOICE   PIN_BEAM_RCHOICE      /* pin 41 - verified */
#define PIN_S_LEFT_RETURN    PIN_BEAM_CENTER_END   /* pin 38 - verified */
#define PIN_S_RIGHT_RETURN   PIN_BEAM_LCHOICE      /* pin 40 - verified */

#define SENSOR_PINS { PIN_S_CENTRAL_ENTRY, PIN_S_CENTRAL_EXIT, \
                      PIN_S_LEFT_CHOICE,   PIN_S_RIGHT_CHOICE, \
                      PIN_S_LEFT_RETURN,   PIN_S_RIGHT_RETURN }

#define LICK_PINS { PIN_LICK_0, PIN_LICK_1, PIN_LICK_2 }

/* ===================== Reward valve pins (one per port) ==================
 * These are SOLENOID_TABLE[port][REWARD_SOLENOID_INDEX], named by the per-pin
 * labels above. Listed in the order the Valve enum in task_core.h expects:
 * 0 LEFT, 1 RIGHT, 2 START(center). These track REWARD_SOLENOID_INDEX (== R2);
 * v1 only fires the center spout, but all three use R2 for consistency. */
#define PIN_VALVE_LEFT   PIN_SOL_LEFT_R2     /* left reward spout    CONFIRM */
#define PIN_VALVE_RIGHT  PIN_SOL_RIGHT_R2    /* right reward spout   CONFIRM */
#define PIN_VALVE_START  PIN_SOL_CENTER_R2   /* center/start spout (v1 reward = C.R2) */

#define VALVE_PINS { PIN_VALVE_LEFT, PIN_VALVE_RIGHT, PIN_VALVE_START }

/* ============================ Timing / debounce ========================= */
#define SENSOR_DEBOUNCE_MS   5      /* CONFIRM against IR-sensor behavior */
/* Single source of truth for valve-open time. Used by cue-guided-task (reward
 * + manual flush) and tmaze_test (R<port><sol> default), and is the default for
 * experiment_control's configurable pulse field. */
#define REWARD_PULSE_MS      300
#define TTL_PULSE_MS         5

/* ========================= Session / cue tunables ======================= */
#define ANIMAL_ID            "RAT001"
/* Default training mode at boot (Mode enum lives in task_core.h; this macro is
 * only expanded inside makeConfig() where Mode is in scope). FreeRoam is the
 * safe default: no trials run until the experimenter sends MODE,FULL.
 * see docs/decisions/0009 */
#define DEFAULT_MODE         Mode::FreeRoam
#define HIGH_TONE_IS_LEFT    true    /* true: high tone -> left, low -> right */
/* Cue source at boot. false = BEAM: the firmware plays the tone the whole time
 * it is in Phase::CueRunning (v4 behavior). true = VISION: the firmware
 * suppresses that auto-tone and the HOST drives the tone via CUE_ON/CUE_OFF
 * (the positional, vision-gated cue). Switchable at runtime with CUE,BEAM /
 * CUE,VISION. see docs/decisions/0044 */
#define DEFAULT_CUE_SOURCE_VISION false
/* v1 reward is delivered at the central port, contingent on a correct choice
 * (see docs/decisions/0007). This flag additionally rewards the central port on
 * INCORRECT trials too (free/shaping reward); false = correct-only. */
#define START_REWARD_ENABLED false
#define MAX_TRIALS           200
#define MAX_TIME_MS          3600000UL   /* 1 h */
#define BLOCK_SIZE           20          /* must divide MAX_TRIALS; half L / half R */
/* Anti-bias: max consecutive same-side trials allowed in the generated
 * sequence (0 disables). Long same-side runs let rats develop a side bias;
 * cf. Holleman et al. 2019, Sci Rep 9:12589. see docs/decisions/0009 */
#define MAX_SAME_RUN         3
/* Auto well-clean: at the start of each trial - the return-arm -> central-entry
 * transition the trial state machine already enforces - rinse then drain the
 * central well so each trial's reward is fresh and the line stays clean. The
 * DRAIN must evacuate faster than the RINSE fills, and the clean fires near cue
 * onset (an added solenoid click during the cue) - both acceptable but noted.
 * Set AUTO_DRAIN_ON_TRIAL false to disable. see docs/decisions/0010 */
#define AUTO_DRAIN_ON_TRIAL  true
#define WELL_RINSE_MS        150         /* CONFIRM against the well/plumbing */
#define WELL_DRAIN_MS        300         /* CONFIRM: must clear what the rinse adds */
#define TONE_HIGH_HZ         8000
#define TONE_LOW_HZ          4000
/* Clamp band for a host-supplied cue frequency (CUE_ON,<kind>,<hz>, v6). The
 * host owns tone identity in the host-owned Phase 3; this bounds a bad value so
 * it can never drive the DAC out of a sane range. The wavetable ISR is cleanest
 * at/below ~8 kHz (>=10 samples/cycle at DAC_SAMPLE_RATE), so keep host tones in
 * this band. see docs/decisions/0046 */
#define CUE_HZ_MIN           500
#define CUE_HZ_MAX           20000
/* Per-cue playback volume as a PERCENT of full DAC amplitude (CUE_ON,<kind>,
 * <hz>,<gain>, v7). A cheap speaker's response is not flat, so the HIGH and LOW
 * cues can measure at different dB even at equal amplitude; this lets the host
 * ATTENUATE the louder cue to equalize them (100 = today's full level, the
 * default when no gain is sent). We only go DOWN because DAC_AMPLITUDE already
 * sits just under the clip ceiling. see docs/decisions/0125 */
#define CUE_GAIN_PCT_MIN     0
#define CUE_GAIN_PCT_MAX     100
#define CUE_GAIN_PCT_DEFAULT 100

/* DAC / audio (Arduino Due). The tone ISR is wavetable-based (integer-only, see
 * sound.h), so this is cheap. 80 kHz gives a clean sine (>=10 samples/cycle at
 * the 8 kHz tone) with large CPU headroom for loop() + native USB. */
#define DAC_SAMPLE_RATE      80000.0f    /* Hz */

/* ================================ Protocol ============================== */
/* v8 corrects the body-beam and lick STATE polarity. The Due-pin sense was
 * inverted relative to the physical event (the NOYIYO opto is "Normal Low /
 * Actuated High" for beams; the lick pin idles HIGH and is pulled LOW on
 * contact), so pre-v8 firmware printed S,<i>,1 / L,<i>,1 for the RESTING state
 * and pulsed the FSM/TTL on beam CLEAR. v8 flips the firmware read so S,<i>,1 =
 * beam broken and L,<i>,1 = tongue contact again (also fixing the FSM/TTL edge
 * and the boot state). This is a SEMANTIC break, not an appended field, hence
 * the version bump; recorded bundles carry a per-bundle sensor_polarity flag so
 * the analysis can tell pre-v8 (inverted) from v8 (corrected) sessions.
 * see docs/decisions/0164
 * v7 lets the host set a per-cue playback volume: CUE_ON,<CHOICE|RETURN>,<hz>,
 * <gain> where <gain> is a percent 0..100 of full DAC amplitude (optional and
 * clamped to [CUE_GAIN_PCT_MIN, CUE_GAIN_PCT_MAX]; absent == 100 == v6 level).
 * The firmware scales the tone in the wavetable ISR (integer Q8, see sound.h)
 * and echoes the applied gain: CUE,ON,<type>,<freq>,<gain>. Used to equalize
 * the HIGH/LOW cues on a non-flat speaker. see docs/decisions/0125.
 * v6 lets the host supply the cue tone identity explicitly:
 * CUE_ON,<CHOICE|RETURN>,<hz> (the <hz> is optional and clamped to
 * [CUE_HZ_MIN, CUE_HZ_MAX]); without it the firmware resolves identity as in v5.
 * v6 also fires the choice / return-arm TTLs autonomously on their beam edges in
 * Free Roam, so the host-owned Phase 3 keeps the Open Ephys event marks while the
 * firmware trial FSM is off. see docs/decisions/0046.
 * v5 adds the host-driven positional audio cue: commands CUE_ON,<CHOICE|RETURN>,
 * CUE_OFF, and the cue-source flag CUE,VISION|CUE,BEAM, plus the firmware
 * CUE,ON,<type>,<freq> / CUE,OFF log line. Under CUE,VISION the firmware
 * suppresses its beam-driven CueRunning auto-tone so the host is the single DAC
 * authority. see docs/decisions/0044.
 * v4 adds a trailing pulse-duration field (ms) to the reward/solenoid lines so
 * the host can map each pulse to a calibrated dispensed volume:
 * REWARD,CENTER,<dur_ms>, REWARD,<port>,MANUAL,<dur_ms>, and SOL,<port>,<sol>,
 * <dur_ms> (cue-guided-task only). see docs/decisions/0019.
 * v3 added: training modes (MODE command + 0,MODE,<name> line), manual-reward
 * logging (REWARD,<port>,MANUAL), and SESSION_SUMMARY (see docs/decisions/0009);
 * plus full manual solenoid control in cue-guided-task (R<port><sol>[,<ms>] ->
 * SOL ack) and auto well-clean SOL lines at trial start (docs/decisions/0010). */
#define PROTO_VERSION        8

#endif /* RIG_CONFIG_H */
