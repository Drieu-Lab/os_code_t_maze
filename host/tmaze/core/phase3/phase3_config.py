"""
phase3_config.py - config-driven parameters for host-owned Phase 3 (pure core).

Phase 3's reward, cue, and sequence knobs used to be hardcoded frozen dataclasses
(`Phase3Config` / `CueParams` / `TrialConfig`) plus ephemeral Tk vars that reset
every launch, unlike the Rules engine which is fully driven by a versioned
`rules.json` with load/validate/write-back (docs/decisions/0031). This core closes
that gap for Phase 3 (FW-0024): a versioned `host/phase3.json` with three
sections, loaded with defaults that reproduce today's behavior, validated before
use, and written back from the Phase 3 tab's "Save settings" button.

Layering (like `rules_engine`'s editor support): this is a PURE core - it reads /
merges / validates / resolves the config dict and builds the three frozen
dataclasses, but does no Tk / file-watching / arming. The thin Phase 3 controller
shell owns the file path, the load-at-startup, the Save/Reload buttons, and
applying the resolved values on arm.

Division of the sections (see docs/decisions/0070):
  - ``reward`` (port, pulse_ms, max_topups, reward_incorrect) and ``cue``
    (choice/return enabled, require_up_stem, min_confidence) are operator knobs,
    editable from the tab and persisted by Save.
  - ``sequence`` (max_trials, block_size, max_same_run, tone Hz, high_tone_is_left,
    seed) holds the SCIENTIFIC schedule parameters. These are hand-edited in
    `phase3.json` only (shown read-only in the tab) so the balancing / tone
    identities are deliberate, not fat-fingered mid-session. ``seed`` is optional:
    null/absent -> a fresh random seed each launch (reproducible only within a
    session); an integer pins the schedule across launches.

Defaults are DERIVED from the dataclass defaults below, so the config can never
silently drift from the code's built-in behavior. Missing keys fall back to those
defaults; unknown keys (e.g. ``_schema``) are preserved on round-trip.

Pure and stdlib-only (json). Headless tests: python -m unittest test_phase3_config.
"""

from __future__ import annotations

import json
from typing import Optional

from tmaze.core.cue_gate import CueParams
from tmaze.core.phase3.debias import DebiasBlockConfig
from tmaze.core.phase3.phase3_task import Phase3Config
from tmaze.core.phase3.trial_sequence import (
    GEN_BALANCED, GEN_DEBIAS_BLOCKS, TrialConfig,
)

# Default number of lick top-ups at the well after the correct-choice prime
# (ADR-0069). Not a field on Phase3Config (it belongs to the WellDispenser the
# controller builds), so it is named here as the single default the config +
# the tab both start from.
DEFAULT_MAX_TOPUPS = 3

# 32-bit mask so a pinned seed matches the sequence PRNG (trial_sequence.lcg).
_SEED_MASK = 0xFFFFFFFF

# The three config sections; any other top-level key (``_schema``, ...) is left
# untouched on load/merge/save so the file can carry its own documentation.
_SECTIONS = ("reward", "cue", "sequence")

# A short, self-describing block written into a fresh phase3.json so the file
# explains itself (mirrors rules.json's ``_schema``). Preserved on round-trip.
_SCHEMA = {
    "note": ("Host-owned Phase 3 config (FW-0024, ADR-0070). Delete this file to "
             "reset to built-in defaults. 'reward' + 'cue' + the sequence "
             "'generator'/'debias_blocks' are editable from the Phase 3 tab (Save "
             "settings); the rest of 'sequence' holds scientific schedule params - "
             "hand-edit here only. seed: null = fresh random each launch, an "
             "integer pins the schedule. generator: 'balanced' (counterbalanced, "
             "default) or 'debias_blocks' (counterbalanced + closed-loop corrective "
             "blocks when a side bias develops; params in 'debias_blocks')."),
}


def default_config() -> dict:
    """A fresh config dict with every section at its built-in default.

    Values are read off the frozen dataclass defaults (`Phase3Config` /
    `CueParams` / `TrialConfig`) so this can never drift from the code's default
    behavior, plus ``max_topups`` (a WellDispenser knob) and an optional ``seed``.
    Includes the ``_schema`` doc block so a first Save writes a self-explaining
    file.
    """
    pc, cp, tc, db = Phase3Config(), CueParams(), TrialConfig(), DebiasBlockConfig()
    return {
        "_schema": dict(_SCHEMA),
        "reward": {
            "port": pc.reward_port,
            "pulse_ms": pc.reward_pulse_ms,
            "max_topups": DEFAULT_MAX_TOPUPS,
            "reward_incorrect": pc.reward_incorrect,
        },
        "cue": {
            "choice_enabled": cp.choice_enabled,
            "return_enabled": cp.return_enabled,
            "require_up_stem": cp.require_up_stem,
            "min_confidence": cp.min_confidence,
        },
        "sequence": {
            "max_trials": tc.max_trials,
            "block_size": tc.block_size,
            "max_same_run": tc.max_same_run,
            "high_tone_is_left": tc.high_tone_is_left,
            "high_tone_hz": tc.high_tone_hz,
            "low_tone_hz": tc.low_tone_hz,
            "seed": None,
            # Trial-sequence generator + the closed-loop debias-block params it uses
            # (only when generator == "debias_blocks"; see debias.py, ADR).
            "generator": tc.generator,
            "debias_blocks": {
                "window": db.window,
                "min_samples": db.min_samples,
                "trigger_bias": db.trigger_bias,
                "exit_consecutive": db.exit_consecutive,
                "max_block_len": db.max_block_len,
                "signal": db.signal,
            },
        },
    }


def load_phase3_config(path: str) -> dict:
    """Load ``path`` merged over the built-in defaults; defaults if it is absent.

    A present file's known section keys OVERRIDE the defaults (missing keys fall
    back), and any extra top-level keys (e.g. ``_schema``) are carried through.
    Raises ``ValueError`` if the file is not a JSON object; a JSON syntax error
    propagates as ``json.JSONDecodeError`` for the caller to surface. This does
    NOT validate values - call :func:`validate_phase3_config` for that.
    """
    cfg = default_config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return cfg
    if not isinstance(raw, dict):
        raise ValueError("phase3.json must be a JSON object")
    for sec in _SECTIONS:
        val = raw.get(sec)
        if isinstance(val, dict):
            cfg[sec].update(val)
    for key, val in raw.items():          # preserve _schema + any unknown keys
        if key not in _SECTIONS:
            cfg[key] = val
    return cfg


def save_phase3_config(path: str, config: dict) -> None:
    """Write ``config`` back to ``path`` (pretty-printed), round-tripping
    :func:`load_phase3_config`. Callers mutate only the edited keys in place, so
    the ``_schema`` block and any untouched keys are preserved. Validate BEFORE
    calling this (mirrors docs/decisions/0031)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ------------------------------------------------------------------ resolvers
# Each builds a frozen dataclass (or scalar) from the config dict, coercing types
# and range-checking. They raise ValueError/TypeError on malformed input so a
# caller can reject an edit before it reaches disk or the actuation thread.

def to_phase3_config(config: dict) -> Phase3Config:
    """Build the task-policy `Phase3Config` (reward port / pulse / incorrect)."""
    r = config.get("reward", {})
    pulse = int(r.get("pulse_ms", 500))
    if pulse <= 0:
        raise ValueError("reward.pulse_ms must be > 0")
    port = int(r.get("port", 2))
    return Phase3Config(reward_incorrect=bool(r.get("reward_incorrect", False)),
                        reward_port=port, reward_pulse_ms=pulse)


def reward_max_topups(config: dict) -> int:
    """Max lick top-ups at the well after the prime (>= 0; 0 = none)."""
    n = int(config.get("reward", {}).get("max_topups", DEFAULT_MAX_TOPUPS))
    if n < 0:
        raise ValueError("reward.max_topups must be >= 0")
    return n


def to_cue_params(config: dict) -> CueParams:
    """Build the positional-cue `CueParams` (enables + confidence + up-stem)."""
    c = config.get("cue", {})
    mc = float(c.get("min_confidence", 0.0))
    if not 0.0 <= mc <= 1.0:
        raise ValueError("cue.min_confidence must be in [0, 1]")
    return CueParams(choice_enabled=bool(c.get("choice_enabled", True)),
                     return_enabled=bool(c.get("return_enabled", True)),
                     min_confidence=mc,
                     require_up_stem=bool(c.get("require_up_stem", True)))


def _to_debias_config(d: dict) -> DebiasBlockConfig:
    """Build + range-check the closed-loop `DebiasBlockConfig` from the
    ``sequence.debias_blocks`` sub-section (missing keys fall back to defaults)."""
    db = DebiasBlockConfig()
    window = int(d.get("window", db.window))
    min_samples = int(d.get("min_samples", db.min_samples))
    trigger_bias = float(d.get("trigger_bias", db.trigger_bias))
    exit_consecutive = int(d.get("exit_consecutive", db.exit_consecutive))
    max_block_len = int(d.get("max_block_len", db.max_block_len))
    signal = str(d.get("signal", db.signal))
    if window <= 0:
        raise ValueError("sequence.debias_blocks.window must be > 0")
    if not 0 < min_samples <= window:
        raise ValueError("sequence.debias_blocks.min_samples must be in (0, window]")
    if not 0.0 <= trigger_bias <= 1.0:
        raise ValueError("sequence.debias_blocks.trigger_bias must be in [0, 1]")
    if exit_consecutive <= 0:
        raise ValueError("sequence.debias_blocks.exit_consecutive must be > 0")
    if max_block_len <= 0:
        raise ValueError("sequence.debias_blocks.max_block_len must be > 0")
    return DebiasBlockConfig(window=window, min_samples=min_samples,
                             trigger_bias=trigger_bias,
                             exit_consecutive=exit_consecutive,
                             max_block_len=max_block_len, signal=signal)


def to_trial_config(config: dict) -> TrialConfig:
    """Build the schedule/tone `TrialConfig` from the ``sequence`` section."""
    s = config.get("sequence", {})
    max_trials = int(s.get("max_trials", 200))
    block_size = int(s.get("block_size", 20))
    max_same_run = int(s.get("max_same_run", 4))
    high_hz = int(s.get("high_tone_hz", 8000))
    low_hz = int(s.get("low_tone_hz", 4000))
    generator = str(s.get("generator", GEN_BALANCED))
    if max_trials <= 0:
        raise ValueError("sequence.max_trials must be > 0")
    if block_size <= 0:
        raise ValueError("sequence.block_size must be > 0")
    if max_same_run < 0:
        raise ValueError("sequence.max_same_run must be >= 0")
    if high_hz <= 0 or low_hz <= 0:
        raise ValueError("sequence tone Hz must be > 0")
    if generator not in (GEN_BALANCED, GEN_DEBIAS_BLOCKS):
        raise ValueError(
            f"sequence.generator must be {GEN_BALANCED!r} or {GEN_DEBIAS_BLOCKS!r}")
    # Always validate the debias params (so a bad value is caught on Save even if
    # not currently selected), but only ATTACH them when the debias generator is
    # active - so the balanced default stays debias=None (identical to TrialConfig).
    debias_cfg = _to_debias_config(s.get("debias_blocks", {}))
    debias = debias_cfg if generator == GEN_DEBIAS_BLOCKS else None
    return TrialConfig(max_trials=max_trials, block_size=block_size,
                       max_same_run=max_same_run,
                       high_tone_is_left=bool(s.get("high_tone_is_left", True)),
                       high_tone_hz=high_hz, low_tone_hz=low_hz,
                       generator=generator, debias=debias)


def resolve_seed(config: dict) -> Optional[int]:
    """The pinned sequence seed, or None to draw a fresh random one each launch.

    ``sequence.seed`` null/absent -> None; an integer is masked to 32 bits."""
    s = config.get("sequence", {}).get("seed", None)
    if s is None:
        return None
    return int(s) & _SEED_MASK


def validate_phase3_config(config: dict) -> None:
    """Raise if any value is malformed, by resolving every section (a clean
    return means the config will build). The Save button calls this to guard a
    write, and the controller calls it after load to fall back safely."""
    to_phase3_config(config)
    reward_max_topups(config)
    to_cue_params(config)
    to_trial_config(config)
    resolve_seed(config)
