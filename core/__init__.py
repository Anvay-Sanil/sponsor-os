"""Sponsor OS core package.

Submodules are NOT imported eagerly: `auth` and `db` import Streamlit, but
batch jobs in jobs/ must be able to import `core.llm` and `core.scoring`
headlessly (GitHub Actions has no Streamlit installed). Python's import
system resolves `from core import auth` to the submodule on demand.
"""

__all__ = ["auth", "db", "llm", "scoring"]
