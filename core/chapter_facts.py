"""Real numbers about our chapter and fest, used inside every generated deck.

HONESTY RULE: these go into documents sponsors read and may verify. Use real
gate counts and real follower numbers — never inflate.

While ANY value still contains "[UPDATE ME", the Deck Generator refuses to
generate decks for real leads (demo/test decks still work). A placeholder in
this file is fine; a placeholder in a sponsor's inbox is a reputation incident.
"""
from __future__ import annotations

PLACEHOLDER_MARK = "[UPDATE ME"

CHAPTER_FACTS: dict[str, str] = {
    "chapter_name": "ACM SIGAI Student Chapter, Manipal University Jaipur",
    "fest_name": "[UPDATE ME — official fest name]",
    "fest_dates": "[UPDATE ME — e.g. 14–16 February 2027]",
    "expected_footfall": "[UPDATE ME — realistic number from past gate counts]",
    "instagram_followers": "[UPDATE ME — current follower count]",
    "past_highlights": "[UPDATE ME — e.g. 12 events and 2 hackathons since 2023]",
    "contact_email": "[UPDATE ME — official chapter email]",
}


def missing_facts() -> list[str]:
    """Names of facts still holding placeholders. Empty list = safe for real leads."""
    return [key for key, value in CHAPTER_FACTS.items() if PLACEHOLDER_MARK in value]
