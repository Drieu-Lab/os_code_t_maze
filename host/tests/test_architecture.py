"""test_architecture.py - enforce the tmaze package layer boundaries (ADR-0083).

Static checks over the tmaze/core sources (parsed, never imported, so missing
optional deps don't matter): a pure core must not import a shell-only library
(Tk / OpenCV / numpy / serial / ...) nor an outer layer (io / vision / ui).
This is the executable form of the placement rule - it fails at the author's
desk when a new file lands in the wrong home. Run via the normal test suite.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

import tmaze.core

CORE_DIR = pathlib.Path(tmaze.core.__file__).resolve().parent

# Shell-only libraries a pure core must never import.
FORBIDDEN_ROOTS = {
    "tkinter", "cv2", "serial", "numpy", "PIL", "pypylon", "av", "matplotlib",
}
# Outer layers a core must not depend on (no "upward" imports).
FORBIDDEN_TMAZE = {"tmaze.io", "tmaze.vision", "tmaze.ui"}


def _imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Only absolute imports carry a module path we can classify.
            if node.level == 0 and node.module:
                mods.add(node.module)
    return mods


class TestCorePurity(unittest.TestCase):
    def test_core_has_no_forbidden_imports(self) -> None:
        offenders: list[str] = []
        for path in sorted(CORE_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = path.relative_to(CORE_DIR)
            for mod in _imported_modules(tree):
                root = mod.split(".")[0]
                if root in FORBIDDEN_ROOTS:
                    offenders.append(f"{rel}: imports shell library '{mod}'")
                if any(mod == f or mod.startswith(f + ".") for f in FORBIDDEN_TMAZE):
                    offenders.append(f"{rel}: imports outer layer '{mod}'")
        self.assertEqual(
            offenders, [], "core purity violated (see ADR-0083):\n" + "\n".join(offenders)
        )


if __name__ == "__main__":
    unittest.main()
