"""
reward_dispatcher.py - one shared seam for dispensing reward on the host.

Both host task controllers - the Rules engine (rules_view.py) and the host-owned
Phase 3 (phase3_controller.py) - deliver reward at the central well by sending a
bare ``R<port>1,<ms>`` command to the firmware. They used to do so through two
unrelated paths: the Rules engine enforced a global minimum-interval rate
backstop (and paired each fire with a log record) inside its pure core, while
Phase 3 fired directly with no backstop and its own logging. So the anti-spam
guarantee and the reward record differed between the two task modes for no
principled reason. See docs/decisions/0054 (and FW-0019).

This module is that single seam. ``RewardDispatcher.fire`` is the ONE place a
reward is gated (a global rate backstop), recorded (reason + port + pulse +
calibrated volume for the active reward liquid; ADR-0019, ADR-0091), and sent
(``R<port>1,<ms>`` - unchanged on-wire), so a fire is handled identically
whichever task drove it. It owns no log file:
``fire`` RETURNS an immutable ``RewardRecord`` and each controller persists it
into its own data file (the Rules track-log vs the Phase 3 session file), so the
record shape is shared while each mode keeps its own file.

One shared instance is injected into both controllers (task_view.py), so the
backstop and volume accounting are truly rig-global and survive a task-tab
switch. Because the two controllers run their own ADR-0043 actuation threads (and
the Rules tab may also fire from the Tk poll in its no-pipeline fallback),
``fire`` takes a lock; fires are rare (a few per second at most), so the lock is
off the hot path in practice.

Pure-ish core: stdlib only, no Tk/serial/OpenCV imports. The serial ``send`` and
the monotonic ``clock`` are injected, so this is headless-testable (see
test_reward_dispatcher.py).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from tmaze.core.reward.reward_calibration import normalize_liquid
from tmaze.core.reward.reward_volume import DEFAULT_REWARD_LIQUID, reward_volume_ul

# Default global minimum interval (ms) between two accepted reward fires,
# regardless of which task drove them - a last-resort guard against runaway
# firing. Matches the historical rules-engine default
# (EngineDefaults.global_min_interval_ms) so behavior is unchanged; the Rules tab
# pushes rules.json's configured value into the shared instance on engine build.
# See docs/decisions/0054.
DEFAULT_GLOBAL_MIN_INTERVAL_MS = 500


@dataclass(frozen=True)
class RewardRecord:
    """Immutable outcome of one ``RewardDispatcher.fire`` call.

    ``fired`` is True when the pulse was actually sent; when False the fire did
    NOT reach the wire and ``suppressed_reason`` says why - either
    ``"global_backstop"`` (dropped by the rate limiter) or ``"send_failed"``
    (the serial send raised, so no reward was delivered; ADR-0122). ``volume_ul``
    is the calibrated dispensed volume for a fired pulse (0.0 when not fired).
    ``reason`` / ``rule_id`` describe what asked for the reward (e.g.
    "prime"/"topup" from a rule, "phase3_reward" from the Phase 3 scorer), so
    both modes log the same shape.
    """
    fired: bool
    reason: str
    rule_id: str
    port: int
    pulse_ms: int
    volume_ul: float = 0.0
    suppressed_reason: Optional[str] = None


class RewardDispatcher:
    """Gate + record + send for every host reward fire (see module docstring)."""

    def __init__(self, send: Optional[Callable[[str], None]],
                 *, min_interval_ms: int = DEFAULT_GLOBAL_MIN_INTERVAL_MS,
                 active_liquid: str = DEFAULT_REWARD_LIQUID,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._send = send
        self._min_interval_ms = int(min_interval_ms)
        self._active_liquid = normalize_liquid(active_liquid)
        self._clock = clock
        self._last_fire_now: Optional[float] = None
        self._lock = threading.Lock()

    def set_min_interval_ms(self, ms: int) -> None:
        """Reconfigure the global backstop interval (e.g. from rules.json's
        ``global_min_interval_ms`` when the Rules engine (re)builds)."""
        with self._lock:
            self._min_interval_ms = int(ms)

    @property
    def min_interval_ms(self) -> int:
        return self._min_interval_ms

    def set_active_liquid(self, liquid: str) -> None:
        """Set the reward liquid currently in the line, so each fire is scored
        against the matching calibration curve (ADR-0091). Driven by the Session
        panel's "Reward" field; the value is normalized to a canonical token."""
        with self._lock:
            self._active_liquid = normalize_liquid(liquid)

    @property
    def active_liquid(self) -> str:
        return self._active_liquid

    def fire(self, port: int, ms: int, *, reason: str,
             rule_id: str) -> RewardRecord:
        """Dispense one reward pulse, subject to the global rate backstop.

        Returns a :class:`RewardRecord`. If the last accepted fire was less than
        ``min_interval_ms`` ago, the fire is suppressed (nothing is sent) and the
        record has ``fired=False`` + ``suppressed_reason="global_backstop"``.
        Otherwise ``R<port>1,<ms>`` is sent; on a successful send the timer
        advances and the record carries the calibrated ``volume_ul`` (ADR-0019).
        If the send itself raises (serial hiccup), NO reward was delivered, so
        the record is ``fired=False`` + ``suppressed_reason="send_failed"`` and
        the backstop timer is NOT advanced (so accounting stays truthful and an
        immediate retry isn't blocked; ADR-0122).
        """
        port = int(port)
        ms = int(ms)
        with self._lock:
            now = self._clock()
            if self._last_fire_now is not None and \
                    (now - self._last_fire_now) * 1000.0 < self._min_interval_ms:
                return RewardRecord(
                    fired=False, reason=reason, rule_id=rule_id, port=port,
                    pulse_ms=ms, volume_ul=0.0,
                    suppressed_reason="global_backstop")
            # Send BEFORE advancing the timer so a failed send neither counts as
            # a delivered reward nor blocks the next attempt (ADR-0122).
            if not self._do_send(f"R{port}1,{ms}"):
                return RewardRecord(
                    fired=False, reason=reason, rule_id=rule_id, port=port,
                    pulse_ms=ms, volume_ul=0.0,
                    suppressed_reason="send_failed")
            self._last_fire_now = now
            return RewardRecord(
                fired=True, reason=reason, rule_id=rule_id, port=port,
                pulse_ms=ms,
                volume_ul=reward_volume_ul(ms, port=port,
                                           liquid=self._active_liquid),
                suppressed_reason=None)

    def _do_send(self, cmd: str) -> bool:
        """Hand ``cmd`` to the injected serial send. Returns True when the pulse
        reached the wire (or there is no wire configured - ``send is None`` is a
        deliberate headless no-op that counts as delivered), and False when the
        send raised. A serial hiccup never propagates (it must not break the
        actuation thread), but it IS reported so ``fire`` can record the truth
        (ADR-0122)."""
        if self._send is None:
            return True
        try:
            self._send(cmd)
        except Exception:
            return False
        return True
