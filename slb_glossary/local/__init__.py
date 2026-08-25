"""
Local search database. A SQLite (FTS5 + `sqlite-vec`) cache of glossary
terms, so repeat lookups don't have to keep re-visiting the live site.

**Disclaimer**: the data stored here is still SLB's - see the
the package docstring for the full notice. Enabling this
module means keeping a local copy of glossary content on your own
machine; you are solely responsible for that copy's lifecycle (how long
you keep it, how often you refresh it, and deleting it when you're done)
in compliance with SLB's terms of use <https://www.slb.com/en/terms-of-service>.

Prefer `sync_query`/`sync_topic` over `sync_all` where you can.
Fetching only what you actually look up keeps this package's
footprint on the live site as light as possible.
"""

from slb_glossary.errors import DatabaseError
from slb_glossary.local.api import (
    count,
    fuzzy_match_topics,
    get_random_term,
    get_term,
    get_terms_on,
    get_terms_urls,
    get_topics,
    search,
    upsert_results,
)
from slb_glossary.local.connection import close_db, database, open_db
from slb_glossary.local.hybrid import hybrid_search
from slb_glossary.local.lexical import lexical_search
from slb_glossary.local.load import load_file
from slb_glossary.local.maintenance import flush, reset
from slb_glossary.local.sync import (
    SyncSummary,
    sync_all,
    sync_letter,
    sync_query,
    sync_topic,
    sync_topics,
)
from slb_glossary.local.types import Database, Metadata, SearchMode
from slb_glossary.local.vectors import delete_embeddings, embed_terms, vector_search

__all__ = [
    "Database",
    "Metadata",
    "SearchMode",
    "DatabaseError",
    "open_db",
    "close_db",
    "database",
    "upsert_results",
    "search",
    "lexical_search",
    "get_terms_on",
    "get_term",
    "get_random_term",
    "get_terms_urls",
    "get_topics",
    "fuzzy_match_topics",
    "count",
    "load_file",
    "flush",
    "reset",
    "embed_terms",
    "delete_embeddings",
    "vector_search",
    "hybrid_search",
    "SyncSummary",
    "sync_topics",
    "sync_query",
    "sync_topic",
    "sync_letter",
    "sync_all",
]
