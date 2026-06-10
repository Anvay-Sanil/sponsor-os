"""Repo-root conftest: makes `core` and `app` importable in pytest."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (str(ROOT), str(ROOT / "app")):
    if path not in sys.path:
        sys.path.insert(0, path)
