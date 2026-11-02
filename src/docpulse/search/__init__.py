"""Day 4: semantic search over the Day 3 embedding table.

The API never writes vectors. It reads ``paper_embeddings`` (Day 3) joined to
``paper_meta`` (loaded from silver by ``scripts/day4_load_meta.py``) and turns a
query string into a vector with the same pinned model Day 3 used.
"""

from .api import create_app
from .config import SearchSettings
from .query import QueryEncoder, normalise_query

__all__ = ["create_app", "SearchSettings", "QueryEncoder", "normalise_query"]
