"""
cue_volume_view.py - "Cue volume" calibration subtab (under Setup).

A cheap speaker's frequency response is not flat, so the HIGH and LOW cues can
measure at different loudness even at equal amplitude. This tab lets the operator
play each cue at an adjustable volume (a percent of full DAC amplitude), balance
them by ear / an external dB meter, and Save the result. The saved balance lives
in ``host/cue_calibration.json`` (pure core ``tmaze.core.cue_volume``) and is then
auto-applied wherever a cue is sent - the Phase 3 task and the Rig-view speaker
test - so calibrating here changes what the rat hears. See docs/decisions/0125.

The rig has no microphone, so calibration is operator-driven: play, adjust, Save.
Volume can only be ATTENUATED (100 % is the firmware's full level); to equalize,
turn the LOUDER cue down to match the quieter one, then restore overall level
with the speaker's own gain potentiometer.

Module contract (like the other tabs):
    widget() -> a Tk widget to place in its tab
    apply(info) -> None   (no serial input needed; a no-op)
    close() -> None       silence any test tone on exit
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

from tmaze.core import cue_volume
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


class CueVolumeView:
    """Play each cue at an adjustable volume and save the balance (ADR-0125)."""

    def __init__(self, parent: tk.Misc, *, send: Callable[[str], None],
                 config_path=None) -> None:
        self._send = send
        self._path = config_path or cue_volume.default_calibration_path()
        self._cues = _phase3_cue_freqs()
        self._pct_vars: Dict[int, tk.IntVar] = {}
        self._after_id: Optional[str] = None

        saved = {}
        try:
            saved = cue_volume.load_gains(self._path)
        except (OSError, ValueError):
            saved = {}

        frame = ttk.LabelFrame(
            parent, text="Cue volume  -  balance HIGH vs LOW, then Save")
        self._frame = frame

        intro = ("Play each cue and turn the LOUDER one DOWN (with a dB meter) "
                 "until they match, then Save.\nSaved levels auto-apply to Phase 3 "
                 "and the speaker test. 100 % = full; overall\nloudness is set with "
                 "the speaker's volume knob.")
        ttk.Label(frame, text=intro, justify="left",
                  font=("TkDefaultFont", 8)).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=6, pady=(6, 8))

        for i, (label, hz) in enumerate(self._cues):
            row = i + 1
            var = tk.IntVar(value=int(saved.get(hz, cue_volume.DEFAULT_GAIN_PCT)))
            self._pct_vars[hz] = var
            ttk.Label(frame, text=f"{label} ({hz} Hz)",
                      width=14).grid(row=row, column=0, sticky="w", padx=(6, 4),
                                     pady=3)
            scale = ttk.Scale(frame, from_=cue_volume.GAIN_PCT_MIN,
                              to=cue_volume.GAIN_PCT_MAX, orient="horizontal",
                              length=200,
                              command=lambda v, hv=hz: self._on_scale(hv, v))
            scale.set(var.get())
            scale.grid(row=row, column=1, sticky="we", padx=4, pady=3)
            tip(scale, "cue.volume")
            spin = ttk.Spinbox(frame, from_=cue_volume.GAIN_PCT_MIN,
                               to=cue_volume.GAIN_PCT_MAX, increment=1, width=5,
                               textvariable=var,
                               command=lambda hv=hz, sc=scale: self._sync_scale(hv, sc))
            spin.grid(row=row, column=2, sticky="w", padx=4, pady=3)
            ttk.Label(frame, text="%").grid(row=row, column=3, sticky="w")
            play = ttk.Button(frame, text=f"Play {label}",
                              command=lambda hv=hz: self._play(hv))
            play.grid(row=row, column=4, padx=6, pady=3)
            tip(play, "cue.volume.play")

        ctl = ttk.Frame(frame)
        ctl.grid(row=len(self._cues) + 1, column=0, columnspan=5, sticky="we",
                 padx=6, pady=(8, 6))
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

    # ----- playback -----
    def _pct(self, hz: int) -> int:
        return cue_volume.clamp_gain_pct(self._pct_vars[hz].get())

    def _stop(self) -> None:
        if self._after_id is not None:
            try:
                self._frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self._send("CUE_OFF")

    def _play(self, hz: int) -> None:
        # The firmware no-ops CUE_ON while a tone is already playing, so stop any
        # current/pending tone first, then start the new one at the live percent.
        self._stop()
        self._send(f"CUE_ON,{_TEST_KIND},{hz},{self._pct(hz)}")
        self._after_id = self._frame.after(_TEST_BURST_MS, self._stop)

    # ----- save -----
    def _save(self) -> None:
        gains = {hz: self._pct(hz) for hz in self._pct_vars}
        try:
            cue_volume.save_gains(self._path, gains)
            cue_volume.reload_calibration(self._path)
        except OSError as e:
            self._status.set(f"Save failed: {e}")
            return
        summary = ", ".join(f"{lbl} {self._pct(hz)}%" for lbl, hz in self._cues)
        self._status.set(f"Saved ({summary}).")
