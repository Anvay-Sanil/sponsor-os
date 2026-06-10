"""Make the repo root importable (`core` package) on Streamlit Cloud.

Streamlit puts the main script's directory (app/) on sys.path, not the repo
root, so every page imports this module first: `import _bootstrap  # noqa: F401`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
