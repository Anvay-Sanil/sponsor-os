"""Tests for the pure role-permission matrix in core.auth."""
from __future__ import annotations

import pytest

from core.auth import PAGE_ACCESS, ROLES, WRITE_ACCESS, can_write, has_access

ALL_PAGES = tuple(PAGE_ACCESS)

EXPECTED_PAGES: dict[str, set[str]] = {
    "admin": {"home", "lead_board", "tier_simulator", "deck_generator", "outcomes", "admin"},
    "sponsorship": {"home", "lead_board", "tier_simulator", "deck_generator", "outcomes"},
    "analyst": {"home", "lead_board", "tier_simulator"},
    "viewer": {"home"},
}


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("page", ALL_PAGES)
def test_page_access_matrix(role: str, page: str) -> None:
    assert has_access(role, page) is (page in EXPECTED_PAGES[role])


def test_unknown_role_and_page_denied() -> None:
    assert not has_access(None, "home")
    assert not has_access("hacker", "lead_board")
    assert not has_access("admin", "no_such_page")


def test_every_role_has_a_write_policy_defined() -> None:
    assert set(WRITE_ACCESS) == set(ROLES)


@pytest.mark.parametrize("table", ["leads", "decks", "outcomes"])
def test_sponsorship_writes_pipeline_tables(table: str) -> None:
    assert can_write("sponsorship", table)


@pytest.mark.parametrize("role", ["analyst", "viewer"])
@pytest.mark.parametrize("table", ["leads", "decks", "outcomes", "brands", "invite_codes"])
def test_readonly_roles_write_nothing(role: str, table: str) -> None:
    assert not can_write(role, table)


def test_sponsorship_cannot_touch_admin_tables() -> None:
    for table in ("invite_codes", "profiles", "brands", "tiers"):
        assert not can_write("sponsorship", table)


def test_admin_writes_everything_listed() -> None:
    for table in WRITE_ACCESS["admin"]:
        assert can_write("admin", table)


def test_none_role_denied_everywhere() -> None:
    assert not can_write(None, "leads")
    assert not has_access(None, "lead_board")
