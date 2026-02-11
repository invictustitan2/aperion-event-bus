"""
Correlation ID management for distributed tracing.

Provides end-to-end request tracing through correlation IDs that propagate
across HTTP headers, Event Bus events, logs, and all downstream calls.

This is a foundational observability primitive (Constitution D1) that enables:
- Request tracing across services
- Log aggregation and correlation
- Debugging distributed operations
- Performance analysis

Usage:
    from aperion_event_bus.correlation import (
        get_correlation_id,
        set_correlation_id,
        CorrelationContext
    )

    # Set correlation ID for current context
    with CorrelationContext("request-abc-123"):
        # All operations in this block have access to correlation_id
        bus.emit("event.type", payload)

    # In logs
    logger.info("Operation complete", extra={"correlation_id": get_correlation_id()})
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

# Context variable for storing correlation ID in async contexts
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Header name for correlation ID (HTTP integration)
CORRELATION_ID_HEADER = "X-Correlation-ID"

logger = logging.getLogger(__name__)


def generate_correlation_id() -> str:
    """
    Generate a new correlation ID.

    Returns:
        A UUID4 string suitable for correlation tracking
    """
    return str(uuid.uuid4())


def get_correlation_id() -> str | None:
    """
    Get the current correlation ID from context.

    Returns:
        The correlation ID if set, None otherwise
    """
    return _correlation_id.get()


def set_correlation_id(correlation_id: str) -> None:
    """
    Set the correlation ID in the current context.

    Args:
        correlation_id: The correlation ID to set
    """
    _correlation_id.set(correlation_id)


def clear_correlation_id() -> None:
    """Clear the correlation ID from the current context."""
    _correlation_id.set(None)


def extract_correlation_id_from_headers(headers: dict[str, str]) -> str:
    """
    Extract correlation ID from HTTP request headers or generate a new one.

    Args:
        headers: HTTP request headers (case-insensitive dict)

    Returns:
        Correlation ID from header if present, otherwise a new generated ID
    """
    # Check for correlation ID in headers (case-insensitive)
    for key, value in headers.items():
        if key.lower() == CORRELATION_ID_HEADER.lower() and value and isinstance(value, str):
            logger.debug(f"Extracted correlation ID from header: {value}")
            return value

    # Generate new correlation ID if not provided
    new_id = generate_correlation_id()
    logger.debug(f"Generated new correlation ID: {new_id}")
    return new_id


class CorrelationContext:
    """
    Context manager for correlation ID management.

    Automatically sets and clears correlation ID for the duration of the context.
    Supports nested contexts by preserving and restoring the previous ID.

    Usage:
        with CorrelationContext(correlation_id):
            # All operations in this block have access to correlation_id
            do_work()

        # Nested contexts
        with CorrelationContext("outer"):
            with CorrelationContext("inner"):
                assert get_correlation_id() == "inner"
            assert get_correlation_id() == "outer"
    """

    def __init__(self, correlation_id: str | None = None):
        """
        Initialize correlation context.

        Args:
            correlation_id: Correlation ID to use, or None to generate a new one
        """
        self.correlation_id = correlation_id or generate_correlation_id()
        self.previous_id: str | None = None

    def __enter__(self) -> str:
        """Enter the correlation context."""
        self.previous_id = get_correlation_id()
        set_correlation_id(self.correlation_id)
        logger.debug(f"Entered correlation context: {self.correlation_id}")
        return self.correlation_id

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit the correlation context and restore previous ID."""
        if self.previous_id:
            set_correlation_id(self.previous_id)
        else:
            clear_correlation_id()
        logger.debug(f"Exited correlation context: {self.correlation_id}")


def get_headers_with_correlation() -> dict[str, str]:
    """
    Get HTTP headers dict with correlation ID included.

    Returns:
        Dictionary with X-Correlation-ID header if correlation ID is set
    """
    correlation_id = get_correlation_id()
    if correlation_id:
        return {CORRELATION_ID_HEADER: correlation_id}
    return {}


def add_correlation_to_log_extra(extra: dict[str, object] | None = None) -> dict[str, object]:
    """
    Add correlation ID to logging extra dict.

    Args:
        extra: Existing extra dict or None

    Returns:
        Dict with correlation_id added if available
    """
    result: dict[str, object] = extra.copy() if extra else {}
    correlation_id = get_correlation_id()
    if correlation_id:
        result["correlation_id"] = correlation_id
    return result
