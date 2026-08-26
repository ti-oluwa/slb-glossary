"""
Live search API. Crawls the live SLB glossary site directly, so results
are always current, at the cost of a network round trip per call.

**Disclaimer**: the data returned here is still SLB's - see the package
docstring for the full notice.

Use this responsibly: rate-limit your own calls, avoid tight loops or
bulk scraping, and prefer `slb_glossary.local` (synced once, then queried
offline) wherever repeat lookups are possible, so you're not hitting the
live site on every request. See SLB's terms of use
<https://www.slb.com/en/terms-of-service> for what's actually permitted.
"""

from .api import (
    ensure_initialized,
    get_results_from_url,
    get_results_from_urls,
    get_terms_on,
    get_terms_urls,
    search,
)
from .browser import (
    browser_session,
    close_session,
    open_session,
    open_session_from_config,
    session,
    session_from_config,
)
from .relevance import score_content_overlap, score_name_match, score_result
from .topics import refresh_topics
from .types import BrowserType, PageHandle, Pages, ResourceType, Session

__all__ = [
    "BrowserType",
    "PageHandle",
    "Pages",
    "ResourceType",
    "Session",
    "browser_session",
    "close_session",
    "ensure_initialized",
    "get_results_from_url",
    "get_results_from_urls",
    "get_terms_on",
    "get_terms_urls",
    "open_session",
    "open_session_from_config",
    "refresh_topics",
    "score_content_overlap",
    "score_name_match",
    "score_result",
    "search",
    "session",
    "session_from_config",
]
