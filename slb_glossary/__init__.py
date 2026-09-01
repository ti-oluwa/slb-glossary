"""
Search the SLB Energy Glossary (<https://glossary.slb.com/>).

All rights to the data and content on the SLB Energy Glossary website are owned by SLB.
This package is not affiliated with or endorsed by SLB.
Visit <https://www.slb.com/en/terms-of-service> for the terms of use.

**Not for commercial use. This package is intended for instructional and research purposes only.**

This package can optionally cache glossary data locally (see `slb_glossary.local`)
so repeat lookups don't have to re-visit the site. That local copy is still SLB's
data: anyone who enables local storage is solely responsible for keeping its
retention, refresh, and deletion in compliance with SLB's terms of use linked above.

@Author: Daniel T. Afolayan (ti-oluwa)
"""

import logging as py_logging

from . import live, local, query, readers, writers
from . import logging as log
from .config import Config
from .errors import (
    BrowserError,
    ConfigError,
    DatabaseError,
    EmbeddingError,
    EnvironmentVariableError,
    LoggingError,
    NetworkError,
    ParsingError,
    QueryError,
    SessionNotInitializedError,
    SLBGlossaryError,
    UnsupportedFormatError,
    WriterError,
)
from .live.browser import (
    BrowserType,
    ResourceType,
    Session,
    browser_session,
    close_session,
    open_session,
    open_session_from_config,
    session,
    session_from_config,
)
from .live.topics import refresh_topics
from .query import (
    QueryResult,
    Source,
    compare,
    get_random_term,
    get_term,
    get_terms_on,
    get_terms_urls,
    get_topics,
    related_terms,
    search,
)
from .readers import READERS, Reader, read_rows, reader
from .retries import BackoffType, RetryPolicy
from .types import (
    Language,
    RecordLike,
    RelatedTerm,
    SearchMode,
    SearchResult,
)
from .utils import get_topic_match, print_async_records, print_records
from .writers import WRITERS, Writer, records_to_dicts, save, writer

py_logging.basicConfig(
    format="%(levelname)s  %(asctime)s  [%(name)s.%(funcName)s:%(lineno)d]:  %(message)s",
    level=py_logging.INFO,
)

__version__ = "0.1.0"
__all__ = [
    "READERS",
    "WRITERS",
    "BackoffType",
    "BrowserError",
    "BrowserType",
    "Config",
    "ConfigError",
    "DatabaseError",
    "EmbeddingError",
    "EnvironmentVariableError",
    "Language",
    "LoggingError",
    "NetworkError",
    "ParsingError",
    "QueryError",
    "QueryResult",
    "Reader",
    "RecordLike",
    "RelatedTerm",
    "ResourceType",
    "RetryPolicy",
    "SLBGlossaryError",
    "SearchMode",
    "SearchResult",
    "Session",
    "SessionNotInitializedError",
    "Source",
    "UnsupportedFormatError",
    "Writer",
    "WriterError",
    "browser_session",
    "close_session",
    "compare",
    "get_random_term",
    "get_term",
    "get_terms_on",
    "get_terms_urls",
    "get_topic_match",
    "get_topics",
    "live",
    "local",
    "log",
    "open_session",
    "open_session_from_config",
    "print_async_records",
    "print_records",
    "query",
    "read_rows",
    "reader",
    "readers",
    "records_to_dicts",
    "refresh_topics",
    "related_terms",
    "save",
    "search",
    "session",
    "session_from_config",
    "writer",
    "writers",
]
