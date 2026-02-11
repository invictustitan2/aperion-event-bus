"""
Tests for correlation ID management.
"""

from __future__ import annotations

from aperion_event_bus.correlation import (
    CORRELATION_ID_HEADER,
    CorrelationContext,
    add_correlation_to_log_extra,
    clear_correlation_id,
    extract_correlation_id_from_headers,
    generate_correlation_id,
    get_correlation_id,
    get_headers_with_correlation,
    set_correlation_id,
)


class TestCorrelationIDBasic:
    """Test basic correlation ID operations."""

    def test_generate_correlation_id(self):
        """generate_correlation_id returns unique UUIDs."""
        id1 = generate_correlation_id()
        id2 = generate_correlation_id()

        assert id1 != id2
        assert len(id1) == 36  # UUID format
        assert "-" in id1

    def test_set_and_get_correlation_id(self):
        """set_correlation_id and get_correlation_id work together."""
        set_correlation_id("test-123")
        assert get_correlation_id() == "test-123"

        # Cleanup
        clear_correlation_id()
        assert get_correlation_id() is None

    def test_clear_correlation_id(self):
        """clear_correlation_id removes the current correlation ID."""
        set_correlation_id("to-be-cleared")
        assert get_correlation_id() == "to-be-cleared"

        clear_correlation_id()
        assert get_correlation_id() is None


class TestCorrelationContext:
    """Test CorrelationContext context manager."""

    def test_context_sets_correlation_id(self):
        """CorrelationContext sets correlation ID within the block."""
        with CorrelationContext("ctx-abc") as cid:
            assert cid == "ctx-abc"
            assert get_correlation_id() == "ctx-abc"

        # After exiting, should be cleared
        assert get_correlation_id() is None

    def test_context_generates_id_if_none_provided(self):
        """CorrelationContext generates ID if none provided."""
        with CorrelationContext() as cid:
            assert cid is not None
            assert len(cid) == 36  # UUID format
            assert get_correlation_id() == cid

    def test_nested_contexts_restore_previous(self):
        """Nested CorrelationContexts properly save and restore."""
        with CorrelationContext("outer"):
            assert get_correlation_id() == "outer"

            with CorrelationContext("inner"):
                assert get_correlation_id() == "inner"

            # After inner context exits, outer should be restored
            assert get_correlation_id() == "outer"

        # After all contexts exit, should be None
        assert get_correlation_id() is None


class TestCorrelationHeaders:
    """Test HTTP header integration."""

    def test_extract_from_headers_existing(self):
        """Extract correlation ID from existing header."""
        headers = {CORRELATION_ID_HEADER: "existing-id-123"}
        result = extract_correlation_id_from_headers(headers)
        assert result == "existing-id-123"

    def test_extract_from_headers_case_insensitive(self):
        """Header extraction is case-insensitive."""
        headers = {"x-correlation-id": "lower-case-id"}
        result = extract_correlation_id_from_headers(headers)
        assert result == "lower-case-id"

    def test_extract_from_headers_generates_if_missing(self):
        """Generate new ID if header is missing."""
        headers = {}
        result = extract_correlation_id_from_headers(headers)
        assert result is not None
        assert len(result) == 36  # UUID format

    def test_get_headers_with_correlation(self):
        """get_headers_with_correlation returns header dict."""
        set_correlation_id("header-id")
        headers = get_headers_with_correlation()

        assert headers == {CORRELATION_ID_HEADER: "header-id"}

        # Cleanup
        clear_correlation_id()

    def test_get_headers_with_correlation_empty_if_not_set(self):
        """get_headers_with_correlation returns empty dict if no ID set."""
        clear_correlation_id()
        headers = get_headers_with_correlation()
        assert headers == {}


class TestLoggingIntegration:
    """Test logging helper functions."""

    def test_add_correlation_to_log_extra(self):
        """add_correlation_to_log_extra adds correlation ID to extra dict."""
        set_correlation_id("log-id")
        extra = {"existing": "value"}
        result = add_correlation_to_log_extra(extra)

        assert result["existing"] == "value"
        assert result["correlation_id"] == "log-id"

        # Original should not be modified
        assert "correlation_id" not in extra

        # Cleanup
        clear_correlation_id()

    def test_add_correlation_to_log_extra_with_none(self):
        """add_correlation_to_log_extra works with None input."""
        set_correlation_id("log-id-2")
        result = add_correlation_to_log_extra(None)

        assert result["correlation_id"] == "log-id-2"

        # Cleanup
        clear_correlation_id()

    def test_add_correlation_to_log_extra_no_id_set(self):
        """add_correlation_to_log_extra returns original dict if no ID set."""
        clear_correlation_id()
        extra = {"key": "value"}
        result = add_correlation_to_log_extra(extra)

        assert result == {"key": "value"}
        assert "correlation_id" not in result
