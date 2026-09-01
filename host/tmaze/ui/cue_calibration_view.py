"""
cue_calibration_view.py - "Cue calibration" subtab (under Setup).

A cheap speaker is imperfect in two ways this tab corrects, per cue:

  - VOLUME: its frequency response is not flat, so HIGH and LOW cues measure at
    different loudness even at equal amplitude. Play each cue at an adjustable
    volume (percent of full DAC amplitude), balance by ear / a dB meter, Save.
  - FREQUENCY: the emitted tone sits slightly BELOW the commanded frequency.
    Enter the MEASURED actual frequency (from a spectrum app) and Set; the tab
    computes a corrected commanded value (commanded x target / measured) so the
    speaker emits the target. Re-play and re-measure to refine.

Both are saved to ``host/cue_calibration.json`` (pure core
``tmaze.core.cue_calibration``) and auto-applied wherever a cue is sent - the
Phase 3 task and the Rig-view speaker test - so calibrating here changes what the
rat hears. See docs/decisions/0125 (volume) and 0148 (frequency).

The rig has no microphone, so calibration is operator-driven: play, measure,
adjust, Save. Volume can only be ATTENUATED (100 % is the firmware's full level);
to equalize, turn the LOUDER cue down to match the quieter one, then restore
overall level with the speaker's own gain potentiometer.

Module contract (like the other tabs):
    widget() -> a Tk widget to place in its tab
    apply(info) -> None   (no serial input needed; a no-op)
    close() -> None       silence any test tone on exit
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

from tmaze.core import cue_calibration
from tmaze.core.phase3.phase3_config import load_phase3_config
from tmaze.paths import host_dir

try:
    from tmaze.ui.tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None

# Firmware defaults (mirror TONE_HIGH_HZ / TONE_LOW_HZ in
# firmware/common/rig_config.h), used when phase3.json can't be read.
_DEFAULT_HIGH_HZ = 8000
_DEFAULT_LOW_HZ = 4000
# How long a test tone plays before the host sends CUE_OFF (so a preview doesn't
# sound forever); the operator re-presses Play to hear it again.
_TEST_BURST_MS = 700
# Cue kind sent for the test. Identity does not affect the tone when the host
# supplies the frequency (it only labels the log line / TTL), so CHOICE is fine.
_TEST_KIND = "CHOICE"


def _phase3_cue_freqs() -> List[Tuple[str, int]]:
    """The (label, frequency_hz) cues to calibrate, read from phase3.json so the
    keys match the tones Phase 3 actually plays; falls back to the firmware
    defaults if the config can't be read."""
    hi, lo = _DEFAULT_HIGH_HZ, _DEFAULT_LOW_HZ
    try:
        cfg = load_phase3_config(str(host_dir() / "phase3.json"))
        seq = cfg.get("sequence", {})
        hi = int(seq.get("high_tone_hz", hi))
        lo = int(seq.get("low_tone_hz", lo))
    except Exception:
        pass
    return [("HIGH", hi), ("LOW", lo)]


class CueCalibrationView:
    """Balance cue volume and correct cue frequency, then Save (ADR-0125/0148)."""

    def __init__(self, parent: tk.Misc, *, send: Callable[[str], None],
                 config_path=None) -> None:
        self._send = send
        self._path = config_path or cue_calibration.default_calibration_path()
        self._cues = _phase3_cue_freqs()
        self._pct_vars: Dict[int, tk.IntVar] = {}
        # Per-target: the current COMMANDED Hz (what we actually send), the
        # operator's measured-actual entry, and the label that echoes commanded.
        self._commanded: Dict[int, int] = {}
        self._measured_vars: Dict[int, tk.StringVar] = {}
        self._commanded_vars: Dict[int, tk.StringVar] = {}
        self._after_id: Optional[str] = None

        saved_gains: Dict[int, int] = {}
        saved_cmd: Dict[int, int] = {}
        try:
            saved_gains = cue_calibration.load_gains(self._path)
        except (OSError, ValueError):
            saved_gains = {}
        try:
            saved_cmd = cue_calibration.load_commanded(self._path)
        except (OSError, ValueError):
            saved_cmd = {}

        frame = ttk.LabelFrame(
            parent, text="Cue calibration  -  balance volume + correct frequency, then Save")
        self._frame = frame

        intro = ("VOLUME: play each cue and turn the LOUDER one DOWN (dB meter) "
                 "until they match.  FREQUENCY: enter the measured actual Hz (a "
                 "spectrum app) and\nSet - the commanded tone is corrected so the "
                 "speaker emits the target.  Saved values auto-apply to Phase 3 "
                 "and the speaker test.")
        ttk.Label(frame, text=intro, justify="left",
                  font=("TkDefaultFont", 8)).grid(
            row=0, column=0, columnspan=8, sticky="w", padx=6, pady=(6, 8))

        # ---- Volume section ----
        ttk.Label(frame, text="Volume", font=("TkDefaultFont", 8, "bold")).grid(
            row=1, column=0, sticky="w", padx=6, pady=(2, 2))
        for i, (label, hz) in enumerate(self._cues):
            row = i + 2
            var = tk.IntVar(
                value=int(saved_gains.get(hz, cue_calibration.DEFAULT_GAIN_PCT)))
            self._pct_vars[hz] = var
            ttk.Label(frame, text=f"{label} ({hz} Hz)",
                      width=14).grid(row=row, column=0, sticky="w", padx=(6, 4),
                                     pady=3)
            scale = ttk.Scale(frame, from_=cue_calibration.GAIN_PCT_MIN,
                              to=cue_calibration.GAIN_PCT_MAX, orient="horizontal",
                              length=200,
                              command=lambda v, hv=hz: self._on_scale(hv, v))
            scale.set(var.get())
            scale.grid(row=row, column=1, sticky="we", padx=4, pady=3)
            tip(scale, "cue.volume")
            spin = ttk.Spinbox(frame, from_=cue_calibration.GAIN_PCT_MIN,
                               to=cue_calibration.GAIN_PCT_MAX, increment=1, width=5,
                               textvariable=var,
                               command=lambda hv=hz, sc=scale: self._sync_scale(hv, sc))
            spin.grid(row=row, column=2, sticky="w", padx=4, pady=3)
            ttk.Label(frame, text="%").grid(row=row, column=3, sticky="w")
            play = ttk.Button(frame, text=f"Play {label}",
                              command=lambda hv=hz: self._play(hv))
            play.grid(row=row, column=4, padx=6, pady=3)
            tip(play, "cue.volume.play")

        # ---- Frequency section ----
        freq_row0 = len(self._cues) + 2
        ttk.Separator(frame, orient="horizontal").grid(
            row=freq_row0, column=0, columnspan=8, sticky="we", padx=6, pady=(8, 4))
        ttk.Label(frame, text="Frequency", font=("TkDefaultFont", 8, "bold")).grid(
            row=freq_row0 + 1, column=0, sticky="w", padx=6, pady=(2, 2))
        for i, (label, hz) in enumerate(self._cues):
            row = freq_row0 + 2 + i
            self._commanded[hz] = int(saved_cmd.get(hz, hz))
            ttk.Label(frame, text=f"{label} ({hz} Hz)",
                      width=14).grid(row=row, column=0, sticky="w", padx=(6, 4),
                                     pady=3)
            ttk.Label(frame, text="measured Hz:").grid(
                row=row, column=1, sticky="e", padx=(4, 2))
            meas = tk.StringVar(value="")
            self._measured_vars[hz] = meas
            ent = ttk.Spinbox(frame, from_=cue_calibration.FREQ_HZ_MIN,
                              to=cue_calibration.FREQ_HZ_MAX, increment=1, width=8,
                              textvariable=meas)
            ent.grid(row=row, column=2, sticky="w", padx=4, pady=3)
            tip(ent, "cue.freq")
            set_btn = ttk.Button(frame, text="Set",
                                 command=lambda hv=hz: self._set_freq(hv))
            set_btn.grid(row=row, column=3, padx=4, pady=3)
            tip(set_btn, "cue.freq.set")
            cmd_var = tk.StringVar(value=f"cmd {self._commanded[hz]} Hz")
            self._commanded_vars[hz] = cmd_var
            ttk.Label(frame, textvariable=cmd_var, width=12).grid(
                row=row, column=4, sticky="w", padx=(6, 2))
            play_f = ttk.Button(frame, text=f"Play {label}",
                                command=lambda hv=hz: self._play_commanded(hv))
            play_f.grid(row=row, column=5, padx=4, pady=3)
            tip(play_f, "cue.freq.play")
            reset_btn = ttk.Button(frame, text="Reset",
                                   command=lambda hv=hz: self._reset_freq(hv))
            reset_btn.grid(row=row, column=6, padx=(2, 6), pady=3)

        # ---- Controls ----
        ctl = ttk.Frame(frame)
        ctl.grid(row=freq_row0 + 2 + len(self._cues), column=0, columnspan=8,
                 sticky="we", padx=6, pady=(8, 6))
        stop_btn = ttk.Button(ctl, text="Stop", command=self._stop)
        stop_btn.pack(side="left")
        tip(stop_btn, "cue.stop")
        save_btn = ttk.Button(ctl, text="Save", command=self._save)
        save_btn.pack(side="left", padx=(8, 0))
        tip(save_btn, "cue.volume.save")
        self._status = tk.StringVar(value="")
        ttk.Label(ctl, textvariable=self._status,
                  font=("TkDefaultFont", 8)).pack(side="left", padx=(10, 0))

        frame.columnconfigure(1, weight=1)

    # ----- module contract -----
    def widget(self) -> tk.Widget:
        return self._frame

    def apply(self, _info) -> None:
        # No serial input needed; the tab only sends test tones.
        return None

    def close(self) -> None:
        self._stop()

    # ----- slider/spinbox sync -----
    def _on_scale(self, hz: int, raw: str) -> None:
        try:
            self._pct_vars[hz].set(int(round(float(raw))))
        except (TypeError, ValueError):
            pass

    def _sync_scale(self, hz: int, scale: ttk.Scale) -> None:
        try:
            scale.set(self._pct_vars[hz].get())
        except tk.TclError:
            pass

    # ----- frequency correction -----
    def _set_freq(self, hz: int) -> None:
        """Correct the commanded tone from the operator's measured value:
        commanded_new = round(commanded_current * target / measured)."""
        raw = self._measured_vars[hz].get().strip()
        try:
            measured = float(raw)
        except (TypeError, ValueError):
            self._status.set(f"{hz} Hz: enter a numeric measured frequency.")
            return
        if measured <= 0:
            self._status.set(f"{hz} Hz: measured frequency must be positive.")
            return
        current = self._commanded[hz]
        new_cmd = cue_calibration.clamp_freq_hz(
            round(current * hz / measured), default=hz)
        self._commanded[hz] = int(new_cmd)
        self._commanded_vars[hz].set(f"cmd {self._commanded[hz]} Hz")
        self._status.set(
            f"{hz} Hz: measured {measured:g} -> commanded {self._commanded[hz]}. "
            "Play + re-measure to refine.")

    def _reset_freq(self, hz: int) -> None:
        self._commanded[hz] = hz
        self._commanded_vars[hz].set(f"cmd {hz} Hz")
        self._measured_vars[hz].set("")
        self._status.set(f"{hz} Hz: frequency reset to target.")

    # ----- playback -----
    def _pct(self, hz: int) -> int:
        return cue_calibration.clamp_gain_pct(self._pct_vars[hz].get())

    def _stop(self) -> None:
        if self._after_id is not None:
            try:
                self._frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self._send("CUE_OFF")

    def _play(self, hz: int) -> None:
        # Volume preview: play the TARGET tone at the live percent (frequency
        # correction is previewed separately by the frequency-section Play).
        self._stop()
        self._send(f"CUE_ON,{_TEST_KIND},{hz},{self._pct(hz)}")
        self._after_id = self._frame.after(_TEST_BURST_MS, self._stop)

    def _play_commanded(self, hz: int) -> None:
        # Frequency preview: play the CURRENT commanded tone (so the operator can
        # measure the emitted frequency), at this cue's calibrated volume.
        self._stop()
        cf = self._commanded[hz]
        self._send(f"CUE_ON,{_TEST_KIND},{cf},{self._pct(hz)}")
        self._after_id = self._frame.after(_TEST_BURST_MS, self._stop)

    # ----- save -----
    def _save(self) -> None:
        gains = {hz: self._pct(hz) for hz in self._pct_vars}
        commanded = {hz: int(self._commanded[hz]) for hz in self._commanded}
        try:
            cue_calibration.save_calibration(self._path, gains, commanded)
            cue_calibration.reload_calibration(self._path)
        except OSError as e:
            self._status.set(f"Save failed: {e}")
            return
        vol = ", ".join(f"{lbl} {self._pct(hz)}%" for lbl, hz in self._cues)
        frq = ", ".join(f"{lbl} {self._commanded[hz]}Hz" for lbl, hz in self._cues)
        self._status.set(f"Saved (vol: {vol}; freq: {frq}).")
