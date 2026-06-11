"""Sponsor OS core package.

Submodules are NOT imported eagerly: `auth` and `db` import Streamlit, but
batch jobs in jobs/ must be able to import the rest headlessly (GitHub
Actions has no Streamlit installed). Python's import system resolves
`from core import auth` to the submodule on demand.
"""

__all__ = [
    "auth",
    "chapter_facts",
    "db",
    "deck_render",
    "llm",
    "outcomes",
    "palette",
    "pitch",
    "pitch_memory",
    "pricing",
    "scoring",
]
