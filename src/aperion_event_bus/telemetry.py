"""
OpenTelemetry integration for the Event Bus.

Provides optional bridging between EventBus correlation IDs and
W3C Trace Context / OpenTelemetry spans.

This module gracefully degrades if OpenTelemetry is not installed,
allowing the library to work without OTEL as a required dependency.

Usage:
    from aperion_event_bus.telemetry import get_trace_correlation_id, traced

    # Get correlation ID from OTEL span if available
    correlation_id = get_trace_correlation_id()

    # Decorator to trace function execution
    @traced("my_operation")
    def my_function():
        pass
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# Try to import OpenTelemetry, but don't require it
try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, SpanKind, Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None  # type: ignore
    Status = None  # type: ignore
    StatusCode = None  # type: ignore
    Span = None  # type: ignore
    SpanKind = None  # type: ignore
    TraceContextTextMapPropagator = None  # type: ignore


F = TypeVar("F", bound=Callable[..., Any])


def is_otel_available() -> bool:
    """Check if OpenTelemetry is available."""
    return OTEL_AVAILABLE


def get_tracer(name: str = "event_bus") -> Any:
    """
    Get an OpenTelemetry tracer.

    Args:
        name: Name for the tracer

    Returns:
        Tracer instance if OTEL available, None otherwise
    """
    if not OTEL_AVAILABLE:
        return None
    return trace.get_tracer(name)


def get_current_span() -> Any:
    """
    Get the current OpenTelemetry span.

    Returns:
        Current span if OTEL available and span exists, None otherwise
    """
    if not OTEL_AVAILABLE:
        return None
    return trace.get_current_span()


def get_trace_correlation_id() -> str | None:
    """
    Get correlation ID from the current OpenTelemetry trace context.

    Returns the trace ID formatted as a 32-character hex string,
    compatible with standard correlation ID usage.

    Returns:
        Trace ID as hex string if OTEL span is active, None otherwise
    """
    if not OTEL_AVAILABLE:
        return None

    span = trace.get_current_span()
    if span and span.is_recording():
        trace_id = span.get_span_context().trace_id
        if trace_id:
            return format(trace_id, "032x")
    return None


def extract_trace_context_from_headers(headers: dict[str, str]) -> str | None:
    """
    Extract trace ID from W3C traceparent header.

    Args:
        headers: HTTP headers dict

    Returns:
        Trace ID if traceparent header present and valid, None otherwise
    """
    if not OTEL_AVAILABLE or "traceparent" not in headers:
        return None

    try:
        carrier = {"traceparent": headers["traceparent"]}
        propagator = TraceContextTextMapPropagator()
        ctx = propagator.extract(carrier=carrier)

        # Get span context from the extracted context
        span = trace.get_current_span(ctx)
        if span:
            span_ctx = span.get_span_context()
            if span_ctx and span_ctx.is_valid:
                return format(span_ctx.trace_id, "032x")
    except Exception as e:
        logger.debug(f"Failed to extract trace context: {e}")

    return None


def inject_trace_context_to_headers(headers: dict[str, str]) -> dict[str, str]:
    """
    Inject current trace context into HTTP headers.

    Args:
        headers: Existing headers dict

    Returns:
        Headers dict with traceparent added if OTEL span is active
    """
    if not OTEL_AVAILABLE:
        return headers

    try:
        span = trace.get_current_span()
        if span and span.is_recording():
            propagator = TraceContextTextMapPropagator()
            propagator.inject(headers)
    except Exception as e:
        logger.debug(f"Failed to inject trace context: {e}")

    return headers


def traced(
    span_name: str | None = None,
    kind: Any = None,
    attributes: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """
    Decorator to trace function execution with OpenTelemetry.

    If OpenTelemetry is not available, this is a no-op decorator.

    Args:
        span_name: Name for the span (defaults to function name)
        kind: SpanKind (defaults to INTERNAL)
        attributes: Additional span attributes

    Returns:
        Decorated function

    Usage:
        @traced("process_event")
        def process_event(event):
            pass

        @traced("http_request", kind=SpanKind.CLIENT)
        async def make_request():
            pass
    """

    def decorator(func: F) -> F:
        if not OTEL_AVAILABLE:
            return func

        name = span_name or func.__name__
        span_kind = kind if kind is not None else SpanKind.INTERNAL

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(name, kind=span_kind) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(name, kind=span_kind) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator


def add_event_to_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    """
    Add an event to the current span.

    Args:
        name: Event name
        attributes: Event attributes
    """
    if not OTEL_AVAILABLE:
        return

    span = get_current_span()
    if span and span.is_recording():
        span.add_event(name, attributes=attributes or {})


def set_span_attribute(key: str, value: Any) -> None:
    """
    Set an attribute on the current span.

    Args:
        key: Attribute key
        value: Attribute value
    """
    if not OTEL_AVAILABLE:
        return

    span = get_current_span()
    if span and span.is_recording():
        span.set_attribute(key, value)
