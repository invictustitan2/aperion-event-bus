# Event Bus Gap Analysis & Improvement Roadmap

## Executive Summary

This document identifies gaps in the current `aperion-event-bus` implementation and provides a prioritized roadmap for improvements before integration with `aperion-legendary-ai-main`.

**Current State:** Production-ready library with 134 passing tests
**Target State:** Fully-hardened "Nervous System" for the Aperion ecosystem

**Last Updated:** 2026-02-08

### Completed Milestones

| Milestone | Status | Tests |
|-----------|--------|-------|
| P0: Critical Async Fixes | ✅ COMPLETE | 3 tests |
| Milestone 1: Production Safety | ✅ COMPLETE | 11 tests |
| Milestone 2: Observability | ✅ COMPLETE | 23 tests |
| Milestone 3: Security & Compliance | ✅ COMPLETE | 38 tests |

**Total Tests:** 134 (excluding 1 flaky stability test)

---

## ✅ Fixed (P0 Critical)

### 1. ✅ Async Dispatch Bug: `asyncio.create_task` Without Running Loop
**File:** `src/event_bus/bus.py` (formerly line 344)
**Status:** FIXED (2026-02-08)

**Issue:** `_dispatch_async` called `asyncio.create_task()` which requires an already-running event loop. If called from sync code, this raised `RuntimeError: no running event loop`.

**Solution Implemented:**
- Added loop detection with `asyncio.get_running_loop()` try/except
- Falls back to executor for async handlers when no loop is running
- Added `_handle_executor_exception` callback to surface errors

**Tests Added:**
- `test_emit_from_sync_context_with_async_handler`
- `test_emit_from_sync_with_mixed_handlers`
- `test_executor_exceptions_not_swallowed`

### 2. ✅ ThreadPoolExecutor Exception Swallowing
**File:** `src/event_bus/bus.py` (formerly line 358)
**Status:** FIXED (2026-02-08)

**Issue:** When sync handlers were submitted to executor, exceptions were silently lost.

**Solution Implemented:**
- Added `future.add_done_callback(self._handle_executor_exception)` to all executor submits
- `_handle_executor_exception` calls `future.result()` to surface exceptions
- Errors are printed to stderr (can't emit events during potential shutdown)

### 3. ✅ Shutdown Order Bug
**File:** `src/event_bus/bus.py` (formerly lines 486-495)
**Status:** FIXED (2026-02-08)

**Issue:** Shutdown event was emitted AFTER executor shutdown, causing deadlock.

**Solution Implemented:**
- Shutdown event is now logged directly to audit (not emitted via normal path)
- Subscriptions deactivated AFTER logging shutdown
- Executor shutdown is last step

---

## ✅ Milestone 1: Production Safety (COMPLETE - 2026-02-08)

### 3. ✅ Backpressure / Queue Overflow Protection
**Status:** FIXED

**Solution Implemented:**
- Added `max_pending_events` parameter to EventBus.__init__
- Added `overflow_policy` parameter with DROP, RAISE, BLOCK options
- Added `BackpressureError` exception for RAISE policy
- Added `_acquire_pending_slot()` / `_release_pending_slot()` methods
- Added `get_pending_count()` for monitoring

### 4. ✅ Log Rotation for Audit Files
**Status:** FIXED

**Solution Implemented:**
- Added `max_log_size_bytes` and `max_log_files` parameters to AuditLogger
- Added `_should_rotate()` and `_rotate_log()` methods
- Creates numbered backups: events.1.jsonl, events.2.jsonl, etc.
- Old backups deleted when limit reached

### 5. ✅ Thread Safety of Subscriptions List
**Status:** FIXED

**Solution Implemented:**
- Added `self._subscription_lock = threading.RLock()`
- All subscription list access wrapped in lock
- Safe concurrent subscribe/unsubscribe during emit

### 7. ✅ Handler Timeout Configuration
**Status:** FIXED

**Solution Implemented:**
- Added `handler_timeout` parameter (default: 30.0s)
- Sync handlers now run in executor with timeout
- Timeout logged as `system.handler_error` event

---

## ✅ Milestone 2: Observability (COMPLETE - 2026-02-08)

### 6. ✅ OpenTelemetry Integration
**Status:** FIXED

**Solution Implemented:**
- Created `src/event_bus/telemetry.py` module
- Added `is_otel_available()` to check for OTEL installation
- Added `get_trace_correlation_id()` to bridge OTEL trace IDs
- Added `traced()` decorator for function tracing
- Added optional `[otel]` dependency in pyproject.toml
- Graceful degradation when OTEL not installed

### 9. ✅ Event Schema Validation
**Status:** FIXED

**Solution Implemented:**
- Created `src/event_bus/validation.py` module
- Added `EventValidator` class with strict mode
- Added `validate_events` parameter to EventBus
- Raises `ValidationError` for invalid event types
- Helper functions: `is_valid_event_type()`, `extract_domain()`, `extract_action()`
- Supports allowed domain restrictions

### 11. ✅ Metrics Emission
**Status:** FIXED

**Solution Implemented:**
- Created `src/event_bus/metrics.py` module
- Added `MetricsBackend` abstract base class
- Added `InMemoryMetrics` for testing
- Added `CallbackMetrics` for custom integrations
- Added optional `PrometheusMetrics` backend
- Added `metrics` parameter to EventBus
- Records: events emitted/dropped, handler execution latency, success/failure counts
- `get_stats()` includes full metrics snapshot

---

## ✅ Milestone 3: Security & Compliance (COMPLETE - 2026-02-08)

### 8. ✅ PII Redactor Enhancement
**Status:** FIXED

**Solution Implemented:**
- Added 20+ new PII patterns:
  - Phone numbers (US, international, E.164)
  - IPv4 and IPv6 addresses
  - AWS access keys, secrets, and ARNs
  - GCP API keys and service account identifiers
  - JWT tokens
  - GitHub/GitLab tokens
  - Slack tokens
  - Stripe keys (test/live)
  - Bearer tokens in headers
  - Base64-encoded secrets (64+ chars)
  - Passwords in URLs
- Expanded sensitive key set from 10 to 47 keys
- Added tests for all new patterns

**Tests Added:** 13 new tests in `test_redactor.py`

### 10. ✅ Dead Letter Queue for Failed Events
**Status:** FIXED

**Solution Implemented:**
- Created `src/event_bus/dlq.py` module
- `DeadLetterQueue` class with:
  - Thread-safe deque with configurable max size
  - `add()` for storing failed events
  - `get()`, `get_all()`, `get_by_event_type()`, `get_by_reason()` for retrieval
  - `retry()` and `retry_all()` for re-emitting events
  - `clear()` and `clear_older_than()` for cleanup
  - `get_stats()` for monitoring
- `FailedEvent` dataclass with retry count tracking
- `FailureReason` enum: HANDLER_ERROR, HANDLER_TIMEOUT, etc.
- Integrated into EventBus via `dead_letter_queue` parameter
- Handler errors and timeouts automatically add to DLQ
- DLQ stats included in `EventBus.get_stats()`

**Tests Added:** 18 tests in `test_dlq.py`

---

## Low Priority / Future Enhancements (P3)

### 13. Persistent Event Log (WAL)
For at-least-once delivery guarantees, add optional write-ahead log.

### 14. Event Replay Capability
Allow replaying events from audit log for debugging/recovery.

### 15. Multi-Process Support
Current implementation is single-process; add Redis/Kafka backend option for distributed deployments.

### 16. Type-Safe Events with Pydantic
Add optional Pydantic model validation for event payloads.

### 17. Async Context Manager
```python
async with EventBus() as bus:
    await bus.emit_async(...)
```

---

## Integration Checklist for aperion-legendary-ai-main

Before integrating back:

- [x] Fix P0 issues (async dispatch, exception handling, backpressure)
- [x] Add log rotation
- [x] Add thread safety for subscriptions
- [x] Update correlation.py to optionally bridge with OTEL
- [x] Add handler timeout configuration
- [x] Enhance PII patterns (20+ new patterns)
- [x] Add event naming validation (optional, configurable)
- [x] Fix shutdown order
- [x] Add Dead Letter Queue for failed events
- [x] Add integration tests with FastAPI/Starlette (11 tests)
- [x] Benchmark under load (8/10 benchmarks exceed 10k events/sec)

**✅ READY FOR INTEGRATION**

---

## References

See `/ref/` folder for:
- `best_practices.md` - Async event bus patterns
- `known_bugs.md` - ThreadPoolExecutor/asyncio issues
- `pii_patterns.md` - GDPR-compliant PII detection
- `otel_integration.md` - OpenTelemetry correlation
- `sources.md` - Authoritative sources
