"""
Exception hierarchy (base classes, `isinstance` relationships).
"""

import pathlib

import pytest

from slb_glossary import errors

pytestmark = pytest.mark.unit

ALL_ERROR_CLASSES = [
    getattr(errors, name)
    for name in dir(errors)
    if isinstance(getattr(errors, name), type)
    and issubclass(getattr(errors, name), errors.SLBGlossaryError)
]


class TestExceptionHierarchy:
    @pytest.mark.parametrize("error_class", ALL_ERROR_CLASSES)
    def test_every_error_is_an_slb_glossary_error(self, error_class: type[Exception]):
        """Every exported exception class is a subclass of `SLBGlossaryError`."""
        assert issubclass(error_class, errors.SLBGlossaryError)

    def test_session_not_initialized_error_is_a_browser_error(self):
        """`SessionNotInitializedError` is a `BrowserError`."""
        assert issubclass(errors.SessionNotInitializedError, errors.BrowserError)

    def test_network_error_is_a_connection_error(self):
        """`NetworkError` is a builtin `ConnectionError`."""
        assert issubclass(errors.NetworkError, ConnectionError)

    def test_unsupported_format_error_is_a_value_error(self):
        """`UnsupportedFormatError` is a builtin `ValueError`."""
        assert issubclass(errors.UnsupportedFormatError, ValueError)

    def test_writer_error_is_an_os_error(self):
        """`WriterError` is a builtin `OSError`."""
        assert issubclass(errors.WriterError, OSError)

    def test_writer_error_carries_destination_and_format(self):
        """`WriterError` stores the `destination`/`format` it was constructed with."""
        error = errors.WriterError(
            "could not write", destination=pathlib.Path("out.csv"), format="csv"
        )
        assert error.destination == pathlib.Path("out.csv")
        assert error.format == "csv"
