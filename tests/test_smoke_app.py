"""Smoke test: Home must render the login screen without secrets and never traceback."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

HOME = str(Path(__file__).resolve().parent.parent / "app" / "Home.py")


def test_home_renders_login_without_secrets() -> None:
    app = AppTest.from_file(HOME, default_timeout=30)
    app.run()
    assert not app.exception, f"Home.py raised: {app.exception}"
    # Logged-out state shows the title and the two auth tabs.
    assert any("Sponsor OS" in str(title.value) for title in app.title)
