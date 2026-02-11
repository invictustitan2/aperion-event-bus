# Python Async Event Bus Best Practices

> Compiled from production patterns and authoritative sources (2024-2025)

## Core Principles

### 1. Decoupling & Flexibility
- Producers should not know who is listening
- Listeners can subscribe/unsubscribe dynamically
- Use publish/subscribe with clear event names and type-safe payloads

### 2. Type Safety & Validation
- Use Pydantic for event schema validation
- Catch serialization errors early
- Consider typed event classes vs generic dicts

### 3. Async-First Design
- Handlers should be async-aware
- Use `asyncio` for I/O-bound workloads
- Never block the event loop with sync operations

## Production Patterns

### Bounded Queues for Backpressure

```python
import asyncio

class EventBus:
    def __init__(self, max_queue_size: int = 10000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
    
    async def emit(self, event):
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Policy: drop, block, or error
            raise BackpressureError("Event queue full")
```

### Error Isolation

```python
async def _dispatch(self, event, handlers):
    for handler in handlers:
        try:
            await handler(event)
        except Exception as e:
            # Log but don't propagate
            self._log_handler_error(handler, event, e)
            # Continue to next handler
```

### Graceful Shutdown

```python
async def shutdown(self):
    # 1. Stop accepting new events
    self._accepting = False
    
    # 2. Drain pending events (with timeout)
    try:
        await asyncio.wait_for(self._drain_queue(), timeout=30)
    except asyncio.TimeoutError:
        self._log_warning("Shutdown timeout - some events may be lost")
    
    # 3. Cancel running handlers
    for task in self._running_tasks:
        task.cancel()
```

### Priority Ordering

```python
# Use heapq for O(log n) insertion
import heapq

class PrioritySubscription:
    def __init__(self, priority: int, handler):
        self.priority = -priority  # Negate for max-heap behavior
        self.handler = handler
    
    def __lt__(self, other):
        return self.priority < other.priority

# Or simpler: sort on subscribe
self.subscriptions.sort(key=lambda s: s.priority, reverse=True)
```

## Pattern Matching Best Practices

### Wildcard Patterns

```python
def matches(self, event_type: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return event_type.startswith(prefix + ".")
    return event_type == pattern
```

### Avoid Regex for Simple Patterns
- Regex is overkill for `domain.*` patterns
- Pre-compile if regex is needed
- Cache pattern matching results if patterns are static

## Concurrency Considerations

### Don't Mix asyncio.run() with Running Loops

```python
# BAD: Will raise RuntimeError if loop is running
def _dispatch_async(self, event):
    asyncio.create_task(self._handle(event))  # Requires running loop!

# GOOD: Check for running loop
def _dispatch_async(self, event):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(self._handle(event))
    except RuntimeError:
        # No loop running - use executor
        self.executor.submit(asyncio.run, self._handle(event))
```

### ThreadPoolExecutor Guidelines

1. Always check `future.result()` or add done callback
2. Don't share mutable state without locks
3. Use `run_in_executor` for blocking calls from async code
4. Consider `ProcessPoolExecutor` for CPU-bound work

## Performance Tips

1. **Use uvloop** on Linux for 2-4x faster event loop
2. **Batch events** when possible to reduce overhead
3. **Monitor queue sizes** for early warning of backpressure
4. **Profile handler latency** - slow handlers block the queue

## Libraries to Consider

| Library | Use Case | Notes |
|---------|----------|-------|
| `bubus` | Production event bus | Pydantic, WAL, async/sync |
| `aiokafka` | Distributed events | At-scale streaming |
| `redis.asyncio` | Pub/sub with persistence | Good for microservices |
| `structlog` | Structured logging | Correlates with events |

## Anti-Patterns to Avoid

1. **Unbounded queues** - Will exhaust memory under load
2. **Sync handlers in async code** - Blocks event loop
3. **No error isolation** - One handler crash kills all
4. **Missing correlation IDs** - Impossible to trace
5. **Logging PII in events** - Compliance violation

## Sources

- [bubus - Production Event Bus](https://github.com/browser-use/bubus)
- [Event-Driven Architecture with asyncio](https://johal.in/event-driven-architecture-patterns-using-python-asyncio-for-reactive-systems/)
- [Python AsyncIO Concurrency Guide](https://www.inexture.com/python-async-await-concurrency-optimization/)
- [Events in Python Deep Guide](https://martinuke0.github.io/posts/2025-12-07-events-in-python-a-deep-unforgettable-guide-to-event-driven-thinking/)
