# Authoritative Sources

> Reference materials for aperion-event-bus development

## Official Documentation

### Python AsyncIO
- [asyncio — Asynchronous I/O](https://docs.python.org/3/library/asyncio.html)
- [Developing with asyncio](https://docs.python.org/3/library/asyncio-dev.html)
- [concurrent.futures](https://docs.python.org/3/library/concurrent.futures.html)

### OpenTelemetry
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)
- [Context Propagation](https://opentelemetry.io/docs/concepts/context-propagation/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)

### Logging
- [Python Logging HOWTO](https://docs.python.org/3/howto/logging.html)
- [structlog Documentation](https://www.structlog.org/en/stable/)

---

## Production Event Bus Libraries

### bubus
- **URL:** https://github.com/browser-use/bubus
- **Features:** Pydantic, WAL, async/sync, nested events
- **License:** MIT
- **Relevance:** Modern production-ready reference implementation

### blinker
- **URL:** https://github.com/pallets-eco/blinker
- **Features:** Simple signals library
- **License:** MIT
- **Relevance:** Lightweight alternative

---

## Best Practices Articles

### Async Event Bus Patterns
- [Events in Python: A Deep Guide](https://martinuke0.github.io/posts/2025-12-07-events-in-python-a-deep-unforgettable-guide-to-event-driven-thinking/)
- [Event-Driven Architecture with asyncio](https://johal.in/event-driven-architecture-patterns-using-python-asyncio-for-reactive-systems/)
- [Building an Event Bus in Python](https://www.joeltok.com/posts/2021-03-building-an-event-bus-in-python/)

### Async/Await Production
- [Python Async/Await Playbook](https://www.dataannotation.tech/developers/python-async-await-best-practices)
- [AsyncIO Concurrency Guide](https://www.inexture.com/python-async-await-concurrency-optimization/)
- [Event Loop Optimization](https://dhirendrabiswal.com/advanced-python-concurrency-asyncio-event-loop-optimization-and-concurrent-futures-patterns/)

### Logging
- [10 Best Practices for Python Logging](https://betterstack.com/community/guides/logging/python/python-logging-best-practices/)
- [Logging Best Practices - structlog](https://www.structlog.org/en/stable/logging-best-practices.html)
- [Python Logging Configuration](https://calmops.com/programming/python/python-logging-configuration-best-practices/)

### OpenTelemetry
- [OTEL Context Propagation](https://betterstack.com/community/guides/observability/otel-context-propagation/)
- [Distributed Tracing Demo](https://github.com/isogram/distributed-tracing-demo)
- [OTEL Best Practices](https://johal.in/observability-best-practices-using-opentelemetry-with-python-for-distributed-tracing/)

---

## PII Detection & GDPR

### Libraries
- [OpenRedaction](https://openredaction.com/) - 500+ patterns, AI-enhanced
- [piisa/pii-extract-plg-regex](https://github.com/piisa/pii-extract-plg-regex) - Language-aware
- [RedactPII](https://github.com/wrannaman/redact-pii-python) - Zero-dependency
- [Microsoft Presidio](https://github.com/microsoft/presidio) - ML-based

### Articles
- [BigCode PII Detection](https://deepwiki.com/bigcode-project/bigcode-dataset/3.3-regex-based-pii-detection)
- [GDPR Safe RAG](https://dev.to/charles_nwankpa/introducing-gdpr-safe-rag-build-gdpr-compliant-rag-systems-in-minutes-4ap4)

---

## Known Bugs & Issues

### CPython Issues
- [ThreadPoolExecutor loses exceptions #120508](https://github.com/python/cpython/issues/120508)

### Stack Overflow Discussions
- [asyncio + ThreadPoolExecutor](https://stackoverflow.com/questions/69728812/asyncio-and-concurrent-futures-threadpoolexecutor)
- [Debugging asyncio Concurrency](https://markaicode.com/debugging-python-asyncio-concurrency-issues/)

---

## Backpressure & Queuing

- [Backpressure Handling Patterns](https://softwarepatternslexicon.com/python/reactive-programming-patterns/backpressure-handling/)
- [Little's Law and Back Pressure](https://gist.github.com/rponte/8489a7acf95a3ba61b6d012fd5b90ed3)
- [Async Generator Backpressure](https://johal.in/async-generator-patterns-python-yield-from-asyncio-for-backpressure-2026/)
- [Understanding Back Pressure](https://akashrajpurohit.com/blog/understanding-back-pressure-in-message-queues-a-guide-for-developers/)

---

## Message Queue References

For future distributed backend options:

### Redis Streams
- [Build Type-Safe Event Systems](https://python.elitedev.in/python/build-type-safe-event-driven-systems-python-asyncio-pydantic--redis-streams-complete-guide-3c284558/)

### Kafka
- [aiokafka](https://github.com/aio-libs/aiokafka)

---

## Aperion-Specific

### Source Repository
- `/home/dreamboat/projects/aperion/aperion-legendary-ai-main`

### Original Implementation
- `stack/aperion/foundation/event_bus.py`
- `stack/aperion/foundation/correlation.py`

### Tests (Original)
- `testing/backend/test_event_bus_comprehensive.py`
- `testing/backend/test_event_bus_additional.py`
- `testing/backend/test_event_bus_extra.py`
- `testing/backend/test_event_bus_critical_paths.py`

### Constitution References
- **D1:** Correlation IDs required
- **D2:** Event naming `{domain}.{action}`
- **D4:** No secrets/PII in logs
- **B2:** PII safety

---

## Last Updated
2026-02-08
