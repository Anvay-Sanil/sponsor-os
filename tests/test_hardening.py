"""Phase 6 hardening: the no-unsafe-html rule and per-page friendly rendering."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
APP_FILES = sorted((ROOT / "app").rglob("*.py"))
PAGE_FILES = sorted((ROOT / "app" / "pages").glob("*.py"))


# --- Catch 1: scraped/LLM text renders in this app, so raw HTML is banned ----
@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: p.name)
def test_unsafe_allow_html_is_banned(path: Path) -> None:
    """Evidence snippets and LLM output flow into the UI. Streamlit escapes
    by default; unsafe_allow_html would reopen the injection surface that the
    refresh-token cookie decision depends on staying closed. Grep-able rule."""
    assert "unsafe_allow_html" not in path.read_text(encoding="utf-8"), (
        f"{path.name} uses unsafe_allow_html — banned on scraped/LLM render "
        "paths (see README contributing rules)."
    )


# --- every page renders its friendly logged-out state without raising ---------
@pytest.mark.parametrize("page", PAGE_FILES, ids=lambda p: p.name)
def test_page_renders_friendly_when_logged_out(page: Path) -> None:
    app = AppTest.from_file(str(page), default_timeout=30)
    app.run()
    assert not app.exception, f"{page.name} raised instead of stopping politely"
    # require_role must have shown its friendly notice (info or warning).
    assert app.info or app.warning, f"{page.name} showed no guidance to the logged-out user"
