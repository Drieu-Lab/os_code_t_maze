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
/* Body-beam sensors (active-LOW, INPUT_PULLUP). Pins from tmaze_test. */
#define PIN_BEAM_LEFT        38
#define PIN_BEAM_CENTER      39
#define PIN_BEAM_RIGHT       40
#define PIN_BEAM_RCHOICE     41
#define PIN_BEAM_CENTER_END  42
#define PIN_BEAM_LCHOICE     43

/* Lick detectors (active-HIGH event per tmaze_test). */
#define PIN_LICK_0  35
#define PIN_LICK_1  36
#define PIN_LICK_2  37

/* Reward solenoids [port][R1,R2,R3,DRAIN,RINSE]; ports: 0=left 1=right 2=center.
 * Primarily used by tmaze_test; the cue-guided task drives one spout per port. */
#define SOLENOIDS_PER_PORT 5
#define SOLENOID_TABLE { {23,24,25,26,44}, {27,28,29,30,45}, {31,32,33,34,46} }

/* Which solenoid index (column) per port the task pulses as the reward spout. */
#define REWARD_SOLENOID_INDEX 0          /* CONFIRM */

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
#define PIN_S_CENTRAL_ENTRY  PIN_BEAM_CENTER       /* CONFIRM */
#define PIN_S_CENTRAL_EXIT   PIN_BEAM_CENTER_END   /* CONFIRM */
#define PIN_S_LEFT_CHOICE    PIN_BEAM_LCHOICE      /* confident */
#define PIN_S_RIGHT_CHOICE   PIN_BEAM_RCHOICE      /* confident */
#define PIN_S_LEFT_RETURN    PIN_BEAM_LEFT         /* CONFIRM */
#define PIN_S_RIGHT_RETURN   PIN_BEAM_RIGHT        /* CONFIRM */

#define SENSOR_PINS { PIN_S_CENTRAL_ENTRY, PIN_S_CENTRAL_EXIT, \
                      PIN_S_LEFT_CHOICE,   PIN_S_RIGHT_CHOICE, \
                      PIN_S_LEFT_RETURN,   PIN_S_RIGHT_RETURN }

#define LICK_PINS { PIN_LICK_0, PIN_LICK_1, PIN_LICK_2 }

/* ===================== Reward valve pins (one per port) ==================
 * These are SOLENOID_TABLE[port][REWARD_SOLENOID_INDEX]. Listed in the order
 * the Valve enum in task_core.h expects: 0 LEFT, 1 RIGHT, 2 START(center).
 * If REWARD_SOLENOID_INDEX changes, update these too. CONFIRM. */
#define PIN_VALVE_LEFT   23   /* left[0]   CONFIRM */
#define PIN_VALVE_RIGHT  27   /* right[0]  CONFIRM */
#define PIN_VALVE_START  31   /* center[0] CONFIRM (center port == "Start") */

#define VALVE_PINS { PIN_VALVE_LEFT, PIN_VALVE_RIGHT, PIN_VALVE_START }

/* ============================ Timing / debounce ========================= */
#define SENSOR_DEBOUNCE_MS   5      /* CONFIRM against IR-sensor behavior */
#define REWARD_PULSE_MS      100
#define TTL_PULSE_MS         5

/* ========================= Session / cue tunables ======================= */
#define ANIMAL_ID            "RAT001"
#define HIGH_TONE_IS_LEFT    true    /* true: high tone -> left, low -> right */
#define START_REWARD_ENABLED false   /* optional reward at start/center port on return */
#define MAX_TRIALS           200
#define MAX_TIME_MS          3600000UL   /* 1 h */
#define BLOCK_SIZE           20          /* must divide MAX_TRIALS; half L / half R */
#define TONE_HIGH_HZ         8000
#define TONE_LOW_HZ          4000

/* DAC / audio (Arduino Due). */
#define DAC_SAMPLE_RATE      100000.0f   /* Hz */

/* ================================ Protocol ============================== */
#define PROTO_VERSION        2

#endif /* RIG_CONFIG_H */
