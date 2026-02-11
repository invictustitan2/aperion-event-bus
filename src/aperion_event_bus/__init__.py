"""
Aperion Event Bus - The Nervous System.

A standalone event bus library for distributed systems that enforces:
- Constitution D (Eventing & Observability)
- Constitution B2 (PII Safety)

Features:
- Async and sync event handlers with priority ordering
- Pattern-based subscriptions ({domain}.* wildcards)
- JSONL audit logging with optional PII redaction
- Correlation ID propagation for distributed tracing
- Handler error isolation and auditing
- Backpressure control with configurable overflow policies
- Log rotation for audit files
- Optional OpenTelemetry integration
- Metrics collection with pluggable backends
- Event type validation ({domain}.{action} format)

Basic Usage:
    from pathlib import Path
    from aperion_event_bus import EventBus

    # Create bus with audit logging
    bus = EventBus(enable_audit=True, event_log_path=Path("events.jsonl"))

    # Subscribe to events
    def on_user_event(event):
        print(f"User event: {event.event_type}")

    bus.subscribe(on_user_event, "user.*", priority=100)

    # Emit events
    bus.emit("user.login", {"username": "alice"}, correlation_id="req-123")

With Correlation Context:
    from aperion_event_bus import EventBus, CorrelationContext

    bus = EventBus()

    with CorrelationContext("request-abc-123"):
        # All events in this block automatically get the correlation ID
        bus.emit("user.login", {"username": "alice"})
        bus.emit("session.created", {"session_id": "xyz"})
"""

from .audit import AuditLogger, Redactor
from .bus import BackpressureError, EventBus, EventHandler, OverflowPolicy, Subscription
from .correlation import (
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
from .dlq import DeadLetterQueue, FailedEvent, FailureReason
from .events import Event
from .metrics import (
    CallbackMetrics,
    InMemoryMetrics,
    MetricsBackend,
    MetricsCollector,
    NoopMetrics,
    TimingContext,
)
from .telemetry import (
    add_event_to_span,
    get_current_span,
    get_trace_correlation_id,
    get_tracer,
    inject_trace_context_to_headers,
    is_otel_available,
    set_span_attribute,
    traced,
)
from .validation import (
    EVENT_TYPE_PATTERN,
    EventValidator,
    ValidationError,
    ValidationResult,
    extract_action,
    extract_domain,
    is_valid_event_type,
)

__version__ = "0.3.0"  # Milestone 3: Security & Compliance

__all__ = [
    # Core
    "EventBus",
    "Event",
    "Subscription",
    "EventHandler",
    # Backpressure
    "OverflowPolicy",
    "BackpressureError",
    # Correlation
    "CorrelationContext",
    "get_correlation_id",
    "set_correlation_id",
    "clear_correlation_id",
    "generate_correlation_id",
    "extract_correlation_id_from_headers",
    "get_headers_with_correlation",
    "add_correlation_to_log_extra",
    "CORRELATION_ID_HEADER",
    # Audit
    "AuditLogger",
    "Redactor",
    # Telemetry (OpenTelemetry integration)
    "is_otel_available",
    "get_tracer",
    "get_current_span",
    "get_trace_correlation_id",
    "traced",
    "add_event_to_span",
    "set_span_attribute",
    "inject_trace_context_to_headers",
    # Metrics
    "MetricsBackend",
    "MetricsCollector",
    "NoopMetrics",
    "CallbackMetrics",
    "InMemoryMetrics",
    "TimingContext",
    # Validation
    "EventValidator",
    "ValidationError",
    "ValidationResult",
    "EVENT_TYPE_PATTERN",
    "is_valid_event_type",
    "extract_domain",
    "extract_action",
    # Dead Letter Queue (Milestone 3)
    "DeadLetterQueue",
    "FailedEvent",
    "FailureReason",
]
