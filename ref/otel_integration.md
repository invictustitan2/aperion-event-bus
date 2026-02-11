# OpenTelemetry Integration Guide

> Bridging EventBus correlation IDs with W3C Trace Context

## Current State

The `aperion-event-bus` uses custom correlation IDs via `contextvars`:
- `correlation.py` manages `X-Correlation-ID` header
- Works but not interoperable with OpenTelemetry ecosystem

## Why Integrate with OpenTelemetry?

1. **Industry Standard** - W3C Trace Context is the standard for distributed tracing
2. **Tooling** - Works with Jaeger, Tempo, Zipkin, Datadog, etc.
3. **Auto-instrumentation** - Framework integrations for free
4. **Baggage** - Propagate additional context (user ID, tenant ID)

## W3C Trace Context Headers

```
traceparent: 00-{trace-id}-{span-id}-{flags}
             |      |          |        |
             v      v          v        v
          version  32 hex    16 hex   2 hex

Example: traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

## Integration Approaches

### Option 1: Bridge Existing correlation.py (Recommended)

```python
# correlation.py - enhanced

from typing import Optional
import uuid
from contextvars import ContextVar

# Try to import OpenTelemetry, but don't require it
try:
    from opentelemetry import trace
    from opentelemetry.trace import get_current_span
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

_correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> Optional[str]:
    """
    Get correlation ID, preferring OpenTelemetry trace ID if available.
    """
    # First, try OpenTelemetry
    if OTEL_AVAILABLE:
        span = get_current_span()
        if span and span.is_recording():
            trace_id = span.get_span_context().trace_id
            if trace_id:
                return format(trace_id, '032x')
    
    # Fall back to our custom correlation ID
    return _correlation_id.get()


def extract_correlation_id_from_headers(headers: dict[str, str]) -> str:
    """
    Extract correlation ID from headers, supporting both custom and W3C formats.
    """
    # Try W3C traceparent first
    if OTEL_AVAILABLE and 'traceparent' in headers:
        carrier = {'traceparent': headers['traceparent']}
        ctx = TraceContextTextMapPropagator().extract(carrier=carrier)
        span_ctx = trace.get_current_span(ctx).get_span_context()
        if span_ctx.is_valid:
            return format(span_ctx.trace_id, '032x')
    
    # Try custom header
    for key, value in headers.items():
        if key.lower() == 'x-correlation-id':
            return value
    
    # Generate new
    return str(uuid.uuid4())


def get_headers_with_correlation() -> dict[str, str]:
    """
    Get headers for outgoing requests with correlation ID.
    """
    headers = {}
    correlation_id = get_correlation_id()
    
    if correlation_id:
        headers['X-Correlation-ID'] = correlation_id
    
    # Also add traceparent if OTEL is available and active
    if OTEL_AVAILABLE:
        span = get_current_span()
        if span and span.is_recording():
            TraceContextTextMapPropagator().inject(headers)
    
    return headers
```

### Option 2: Full OTEL Integration

Add optional dependency:

```toml
# pyproject.toml
[project.optional-dependencies]
otel = [
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
]
```

Create tracer module:

```python
# src/event_bus/tracing.py

from typing import Optional
from functools import wraps

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False


def get_tracer(name: str = "event_bus"):
    if OTEL_AVAILABLE:
        return trace.get_tracer(name)
    return None


def traced(span_name: Optional[str] = None):
    """Decorator to trace function execution."""
    def decorator(func):
        if not OTEL_AVAILABLE:
            return func
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            name = span_name or func.__name__
            
            with tracer.start_as_current_span(name) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise
        
        return wrapper
    return decorator
```

Usage in EventBus:

```python
from .tracing import traced, get_tracer

class EventBus:
    @traced("event_bus.emit")
    def emit(self, event_type: str, payload: dict, ...):
        tracer = get_tracer()
        if tracer:
            span = trace.get_current_span()
            span.set_attribute("event.type", event_type)
            span.set_attribute("event.source", source or "unknown")
        
        # ... rest of emit logic
```

## FastAPI Integration Example

```python
from fastapi import FastAPI, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from aperion_event_bus import EventBus, CorrelationContext

app = FastAPI()
bus = EventBus()

# Auto-instrument FastAPI
FastAPIInstrumentor.instrument_app(app)

@app.middleware("http")
async def add_correlation(request: Request, call_next):
    # OTEL already sets up trace context via instrumentation
    # Our CorrelationContext bridges it
    correlation_id = get_correlation_id()  # Now returns OTEL trace ID
    
    with CorrelationContext(correlation_id):
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

@app.post("/action")
async def action():
    # Events automatically get the OTEL trace ID
    bus.emit("user.action", {"data": "value"})
    return {"status": "ok"}
```

## Correlation ID Formats

| Source | Format | Example |
|--------|--------|---------|
| Custom UUID | 36 chars | `550e8400-e29b-41d4-a716-446655440000` |
| OTEL Trace ID | 32 hex | `4bf92f3577b34da6a3ce929d0e0e4736` |
| AWS X-Ray | Special | `1-5759e988-bd862e3fe1be46a994272793` |

## Best Practices

1. **Prefer OTEL when available** - Better tooling ecosystem
2. **Fall back gracefully** - Don't require OTEL dependency
3. **Log both IDs during transition** - Easier debugging
4. **Include in error events** - Critical for tracing failures
5. **Propagate to async tasks** - Use context copy for threads

## Propagating to Threads

```python
import contextvars
from concurrent.futures import ThreadPoolExecutor

def run_in_executor(executor, func, *args):
    """Run function in executor while preserving context."""
    ctx = contextvars.copy_context()
    return executor.submit(ctx.run, func, *args)
```

## Testing OTEL Integration

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory import InMemorySpanExporter

def test_otel_correlation():
    # Setup test tracer
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    
    tracer = trace.get_tracer("test")
    
    with tracer.start_as_current_span("test_span"):
        correlation_id = get_correlation_id()
        
        # Should return OTEL trace ID
        assert len(correlation_id) == 32
        assert all(c in '0123456789abcdef' for c in correlation_id)
```

## Sources

- [OpenTelemetry Python Propagation](https://opentelemetry.io/docs/languages/python/propagation/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [Context Propagation Concepts](https://opentelemetry.io/docs/concepts/context-propagation/)
- [Better Stack OTEL Guide](https://betterstack.com/community/guides/observability/otel-context-propagation/)
