"""`slb_glossary` Exceptions"""

import pathlib


class SLBGlossaryError(Exception):
    """Base exception for all errors in `slb-glossary`"""


class NetworkError(ConnectionError, SLBGlossaryError):
    """Raised when a page or resource could not be reached over the network."""


class BrowserError(SLBGlossaryError):
    """Raised when the browser automation layer fails outside of a network issue."""


class SessionNotInitializedError(BrowserError):
    """
    Raised when a search function is called on a `Session` that hasn't
    loaded its topics/size yet.

    Call `Session.initialize()` first, or open the session with
    `open_session(..., initialize=True)` (the default) so it's ready to
    use as soon as it's returned.
    """


class ParsingError(SLBGlossaryError):
    """Raised when a glossary page did not contain the markup a parser expected."""


class ConfigError(SLBGlossaryError):
    """Raised when a `slb_glossary.config.Config` file or key is invalid."""


class DatabaseError(SLBGlossaryError):
    """Raised when `slb_glossary.local` fails to open, query, or write the local database."""


class EmbeddingError(SLBGlossaryError):
    """Raised when `slb_glossary.local` can not compute a text embedding for semantic search."""


class QueryError(SLBGlossaryError):
    """Raised when `slb_glossary.query` can not satisfy a lookup with the source(s) it was given."""


class LoggingError(SLBGlossaryError):
    """Raised when a `slb_glossary.logging` sink (e.g. `--log-to`/`--log-sink`) could not be set up."""


class UnsupportedFormatError(ValueError, SLBGlossaryError):
    """Raised when `save` is asked to write a format with no registered writer."""


class WriterError(OSError, SLBGlossaryError):
    """Raised when a registered writer fails while writing `records`."""

    def __init__(self, message: str, *, destination: pathlib.Path, format: str) -> None:
        super().__init__(message)
        self.destination = destination
        self.format = format


class EnvironmentVariableError(ValueError):
    """Raised when an environment variable is set but can not be cast/validated to its expected type."""


class SessionPoolError(BrowserError, RuntimeError):
    """
    Raised when a `slb_glossary.live.SessionPool` is misused (e.g. a session it never
    handed out is released to it).

    Also a `RuntimeError`, so code written against the pool's earlier, plain
    `RuntimeError`s keeps working.
    """


class SessionPoolClosedError(SessionPoolError):
    """Raised when a `slb_glossary.live.SessionPool` that has been closed is asked for a session."""


class ResourceError(SLBGlossaryError):
    """
    Raised when a `slb_glossary.live.Runtime` can not hand out a resource (database or
    live session) it was asked for.
    """


class RuntimeClosedError(ResourceError):
    """Raised when a `slb_glossary.live.Runtime` that has been closed is asked for a resource."""


class ResourceDisabledError(ResourceError):
    """
    Raised when a `slb_glossary.live.Runtime` is asked for a resource (the local
    database or live sessions) it was configured not to provide.
    """


class UnknownLanguageError(ResourceError, ValueError):
    """Raised when a `slb_glossary.live.Runtime` is asked for a glossary language that doesn't exist."""


class PagePoolTimeoutError(BrowserError, TimeoutError):
    """
    Raised when a `slb_glossary.live.Pages` pool has no free page within its
    `acquire_timeout`.

    That usually means every page is held by something that is itself waiting for
    another page (too much `concurrency` for `max_pages`), or a page was opened and
    never closed.
    """
