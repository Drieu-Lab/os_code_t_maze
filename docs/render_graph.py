#!/usr/bin/env python3
"""Render the os_code_t_maze architecture graph to an image.

This is a thin wrapper around Graphviz so anyone can regenerate the picture
without remembering the exact ``dot`` invocation. It works two ways:

1. If the Graphviz ``dot`` command line tool is on PATH, it shells out to it.
2. Otherwise, if the ``graphviz`` Python package is installed, it uses that.

If neither is available it prints clear install instructions and exits 1.

Usage:
    python docs/render_graph.py                 # -> docs/architecture.png
    python docs/render_graph.py --format svg    # -> docs/architecture.svg
    python docs/render_graph.py --open          # render then open the image
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOT_FILE = os.path.join(HERE, "architecture.dot")

INSTALL_HINT = """\
Graphviz was not found. Install it one of these ways:

  * Graphviz CLI (recommended, gives the `dot` command):
      - Windows:  winget install graphviz   (or:  choco install graphviz)
      - macOS:    brew install graphviz
      - Linux:    sudo apt install graphviz
    You may need to reopen the terminal so `dot` lands on PATH.

  * Or the Python package (still needs the Graphviz binaries above):
      pip install graphviz
"""


def render_with_cli(dot_exe: str, out_path: str, fmt: str) -> None:
    subprocess.run([dot_exe, f"-T{fmt}", DOT_FILE, "-o", out_path], check=True)


def render_with_package(out_path: str, fmt: str) -> None:
    import graphviz  # type: ignore

    with open(DOT_FILE, "r", encoding="utf-8") as fh:
        source = fh.read()
    # graphviz writes <out_base>.<fmt>; render() returns the final path.
    out_base = os.path.splitext(out_path)[0]
    graphviz.Source(source, filename=out_base, format=fmt).render(
        cleanup=True
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format", "-T", default="png",
        help="output image format (png, svg, pdf, ...). Default: png",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="output path. Default: docs/architecture.<format>",
    )
    parser.add_argument(
        "--open", action="store_true",
        help="open the rendered image when done",
    )
    args = parser.parse_args()

    if not os.path.exists(DOT_FILE):
        print(f"error: missing graph source: {DOT_FILE}", file=sys.stderr)
        return 1

    out_path = args.output or os.path.join(HERE, f"architecture.{args.format}")

    dot_exe = shutil.which("dot")
    try:
        if dot_exe:
            render_with_cli(dot_exe, out_path, args.format)
        else:
            try:
                render_with_package(out_path, args.format)
            except ImportError:
                print(INSTALL_HINT, file=sys.stderr)
                return 1
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"error: failed to render graph: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {out_path}")

    if args.open:
        try:
            if sys.platform.startswith("win"):
                os.startfile(out_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", out_path], check=False)
            else:
                subprocess.run(["xdg-open", out_path], check=False)
        except OSError as exc:
            print(f"note: could not open image automatically: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
