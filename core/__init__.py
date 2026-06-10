"""Sponsor OS core package: database, auth, and (in later phases) llm/scoring/pricing/pitch."""

from core.auth import can_write, has_access, require_role
from core.db import get_client, get_secret

__all__ = [
    "can_write",
    "get_client",
    "get_secret",
    "has_access",
    "require_role",
]
