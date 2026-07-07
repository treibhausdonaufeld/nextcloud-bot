"""Unit tests for the protocol view controller's request-type detection.

The route serves two different templates from the same URL: a bare popup
partial for htmx dialog swaps, and a full standalone page (so a protocol can
be linked to and opened directly, e.g. from a shared chat message).
"""

from unittest.mock import Mock

from app.controllers.protocol_view import _is_htmx_request


def _request_with_header(value):
    request = Mock()
    request.headers = {"hx-request": value} if value is not None else {}
    return request


class TestIsHtmxRequest:
    def test_true_for_lowercase_true(self):
        assert _is_htmx_request(_request_with_header("true")) is True

    def test_true_case_insensitive(self):
        assert _is_htmx_request(_request_with_header("True")) is True

    def test_false_when_header_missing(self):
        assert _is_htmx_request(_request_with_header(None)) is False

    def test_false_for_other_values(self):
        assert _is_htmx_request(_request_with_header("false")) is False
        assert _is_htmx_request(_request_with_header("")) is False
