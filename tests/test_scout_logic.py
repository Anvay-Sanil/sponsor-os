"""Tests for the pure logic in jobs/scout_refresh.py.

The single most important test in this file is
test_existing_lead_update_touches_only_the_score: a Scout re-run must NEVER
reset status/owner/priority a junior set by hand. That bug would silently
destroy the committee's trust in the tool.
"""
from __future__ import annotations

import pytest

from jobs.scout_refresh import (
    MAX_SPONSOR_LINKS_PER_SEED,
    brand_on_page,
    existing_lead_update,
    find_sponsor_links,
    is_institution,
    new_lead_row,
    normalize_ws,
    page_to_text,
)


# --- Catch 1: status-preserving lead updates ---------------------------------
def test_existing_lead_update_touches_only_the_score() -> None:
    payload = existing_lead_update(72.5)
    assert set(payload.keys()) == {"evidence_score"}
    assert "status" not in payload
    assert "owner_id" not in payload
    assert "priority" not in payload


def test_new_lead_row_defaults() -> None:
    row = new_lead_row(brand_id=7, score=80.0)
    assert row["status"] == "new"
    assert row["is_demo"] is False
    assert row["priority"] == "high"
    assert row["brand_id"] == 7


# --- Catch 2: anti-hallucination gate ----------------------------------------
def test_brand_on_page_exact_and_case_insensitive() -> None:
    text = "Title sponsors include boAt Lifestyle and RED BULL this year."
    assert brand_on_page("boAt", text)
    assert brand_on_page("red bull", text)


def test_brand_on_page_whitespace_normalized() -> None:
    assert brand_on_page("Red  Bull", "thanks to Red\nBull for the energy")


def test_brand_on_page_rejects_hallucinations() -> None:
    assert not brand_on_page("Nike", "Sponsors: boAt, Red Bull, Unstop.")
    assert not brand_on_page("Coca-Cola", "")


# --- institution filter (observed live: universities extracted as sponsors) -----
@pytest.mark.parametrize(
    "name",
    ["Deakin University", "University of Melbourne", "Fed Uni. Australia",
     "IIT Bombay", "BITS Pilani", "JECRC College", "Indian Institute of Technology",
     "Delhi Public School"],
)
def test_institutions_are_rejected(name: str) -> None:
    assert is_institution(name)


@pytest.mark.parametrize(
    "name",
    ["Coding Ninjas", "L&T EduTech", "Unstop", "EC-Council, USA", "Truechip",
     "boAt", "Red Bull", "TCS", "Kalvium", "PayTM"],
)
def test_real_brands_pass_institution_filter(name: str) -> None:
    assert not is_institution(name)


# --- fetch helpers --------------------------------------------------------------
def test_normalize_ws() -> None:
    assert normalize_ws("  a\n\t b \r\n c  ") == "a b c"


def test_page_to_text_strips_scripts_and_styles() -> None:
    html = "<html><script>evil()</script><style>.x{}</style><body>Sponsors:  boAt</body></html>"
    text = page_to_text(html)
    assert "boAt" in text
    assert "evil" not in text
    assert "  " not in text


def test_find_sponsor_links_same_domain_only_and_capped() -> None:
    html = """
    <a href="/sponsors">Our Sponsors</a>
    <a href="https://fest.example.com/partners-2026">Partners</a>
    <a href="https://other.example.org/sponsors">External sponsor page</a>
    <a href="/schedule">Schedule</a>
    <a href="/sponsors">Our Sponsors (duplicate)</a>
    <a href="/sponsor-deck">Sponsor deck</a>
    <a href="/why-partner">Why partner with us</a>
    """
    links = find_sponsor_links(html, "https://fest.example.com/")
    assert len(links) <= MAX_SPONSOR_LINKS_PER_SEED
    assert "https://fest.example.com/sponsors" in links
    assert all("other.example.org" not in link for link in links)
    assert all("schedule" not in link for link in links)
    assert len(links) == len(set(links))
