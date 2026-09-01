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
    """Raised when `slb_glossary.local` can't compute a text embedding for semantic search."""


class QueryError(SLBGlossaryError):
    """Raised when `slb_glossary.query` can't satisfy a lookup with the source(s) it was given."""


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
    """Raised when an environment variable is set but can't be cast/validated to its expected type."""
