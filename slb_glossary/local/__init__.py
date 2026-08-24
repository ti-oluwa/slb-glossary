"""
Local search database. A SQLite (FTS5) cache of glossary terms, plus an
optional custom embedding vector store, so repeat lookups don't
have to keep re-visiting the live site.

Open one with `open_db`/`database`, fill it from a live `Session`
(with `sync_topics`/`sync_query`/`sync_topic`/`sync_all`) or from your own
CSV/JSON/XLSX file (`slb_glossary.local.loaders.load_file`, or
`slb-glossary local import` from the CLI), then query
it with `search`/`get_terms_on`/`get_term`/`get_terms_urls`/`iter_topics`
which all have the same shapes `slb_glossary.live`'s live functions return, so code
written against one works against the other. `flush`/`reset` clear it out
again when you're done with it.

`search` ranks results *best-match-first* using SQLite FTS5 plus a second,
in-process scoring pass (so a real match against a term's name always
beats an incidental one buried in its definition). Use `scored_search`
instead if you need each result's `0.0`-`1.0` score, e.g. to decide
whether the local database's results are good enough to serve alone.

Topic filters on `search`/`get_terms_on`/`random_term`/`get_terms_urls`
match locally stored topic names exactly (case-insensitively) by default;
pass `fuzzy=True` to tolerate minor misspellings/partial names instead.

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
    scored_search,
    search,
    upsert_results,
)
from slb_glossary.local.connection import close_db, database, open_db
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
from slb_glossary.local.types import Database, Metadata
from slb_glossary.local.vectors import delete_vectors, upsert_vector, vector_search

__all__ = [
    "Database",
    "Metadata",
    "DatabaseError",
    "open_db",
    "close_db",
    "database",
    "upsert_results",
    "search",
    "scored_search",
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
    "upsert_vector",
    "delete_vectors",
    "vector_search",
    "SyncSummary",
    "sync_topics",
    "sync_query",
    "sync_topic",
    "sync_letter",
    "sync_all",
]
