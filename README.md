# Aperion Event Bus - The Nervous System

A standalone, production-ready event bus library for Python applications. Extracted from the Aperion Legendary AI ecosystem to serve as a foundational component for distributed systems.

## Features

- **Async & Sync Handlers** - Support for both synchronous and asynchronous event handlers with priority ordering
- **Pattern Matching** - Subscribe to events using exact matches, wildcards (`chat.*`), or catch-all (`*`)
- **JSONL Audit Logging** - Persistent event log for compliance, debugging, and observability
- **Log Rotation** - Automatic log rotation with configurable size limits and backup count
- **Correlation ID Propagation** - End-to-end request tracing across distributed systems (Constitution D1)
- **PII Redaction** - Built-in redactor for 20+ sensitive data patterns (Constitution D4/B2)
- **Handler Error Isolation** - One handler's failure doesn't crash others; errors are audited
- **Dead Letter Queue** - Failed events stored for inspection and retry
- **Backpressure Control** - Configurable queue limits with DROP, RAISE, or BLOCK policies
- **Thread Safety** - Safe concurrent subscription/unsubscription during event emission
- **Handler Timeouts** - Configurable timeout for sync handlers to prevent blocking
- **Event Validation** - Enforce `{domain}.{action}` naming convention at runtime
- **Metrics Collection** - Pluggable metrics backends for observability
- **OpenTelemetry Integration** - Optional OTEL trace ID bridge for distributed tracing

## Installation

```bash
# Install from local source
pip install -e .

# Install with development dependencies
pip install -e ".[dev]"

# Install with OpenTelemetry support
pip install -e ".[otel]"

# Install with Prometheus metrics export
pip install -e ".[prometheus]"

# Install all observability features
pip install -e ".[observability]"
```

## Quick Start

```python
from pathlib import Path
from aperion_event_bus import EventBus

# Create an event bus with audit logging
bus = EventBus(
    enable_audit=True,
    event_log_path=Path("events.jsonl")
)

# Subscribe to events
def on_user_login(event):
    print(f"User logged in: {event.payload}")

bus.subscribe(on_user_login, "user.login", priority=100)

# Subscribe to all chat events using wildcard
def on_chat_event(event):
    print(f"Chat event: {event.event_type}")

bus.subscribe(on_chat_event, "chat.*")

# Emit events
bus.emit("user.login", {"username": "alice"}, correlation_id="req-123")
bus.emit("chat.message", {"text": "Hello!"})
```

## Event Naming Convention (Required)

All events **MUST** follow the `{domain}.{action}` format as mandated by Constitution D2:

| Domain | Examples |
|--------|----------|
| `user` | `user.login`, `user.logout`, `user.created` |
| `chat` | `chat.user_input`, `chat.response`, `chat.error` |
| `system` | `system.startup`, `system.shutdown`, `system.handler_error` |
| `session` | `session.created`, `session.expired` |

## Correlation ID Tracking

The Event Bus enforces correlation ID propagation per Constitution D1:

```python
from aperion_event_bus import EventBus, CorrelationContext

bus = EventBus()

# Option 1: Explicit correlation ID
bus.emit("user.login", {"user": "alice"}, correlation_id="req-abc-123")

# Option 2: Context-based (recommended)
with CorrelationContext("request-xyz-789"):
    # All events in this block automatically get the correlation ID
    bus.emit("user.login", {"user": "alice"})
    bus.emit("session.created", {"session_id": "sess-001"})
    # Events will have correlation_id="request-xyz-789"
```

### HTTP Integration

```python
from aperion_event_bus import extract_correlation_id_from_headers, CorrelationContext

# In your HTTP middleware
def middleware(request, call_next):
    correlation_id = extract_correlation_id_from_headers(dict(request.headers))
    
    with CorrelationContext(correlation_id):
        response = call_next(request)
    
    return response
```

## Priority Ordering

Handlers execute in priority order (higher number runs first):

```python
def critical_handler(event):
    print("Runs first")

def normal_handler(event):
    print("Runs second")

def low_priority_handler(event):
    print("Runs last")

bus.subscribe(critical_handler, "alert.*", priority=300)
bus.subscribe(normal_handler, "alert.*", priority=100)
bus.subscribe(low_priority_handler, "alert.*", priority=10)
```

## Error Handling

Handler errors are isolated - one failing handler doesn't affect others:

```python
def failing_handler(event):
    raise ValueError("This handler fails")

def working_handler(event):
    print("This still runs!")

bus.subscribe(failing_handler, "test.event", priority=200)
bus.subscribe(working_handler, "test.event", priority=100)

# working_handler will execute despite failing_handler's exception
bus.emit("test.event", {"data": "test"}, wait_for_handlers=True)
```

Handler errors are automatically logged as `system.handler_error` events in the audit log.

## PII Redaction

The `Redactor` class helps ensure no PII appears in audit logs (Constitution D4):

```python
from aperion_event_bus import EventBus, Redactor

# Enable PII redaction in audit logs
redactor = Redactor(
    enabled=True,
    redact_keys={"password", "ssn", "credit_card", "email"}
)

bus = EventBus(
    enable_audit=True,
    event_log_path=Path("events.jsonl"),
    redactor=redactor
)

# Payload PII will be redacted in the audit log
bus.emit("user.signup", {
    "username": "alice",
    "password": "secret123",  # → "[REDACTED]"
    "email": "alice@example.com"  # → "[REDACTED_EMAIL]"
})
```

## Statistics & Monitoring

```python
stats = bus.get_stats()
print(stats)
# {
#     "active_subscriptions": 5,
#     "total_subscriptions": 5,
#     "audit_enabled": True,
#     "max_concurrent_handlers": 10,
#     "event_types_subscribed": ["user.*", "chat.*", "system.*"],
#     "total_events_logged": 1234,
#     "log_file_size": 102400
# }

# Monitor backpressure
print(f"Pending events: {bus.get_pending_count()}")
```

## Backpressure Control

Prevent memory exhaustion under high load:

```python
from aperion_event_bus import EventBus, OverflowPolicy, BackpressureError

# DROP policy - silently drop events when queue is full
bus = EventBus(
    max_pending_events=1000,
    overflow_policy=OverflowPolicy.DROP
)

# RAISE policy - raise exception when queue is full
bus = EventBus(
    max_pending_events=1000,
    overflow_policy=OverflowPolicy.RAISE
)

try:
    bus.emit("high.volume", {"data": "..."})
except BackpressureError:
    print("Queue is full!")

# BLOCK policy - wait until space is available
bus = EventBus(
    max_pending_events=1000,
    overflow_policy=OverflowPolicy.BLOCK
)
```

## Log Rotation

Prevent disk exhaustion with automatic log rotation:

```python
bus = EventBus(
    event_log_path=Path("events.jsonl"),
    max_log_size_bytes=10 * 1024 * 1024,  # 10 MB
    max_log_files=5  # Keep 5 rotated files
)
# Creates: events.jsonl, events.1.jsonl, events.2.jsonl, ...
```

## Handler Timeouts

Prevent slow handlers from blocking:

```python
bus = EventBus(
    handler_timeout=5.0  # 5 second timeout
)

def slow_handler(event):
    time.sleep(30)  # Will be killed after 5 seconds

bus.subscribe(slow_handler, "test.*")
bus.emit("test.event", {}, wait_for_handlers=True)  # Returns after 5s, not 30s
```

## Event Validation

Enforce the `{domain}.{action}` naming convention at runtime:

```python
from aperion_event_bus import EventBus, EventValidator, ValidationError

# Enable strict validation
bus = EventBus(validate_events=True)

bus.emit("user.login", {"user": "alice"})  # OK
bus.emit("invalid", {"data": "test"})  # Raises ValidationError!

# Custom validator with allowed domains
validator = EventValidator(
    strict=True,
    allowed_domains={"user", "chat", "system"}
)
bus = EventBus(validate_events=True, validator=validator)

bus.emit("user.login", {})  # OK - domain is allowed
bus.emit("order.created", {})  # Raises ValidationError - domain not allowed
```

### Validation Helpers

```python
from aperion_event_bus import is_valid_event_type, extract_domain, extract_action

is_valid_event_type("user.login")  # True
is_valid_event_type("invalid")  # False

extract_domain("user.login")  # "user"
extract_action("user.login")  # "login"
extract_action("chat.message.sent")  # "message.sent"
```

## Metrics Collection

Collect observability metrics with pluggable backends:

```python
from aperion_event_bus import EventBus, MetricsCollector, InMemoryMetrics

# Create a metrics collector with in-memory backend (good for testing)
backend = InMemoryMetrics()
metrics = MetricsCollector(backend)

bus = EventBus(metrics=metrics)

bus.subscribe(lambda e: None, "user.*")
bus.emit("user.login", {"user": "alice"}, wait_for_handlers=True)

# Get metrics snapshot
snapshot = metrics.get_snapshot()
print(snapshot)
# {
#     "events_emitted": {"user.login": 1},
#     "events_dropped": {},
#     "total_events_emitted": 1,
#     "handler_stats": {
#         "<lambda>": {"total_calls": 1, "success_count": 1, "failure_count": 0, ...}
#     }
# }
```

### Custom Metrics Backend

```python
from aperion_event_bus import MetricsCollector, CallbackMetrics

def my_metrics_callback(metric_type, name, value, tags):
    # Send to your metrics system (Datadog, StatsD, etc.)
    print(f"{metric_type}: {name}={value} tags={tags}")

metrics = MetricsCollector(CallbackMetrics(my_metrics_callback))
bus = EventBus(metrics=metrics)
```

### Stats Include Metrics

```python
stats = bus.get_stats()
# Now includes:
# - "metrics_enabled": True
# - "validation_enabled": True
# - "metrics": {...}  # Full metrics snapshot
```

## OpenTelemetry Integration

Bridge correlation IDs with OpenTelemetry trace IDs (optional):

```python
# First, install opentelemetry: pip install -e ".[otel]"
from aperion_event_bus import is_otel_available, get_trace_correlation_id, traced

if is_otel_available():
    # Use OTEL trace ID as correlation ID
    correlation_id = get_trace_correlation_id()
    bus.emit("user.login", {"user": "alice"}, correlation_id=correlation_id)

# Wrap functions with OTEL spans
@traced(name="process_user")
def process_user(user_id):
    # This function will be traced
    pass
```

### OTEL Degradation

OpenTelemetry is optional. If not installed, all OTEL functions return no-op values:

```python
from aperion_event_bus import is_otel_available, get_trace_correlation_id

if not is_otel_available():
    # get_trace_correlation_id() returns None
    # traced() decorator is a no-op
    pass
```

## Dead Letter Queue

Failed events are stored in a Dead Letter Queue for inspection and retry:

```python
from aperion_event_bus import EventBus, DeadLetterQueue, FailureReason

# Create a DLQ with max 1000 entries
dlq = DeadLetterQueue(max_size=1000)

# Attach to EventBus
bus = EventBus(dead_letter_queue=dlq)

# Handler that sometimes fails
def flaky_handler(event):
    if event.payload.get("fail"):
        raise ValueError("Simulated failure")

bus.subscribe(flaky_handler, "test.*")

# This event will fail and go to DLQ
bus.emit("test.event", {"fail": True}, wait_for_handlers=True)

# Inspect failed events
print(f"DLQ size: {dlq.size()}")
for failed in dlq.get_all():
    print(f"Failed: {failed.event.event_type} - {failed.error}")
    print(f"Reason: {failed.reason}")

# Filter by reason
timeouts = dlq.get_by_reason(FailureReason.HANDLER_TIMEOUT)

# Retry a specific event
success = dlq.retry(failed.id, bus)

# Retry all events (with max retry limit)
results = dlq.retry_all(bus, max_retries=3)

# Clear old entries
dlq.clear_older_than(hours=24)

# Get DLQ stats (also included in bus.get_stats())
stats = dlq.get_stats()
# {
#     "size": 5,
#     "max_size": 1000,
#     "by_reason": {"handler_error": 3, "handler_timeout": 2},
#     "by_event_type": {"test.event": 5}
# }
```

### Failure Reasons

| Reason | Description |
|--------|-------------|
| `HANDLER_ERROR` | Handler raised an exception |
| `HANDLER_TIMEOUT` | Handler exceeded timeout limit |
| `ALL_HANDLERS_FAILED` | All handlers for event failed |
| `VALIDATION_ERROR` | Event failed validation |
| `SERIALIZATION_ERROR` | Event could not be serialized |

## API Reference

### EventBus

```python
EventBus(
    event_log_path: Optional[Path] = None,
    max_concurrent_handlers: int = 10,
    enable_audit: bool = True,
    redactor: Optional[Redactor] = None,
    handler_timeout: float = 30.0,
    max_pending_events: Optional[int] = None,
    overflow_policy: OverflowPolicy = OverflowPolicy.DROP,
    max_log_size_bytes: Optional[int] = None,
    max_log_files: int = 5,
    metrics: Optional[MetricsCollector] = None,
    validate_events: bool = False,
    validator: Optional[EventValidator] = None,
    dead_letter_queue: Optional[DeadLetterQueue] = None
)
```

#### Methods

| Method | Description |
|--------|-------------|
| `subscribe(handler, event_types, priority=100)` | Subscribe to events. Returns subscription ID. |
| `unsubscribe(subscription_id)` | Remove a subscription. Returns True if found. |
| `emit(event_type, payload, source=None, correlation_id=None, wait_for_handlers=False)` | Emit an event. Returns event ID. |
| `get_events(event_type=None, since=None, limit=100)` | Read events from audit log. |
| `get_stats()` | Get event bus statistics. |
| `clear_log()` | Clear the audit log. |
| `shutdown()` | Shutdown the event bus. |

### Event

```python
Event(
    event_type: str,
    payload: dict[str, Any],
    timestamp: float = <auto>,
    event_id: str = <auto>,
    source: Optional[str] = None,
    correlation_id: Optional[str] = None
)
```

Immutable event record. Use `event.to_jsonl()` and `Event.from_jsonl(line)` for serialization.

### Correlation Functions

| Function | Description |
|----------|-------------|
| `get_correlation_id()` | Get current correlation ID from context |
| `set_correlation_id(id)` | Set correlation ID in current context |
| `CorrelationContext(id)` | Context manager for correlation ID scope |
| `extract_correlation_id_from_headers(headers)` | Extract or generate correlation ID from HTTP headers |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
make test

# Run tests with coverage
make coverage

# Lint code
make lint

# Format code
make format

# Type check
make type-check
```

## Architecture

```
event_bus/
├── __init__.py      # Public API exports
├── bus.py           # EventBus class
├── events.py        # Event dataclass
├── correlation.py   # Correlation ID management
├── audit.py         # JSONL logging & Redactor
├── validation.py    # Event type validation
├── metrics.py       # Metrics collection backends
└── telemetry.py     # OpenTelemetry integration
```

## Constitution Enforcement

This library enforces the following constitutional requirements:

| Constitution | Requirement | Implementation |
|--------------|-------------|----------------|
| D1 | Correlation IDs | Auto-propagation via `CorrelationContext` |
| D2 | Event Naming | `{domain}.{action}` format enforced via `validate_events=True` |
| D4 | No Secrets/PII | `Redactor` class for audit log scrubbing |
| B2 | PII Safety | `Redactor` with configurable patterns |

## License

MIT
