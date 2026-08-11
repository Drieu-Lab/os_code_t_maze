"""
reward_calibrate.py - guided bench tool to measure a reward-volume curve.

Fires a sweep of reward pulse widths at one port through the running broker and,
for each width, prompts you for the volume you aspirated from the well with a
graduated syringe. It divides by the number of pulses to get microliters/pulse,
averages that across a few replicate readings, and upserts the points into
``host/reward_calibration.csv`` (keyed by port + liquid + pulse_ms), which the
GUI's reward accounting then reads.

Two counts, don't confuse them:
- ``--repeats`` = solenoid ACTIVATIONS fired per reading (a single ~60 uL pulse
  is hard to read, so N pulses accumulate a measurable volume; volume/N = per
  pulse);
- ``--replicates`` = separate READINGS per duration (fire N, aspirate, read;
  repeated a few times and averaged, to see the spread).

Why volume (not mass): the reward liquid may not be water, and mass->volume
depends on density; a direct syringe read is density-independent. One run measures
one liquid; re-run with ``--liquid`` for each. See docs/decisions/0091.

Prerequisite: the rig broker must be running (start_rig.bat / rig_broker), since
this talks to it over TCP the same way the GUIs do. The central spout is the
default. Run from anywhere (paths resolve via tmaze.paths):

    python -m tmaze.tools.reward_calibrate                 # 10% sucrose, center
    python -m tmaze.tools.reward_calibrate --liquid water
    python -m tmaze.tools.reward_calibrate --durations 100,200,300 --repeats 30

This is a thin operator shell: the math, liquid names, and CSV IO live in the
pure core (tmaze.core.reward.reward_calibration); only the socket + prompts are
here.
"""

from __future__ import annotations

import argparse
import socket
import statistics
import sys
import time
from typing import List, Optional

from tmaze.core.reward.reward_calibration import (
    KNOWN_LIQUIDS,
    CalibrationPoint,
    default_calibration_path,
    liquid_label,
    normalize_liquid,
    normalize_port,
    read_points,
    today_str,
    ul_per_pulse,
    upsert_points,
    write_points,
)
from tmaze.core.rig_config import SOLENOID_PORTS, load_rig_config

DEFAULT_DURATIONS = "50,100,150,200,250,300,400,500"
DEFAULT_REPEATS = 10       # solenoid activations per reading
DEFAULT_REPLICATES = 3     # readings per duration (averaged)
DEFAULT_SETTLE_MS = 500
DEFAULT_REWARD_SOL_INDEX = 1  # R2 == the reward spout; matches rig_config.h


class BrokerSender:
    """Minimal send-only TCP client to the broker (avoids the Tk-bound RigClient).

    We only need to push ``R<port><sol>,<ms>`` lines; inbound lines are ignored.
    """

    def __init__(self, host: str, port: int):
        self._sock = socket.create_connection((host, port), timeout=5.0)

    def send(self, cmd: str) -> None:
        if not cmd.endswith("\n"):
            cmd += "\n"
        self._sock.sendall(cmd.encode("utf-8", errors="ignore"))

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def parse_durations(text: str) -> List[int]:
    out: List[int] = []
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        out.append(int(float(tok)))
    if not out:
        raise ValueError("no durations parsed")
    return out


def parse_volume_ul(text: str, default_unit: str) -> Optional[float]:
    """Parse '3.1 mL' / '3100 uL' / '3100' (bare -> default_unit) into uL.

    Returns None for blank input (skip this point).
    """
    s = text.strip().lower().replace("\u00b5", "u")  # micro sign -> 'u'
    if not s:
        return None
    unit = default_unit.lower()
    for suffix, u in (("ml", "ml"), ("ul", "ul"), ("cc", "ml"), ("l", "ml")):
        if s.endswith(suffix):
            unit = u
            s = s[: -len(suffix)].strip()
            break
    value = float(s)
    return value * 1000.0 if unit == "ml" else value


def _prompt(msg: str) -> str:
    try:
        return input(msg)
    except EOFError:
        return "q"


def reward_command(port_index: int, sol_index: int, ms: int) -> str:
    """The on-wire manual solenoid command, identical to the GUI/dispatcher:
    ``R<port><sol>,<ms>`` (e.g. center reward spout = ``R21,300``)."""
    return f"R{port_index}{sol_index},{ms}"


def fire_pulses(sender: Optional[BrokerSender], cmd_template: str, ms: int,
                n: int, settle_ms: int) -> None:
    """Fire ``n`` pulses of ``ms`` at ``settle_ms`` spacing (dry-run: just print)."""
    interval = (ms + settle_ms) / 1000.0
    for i in range(n):
        cmd = cmd_template.format(ms=ms)
        if sender is None:
            print(f"    [dry-run] would send {cmd}")
        else:
            sender.send(cmd)
        # Progress on a single line so a long block isn't a wall of text.
        print(f"\r    pulse {i + 1}/{n} ({cmd})", end="", flush=True)
        if i < n - 1:
            time.sleep(interval)
    print()


def run(args: argparse.Namespace) -> int:
    liquid = normalize_liquid(args.liquid)
    port = normalize_port(args.port)
    try:
        port_index = SOLENOID_PORTS.index(port)
    except ValueError:
        print(f"error: unknown port {args.port!r} (expected one of "
              f"{', '.join(SOLENOID_PORTS)})", file=sys.stderr)
        return 2

    cfg = load_rig_config()
    sol_index = (cfg.reward_sol_index
                 if cfg.ok and cfg.reward_sol_index is not None
                 else DEFAULT_REWARD_SOL_INDEX)

    durations = parse_durations(args.durations)
    out_path = args.out or default_calibration_path()
    cmd_template = reward_command(port_index, sol_index, 0).replace(",0", ",{ms}")

    print("=" * 64)
    print("Reward volume calibration")
    print(f"  liquid   : {liquid_label(liquid)}  [{liquid}]")
    print(f"  port     : {port}  (index {port_index}, reward sol {sol_index})")
    print(f"  durations : {durations} ms")
    print(f"  pulses    : {args.repeats} activations per reading")
    print(f"  replicates: {args.replicates} reading(s) per duration (averaged)")
    print(f"  command   : {cmd_template.format(ms='<ms>')}")
    print(f"  output    : {out_path}")
    if args.dry_run:
        print("  DRY RUN   : no pulses will actually be sent")
    print("=" * 64)
    print("Method: for each reading keep the well EMPTY, fire N pulses, then\n"
          "aspirate ALL the dispensed liquid with the graduated syringe and enter\n"
          f"the total volume (default unit: {args.unit}). Each duration is read\n"
          "several times and averaged. Enter blank to skip a reading, 's' to skip\n"
          "the rest of a duration, or 'q' at any prompt to stop early.")

    sender: Optional[BrokerSender] = None
    if not args.dry_run:
        try:
            sender = BrokerSender(args.broker_host, args.broker_port)
        except OSError as e:
            print(f"\nerror: could not connect to broker at "
                  f"{args.broker_host}:{args.broker_port} ({e}).\n"
                  "Is the rig running (start_rig.bat)? Use --dry-run to rehearse.",
                  file=sys.stderr)
            return 3

    collected: List[CalibrationPoint] = []
    quit_all = False
    try:
        for ms in durations:
            if quit_all:
                break
            reps_pp: List[float] = []      # per-pulse uL for each reading
            reps_meas: List[float] = []    # measured total uL for each reading
            for r in range(1, args.replicates + 1):
                ans = _prompt(
                    f"\n[{ms} ms] reading {r}/{args.replicates}: well empty? Enter "
                    f"to fire {args.repeats} pulses (s=skip duration, q=quit): "
                ).strip().lower()
                if ans == "q":
                    quit_all = True
                    break
                if ans == "s":
                    break
                fire_pulses(sender, cmd_template, ms, args.repeats, args.settle_ms)
                vol_ans = _prompt(
                    f"[{ms} ms] reading {r}/{args.replicates} aspirated volume "
                    f"(e.g. 3.1 mL / 3100 uL) [{args.unit}]: ")
                if vol_ans.strip().lower() == "q":
                    quit_all = True
                    break
                try:
                    measured_ul = parse_volume_ul(vol_ans, args.unit)
                except ValueError:
                    print("    could not parse volume; skipping this reading")
                    continue
                if measured_ul is None:
                    print("    skipped (no volume entered)")
                    continue
                per_pulse = ul_per_pulse(measured_ul, args.repeats)
                print(f"    -> {per_pulse:.2f} uL/pulse "
                      f"({measured_ul:.0f} uL / {args.repeats})")
                reps_pp.append(per_pulse)
                reps_meas.append(measured_ul)

            if not reps_pp:
                continue
            mean_pp = statistics.fmean(reps_pp)
            mean_meas = statistics.fmean(reps_meas)
            spread = (f", sd={statistics.stdev(reps_pp):.2f}"
                      if len(reps_pp) > 1 else "")
            print(f"  [{ms} ms] mean {mean_pp:.2f} uL/pulse over "
                  f"{len(reps_pp)} reading(s){spread}")
            rep_note = (f"mean of {len(reps_pp)} readings x {args.repeats} pulses; "
                        f"per-pulse uL={[round(v, 2) for v in reps_pp]}{spread}")
            notes = f"{args.notes}; {rep_note}" if args.notes else rep_note
            collected.append(CalibrationPoint(
                port=port, liquid=liquid, pulse_ms=ms, volume_ul=mean_pp,
                n_pulses=args.repeats, measured_ul=mean_meas,
                date=today_str(), operator=args.operator, notes=notes))
    finally:
        if sender is not None:
            sender.close()

    if not collected:
        print("\nNo points measured; nothing written.")
        return 0

    print("\nMeasured points:")
    print(f"  {'pulse_ms':>8}  {'uL/pulse':>9}  {'measured_uL':>11}")
    for pt in sorted(collected, key=lambda p: p.pulse_ms):
        print(f"  {pt.pulse_ms:>8}  {pt.volume_ul:>9.2f}  {pt.measured_ul:>11.0f}")

    if args.dry_run:
        print(f"\nDRY RUN: would upsert {len(collected)} point(s) into {out_path}.")
        return 0

    confirm = _prompt(f"\nWrite {len(collected)} point(s) into {out_path}? "
                      "(existing same-key rows are replaced) [y/N]: ")
    if confirm.strip().lower() not in ("y", "yes"):
        print("Not written.")
        return 0

    existing = read_points(out_path)
    merged = upsert_points(existing, collected)
    write_points(out_path, merged)
    print(f"Wrote {out_path} ({len(merged)} total rows).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Measure a reward pulse-duration -> volume curve (per liquid).")
    p.add_argument("--liquid", default="sucrose_10pct",
                   help="reward liquid being measured (default %(default)s; known: "
                        + ", ".join(KNOWN_LIQUIDS) + "; free text is normalized)")
    p.add_argument("--port", default="center",
                   help="reward port: center/left/right or index 0/1/2 "
                        "(default %(default)s)")
    p.add_argument("--durations", default=DEFAULT_DURATIONS,
                   help="comma-separated pulse widths in ms (default %(default)s)")
    p.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                   help="solenoid activations per reading, to accumulate a "
                        "measurable volume (default %(default)s)")
    p.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES,
                   help="replicate readings per duration, averaged "
                        "(default %(default)s)")
    p.add_argument("--settle-ms", type=int, default=DEFAULT_SETTLE_MS,
                   help="gap between pulses in ms (default %(default)s)")
    p.add_argument("--unit", choices=("mL", "uL"), default="mL",
                   help="assumed unit when you enter a bare number (default %(default)s)")
    p.add_argument("--broker-host", default="127.0.0.1",
                   help="broker TCP host (default %(default)s)")
    p.add_argument("--broker-port", type=int, default=5555,
                   help="broker TCP port (default %(default)s)")
    p.add_argument("--out", default=None,
                   help="output CSV (default host/reward_calibration.csv)")
    p.add_argument("--operator", default="",
                   help="your name/initials, recorded with each point")
    p.add_argument("--notes", default="",
                   help="free-text note recorded with each point")
    p.add_argument("--dry-run", action="store_true",
                   help="rehearse without connecting/firing or writing")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
