"""
Event Bus Core Tests.

Comprehensive tests for the Event Bus library covering:
1. Event creation and serialization
2. Subscription and pattern matching
3. Priority ordering
4. Handler error isolation
5. Audit logging
6. Correlation ID propagation

These tests are ported from the aperion-legendary-ai repository and adapted
for the standalone library structure.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from aperion_event_bus import (
    CorrelationContext,
    Event,
    EventBus,
    Subscription,
    get_correlation_id,
)


class TestEvent:
    """Test Event data structure and serialization."""

    def test_event_creation(self):
        """Test creating Event objects."""
        event = Event(
            event_type="test.created",
            payload={"message": "test event", "data": 42},
            timestamp=1234567890.123,
            event_id="test_event_123",
        )

        assert event.event_type == "test.created"
        assert event.payload["message"] == "test event"
        assert event.payload["data"] == 42
        assert event.timestamp == 1234567890.123
        assert event.event_id == "test_event_123"

    def test_event_auto_timestamp(self):
        """Test that events get automatic timestamps."""
        before_time = time.time()
        event = Event(event_type="test.timestamp", payload={"test": True})
        after_time = time.time()

        assert before_time <= event.timestamp <= after_time
        assert isinstance(event.timestamp, float)

    def test_event_auto_id_generation(self):
        """Test that events get automatic IDs."""
        event1 = Event(event_type="test.id1", payload={})
        event2 = Event(event_type="test.id2", payload={})

        assert event1.event_id is not None
        assert event2.event_id is not None
        assert event1.event_id != event2.event_id
        assert isinstance(event1.event_id, str)
        assert len(event1.event_id) > 0

    def test_event_jsonl_serialization(self):
        """Test Event JSONL serialization and deserialization."""
        original = Event(
            event_type="chat.user_input",
            payload={
                "text": "Hello world",
                "session": "session_123",
                "metadata": {"authenticated": True},
            },
            timestamp=1234567890.456,
            event_id="event_abc123",
        )

        # Serialize to JSONL
        jsonl_str = original.to_jsonl()
        assert isinstance(jsonl_str, str)

        # Verify it's valid JSON
        data = json.loads(jsonl_str)
        assert data["event_type"] == "chat.user_input"
        assert data["payload"]["text"] == "Hello world"
        assert data["timestamp"] == 1234567890.456
        assert data["event_id"] == "event_abc123"

        # Deserialize from JSONL
        restored = Event.from_jsonl(jsonl_str)
        assert restored.event_type == original.event_type
        assert restored.payload == original.payload
        assert restored.timestamp == original.timestamp
        assert restored.event_id == original.event_id

    def test_event_jsonl_with_complex_payload(self):
        """Test JSONL serialization with complex payload."""
        complex_payload = {
            "nested": {"deep": {"value": 123}},
            "list": [1, 2, "three", {"four": 4}],
            "unicode": "Hello 世界 🌍",
            "numbers": [1.23, 4.56e-7, 1e10],
            "booleans": [True, False, None],
        }

        event = Event(event_type="test.complex", payload=complex_payload)

        # Serialize and deserialize
        jsonl_str = event.to_jsonl()
        restored = Event.from_jsonl(jsonl_str)

        assert restored.payload == complex_payload

    def test_event_immutability(self):
        """Test that Event is immutable (frozen dataclass)."""
        event = Event(event_type="test.immutable", payload={"test": True})

        # Should not be able to modify frozen dataclass fields
        with pytest.raises(AttributeError):
            event.event_type = "modified"  # type: ignore

        with pytest.raises(AttributeError):
            event.timestamp = 9999999999.0  # type: ignore


class TestSubscription:
    """Test Subscription data structure."""

    def test_subscription_creation(self):
        """Test creating Subscription objects."""

        def test_handler(event):
            pass

        subscription = Subscription(
            handler=test_handler,
            event_types={"test.created", "test.updated"},
            priority=10,
            subscription_id="sub_123",
        )

        assert subscription.subscription_id == "sub_123"
        assert subscription.handler == test_handler
        assert subscription.event_types == {"test.created", "test.updated"}
        assert subscription.priority == 10
        assert subscription.active is True

    def test_subscription_pattern_matching(self):
        """Test event pattern matching."""

        def handler(event):
            pass

        # Exact match subscription
        exact_sub = Subscription(handler=handler, event_types={"exact.event"})

        assert exact_sub.matches("exact.event")
        assert not exact_sub.matches("exact.other")
        assert not exact_sub.matches("other.event")

        # Wildcard subscription
        wildcard_sub = Subscription(handler=handler, event_types={"*"})

        assert wildcard_sub.matches("any.event")
        assert wildcard_sub.matches("another.event.type")
        assert wildcard_sub.matches("single")

        # Pattern subscription
        pattern_sub = Subscription(handler=handler, event_types={"chat.*"})

        assert pattern_sub.matches("chat.user_input")
        assert pattern_sub.matches("chat.response")
        assert not pattern_sub.matches("user.login")

        # Multiple event types subscription
        multi_sub = Subscription(
            handler=handler, event_types={"chat.message", "chat.join", "user.login"}
        )

        assert multi_sub.matches("chat.message")
        assert multi_sub.matches("chat.join")
        assert multi_sub.matches("user.login")
        assert not multi_sub.matches("system.startup")
        assert not multi_sub.matches("chat.leave")


class TestEventBusBasic:
    """Test basic EventBus functionality."""

    def test_event_bus_creation(self):
        """Test creating EventBus instances."""
        # Basic event bus
        bus = EventBus()
        assert bus is not None

        # Event bus with audit disabled
        bus_no_audit = EventBus(enable_audit=False)
        assert bus_no_audit is not None

    def test_event_bus_with_thread_pool(self):
        """Test EventBus with custom thread pool configuration."""
        bus = EventBus(max_workers=2, enable_audit=False)
        assert bus is not None

    def test_event_bus_subscription_basic(self):
        """Test basic event subscription."""
        bus = EventBus(enable_audit=False)
        received_events = []

        def test_handler(event):
            received_events.append(event)

        # Subscribe to specific event type
        sub_id = bus.subscribe(test_handler, "test.event")
        assert sub_id is not None
        assert isinstance(sub_id, str)

        # Emit event
        event_id = bus.emit("test.event", {"message": "hello"})
        assert event_id is not None

        # Give handler time to execute
        time.sleep(0.1)

        # Verify event was received
        assert len(received_events) == 1
        assert received_events[0].event_type == "test.event"
        assert received_events[0].payload["message"] == "hello"

    def test_event_bus_unsubscribe(self):
        """Test event unsubscription."""
        bus = EventBus(enable_audit=False)
        received_events = []

        def test_handler(event):
            received_events.append(event)

        # Subscribe
        sub_id = bus.subscribe(test_handler, "test.unsubscribe")

        # Emit event (should be received)
        bus.emit("test.unsubscribe", {"before": "unsubscribe"})
        time.sleep(0.1)
        assert len(received_events) == 1

        # Unsubscribe
        success = bus.unsubscribe(sub_id)
        assert success is True

        # Emit another event (should not be received)
        bus.emit("test.unsubscribe", {"after": "unsubscribe"})
        time.sleep(0.1)
        assert len(received_events) == 1  # Should still be 1

    def test_event_bus_wildcard_subscription(self):
        """Test wildcard event subscription."""
        bus = EventBus(enable_audit=False)
        received_events = []

        def wildcard_handler(event):
            received_events.append(event.event_type)

        # Subscribe to all events
        bus.subscribe(wildcard_handler, "*")

        # Emit various event types
        bus.emit("chat.user_input", {"text": "hello"})
        bus.emit("system.startup", {"time": "now"})
        bus.emit("user.login", {"user": "alice"})

        # Give handlers time to execute
        time.sleep(0.2)

        # Should have received all events
        assert len(received_events) == 3
        assert "chat.user_input" in received_events
        assert "system.startup" in received_events
        assert "user.login" in received_events

    def test_event_bus_pattern_subscription(self):
        """Test pattern-based event subscription."""
        bus = EventBus(enable_audit=False)
        chat_events = []
        system_events = []

        def chat_handler(event):
            chat_events.append(event)

        def system_handler(event):
            system_events.append(event)

        # Subscribe to chat events only
        bus.subscribe(chat_handler, "chat.*")

        # Subscribe to system events only
        bus.subscribe(system_handler, "system.*")

        # Emit various events
        bus.emit("chat.user_input", {"type": "chat"})
        bus.emit("chat.response", {"type": "chat"})
        bus.emit("system.startup", {"type": "system"})
        bus.emit("user.login", {"type": "user"})  # Should not match either

        # Give handlers time to execute
        time.sleep(0.2)

        # Verify correct filtering
        assert len(chat_events) == 2
        assert len(system_events) == 1

        assert all(event.event_type.startswith("chat.") for event in chat_events)
        assert all(event.event_type.startswith("system.") for event in system_events)


class TestEventBusAdvanced:
    """Test advanced EventBus functionality."""

    def test_event_bus_multiple_handlers(self):
        """Test multiple handlers for the same event."""
        bus = EventBus(enable_audit=False)
        handler1_events = []
        handler2_events = []
        handler3_events = []

        def handler1(event):
            handler1_events.append(event)

        def handler2(event):
            handler2_events.append(event)

        def handler3(event):
            handler3_events.append(event)

        # Subscribe all handlers to the same event
        bus.subscribe(handler1, "multi.event")
        bus.subscribe(handler2, "multi.event")
        bus.subscribe(handler3, "multi.event")

        # Emit single event
        bus.emit("multi.event", {"test": "multiple_handlers"})

        # Give handlers time to execute
        time.sleep(0.2)

        # All handlers should have received the event
        assert len(handler1_events) == 1
        assert len(handler2_events) == 1
        assert len(handler3_events) == 1

        # All should have received the same event data
        assert handler1_events[0].payload["test"] == "multiple_handlers"
        assert handler2_events[0].payload["test"] == "multiple_handlers"
        assert handler3_events[0].payload["test"] == "multiple_handlers"

    def test_event_bus_priority_ordering(self):
        """Test that handlers are called in priority order."""
        bus = EventBus(enable_audit=False)
        execution_order = []

        def high_priority_handler(event):
            execution_order.append("high")

        def medium_priority_handler(event):
            execution_order.append("medium")

        def low_priority_handler(event):
            execution_order.append("low")

        # Subscribe with different priorities (higher number = higher priority)
        bus.subscribe(low_priority_handler, "priority.test", priority=1)
        bus.subscribe(high_priority_handler, "priority.test", priority=10)
        bus.subscribe(medium_priority_handler, "priority.test", priority=5)

        # Emit event
        bus.emit("priority.test", {"test": "priority"}, wait_for_handlers=True)

        # Should execute in priority order (high to low)
        assert len(execution_order) == 3
        assert execution_order == ["high", "medium", "low"]

    def test_event_bus_handler_error_isolation(self):
        """Test that handler errors don't affect other handlers."""
        bus = EventBus(enable_audit=False)
        successful_events = []

        def failing_handler(event):
            raise Exception("Handler failed")

        def successful_handler(event):
            successful_events.append(event)

        # Subscribe both handlers
        bus.subscribe(failing_handler, "error.test")
        bus.subscribe(successful_handler, "error.test")

        # Emit event
        bus.emit("error.test", {"test": "error_isolation"}, wait_for_handlers=True)

        # Successful handler should still execute despite failing handler
        assert len(successful_events) == 1
        assert successful_events[0].payload["test"] == "error_isolation"

    def test_event_bus_concurrent_emission(self):
        """Test concurrent event emission."""
        bus = EventBus(enable_audit=False)
        received_events = []
        event_lock = threading.Lock()

        def concurrent_handler(event):
            with event_lock:
                received_events.append(event.payload["thread_id"])

        bus.subscribe(concurrent_handler, "concurrent.*")

        def emit_events(thread_id):
            for i in range(5):
                bus.emit("concurrent.test", {"thread_id": thread_id, "count": i})

        # Start multiple threads emitting events
        threads = []
        for thread_id in range(3):
            thread = threading.Thread(target=emit_events, args=(thread_id,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Give handlers time to execute
        time.sleep(0.5)

        # Should have received all events
        assert len(received_events) == 15  # 3 threads * 5 events each

        # Should have events from all threads
        unique_threads = set(received_events)
        assert unique_threads == {0, 1, 2}


class TestEventBusAuditLogging:
    """Test EventBus audit logging functionality."""

    def test_event_bus_audit_enabled(self, tmp_path):
        """Test event bus with audit logging enabled."""
        log_path = tmp_path / "audit.jsonl"
        bus = EventBus(event_log_path=log_path, enable_audit=True)

        # Emit some events
        bus.emit("audit.test1", {"message": "first"})
        bus.emit("audit.test2", {"message": "second"})

        # Give time for audit logging
        time.sleep(0.2)

        # Verify audit log was created and has content
        assert log_path.exists()
        content = log_path.read_text()
        lines = [line for line in content.split("\n") if line.strip()]

        assert len(lines) >= 2

        # Parse and verify log entries
        for line in lines:
            event_data = json.loads(line)
            assert "event_type" in event_data
            assert "payload" in event_data
            assert "timestamp" in event_data
            assert "event_id" in event_data

    def test_event_bus_audit_disabled(self, tmp_path):
        """Test event bus with audit logging disabled."""
        log_path = tmp_path / "no_audit.jsonl"
        bus = EventBus(event_log_path=log_path, enable_audit=False)

        # Emit some events
        bus.emit("no_audit.test1", {"message": "first"})
        bus.emit("no_audit.test2", {"message": "second"})

        # Give time (in case audit was enabled accidentally)
        time.sleep(0.2)

        # Verify no audit log was created
        assert not log_path.exists()

    def test_event_bus_audit_concurrent_writing(self, tmp_path):
        """Test concurrent audit log writing."""
        log_path = tmp_path / "concurrent_audit.jsonl"
        bus = EventBus(event_log_path=log_path, enable_audit=True, max_workers=4)

        def emit_batch(batch_id):
            for i in range(10):
                bus.emit(
                    "concurrent.audit",
                    {"batch_id": batch_id, "event_num": i, "timestamp": time.time()},
                )

        # Start multiple threads emitting events
        threads = []
        for batch_id in range(5):
            thread = threading.Thread(target=emit_batch, args=(batch_id,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # Give time for audit logging
        time.sleep(1.0)

        # Verify all events were logged
        assert log_path.exists()
        content = log_path.read_text()
        lines = [line for line in content.split("\n") if line.strip()]

        # Should have 50 events (5 batches * 10 events)
        assert len(lines) == 50

        # Verify all batches are represented
        batch_ids = set()
        for line in lines:
            event_data = json.loads(line)
            batch_ids.add(event_data["payload"]["batch_id"])

        assert batch_ids == {0, 1, 2, 3, 4}


class TestHandlerErrorAuditing:
    """Test handler error audit trail per Coverage Playbook."""

    def test_handler_error_emits_system_handler_error_event(self, tmp_path):
        """When a handler raises an exception, system.handler_error is logged."""
        log_file = tmp_path / "audit.jsonl"
        bus = EventBus(enable_audit=True, event_log_path=log_file)

        # Create a handler that raises an error
        def boom(event):
            raise RuntimeError("boom")

        bus.subscribe(boom, "chat.*")

        # Emit event that matches pattern
        bus.emit("chat.msg", {"t": 1}, wait_for_handlers=True)

        # Read audit log
        events = []
        if log_file.exists():
            with open(log_file) as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line))

        # Should have: 1) original event, 2) system.handler_error
        assert len(events) >= 1
        error_events = [e for e in events if e["event_type"] == "system.handler_error"]
        assert len(error_events) >= 1

        # Verify error event structure
        error_event = error_events[0]
        assert error_event["payload"]["error"] == "boom"
        assert error_event["payload"]["handler_name"] == "boom"
        assert error_event["payload"]["original_event_type"] == "chat.msg"
        assert error_event["source"] == "event_bus"

    def test_handler_error_does_not_crash_bus(self, tmp_path):
        """Event bus continues working after handler errors."""
        log_file = tmp_path / "audit.jsonl"
        bus = EventBus(enable_audit=True, event_log_path=log_file)

        call_count = {"before": 0, "after": 0}

        def failing_handler(event):
            raise ValueError("intentional failure")

        def working_handler_before(event):
            call_count["before"] += 1

        def working_handler_after(event):
            call_count["after"] += 1

        # Subscribe: working → failing → working
        bus.subscribe(working_handler_before, "test.event", priority=300)
        bus.subscribe(failing_handler, "test.event", priority=200)
        bus.subscribe(working_handler_after, "test.event", priority=100)

        # Emit event
        bus.emit("test.event", {"data": "test"}, wait_for_handlers=True)

        # Both working handlers should have executed
        assert call_count["before"] == 1
        assert call_count["after"] == 1


class TestCorrelationID:
    """Test correlation ID propagation."""

    def test_correlation_id_propagated_to_event(self, tmp_path):
        """Correlation ID passed to emit() is stored in event."""
        log_file = tmp_path / "events.jsonl"
        bus = EventBus(enable_audit=True, event_log_path=log_file)

        correlation_id = "request_abc123"
        bus.emit("test.correlation", {"data": "test"}, correlation_id=correlation_id)

        time.sleep(0.1)

        with open(log_file) as f:
            event_data = json.loads(f.readline())

        assert event_data["correlation_id"] == correlation_id

    def test_correlation_context_auto_propagation(self, tmp_path):
        """Events emitted within CorrelationContext get the context's correlation ID."""
        log_file = tmp_path / "events.jsonl"
        bus = EventBus(enable_audit=True, event_log_path=log_file)

        with CorrelationContext("ctx-123"):
            bus.emit("test.context", {"data": "in context"})

        time.sleep(0.1)

        with open(log_file) as f:
            event_data = json.loads(f.readline())

        assert event_data["correlation_id"] == "ctx-123"

    def test_nested_correlation_contexts(self):
        """Nested CorrelationContexts properly save and restore IDs."""
        with CorrelationContext("outer"):
            assert get_correlation_id() == "outer"
            with CorrelationContext("inner"):
                assert get_correlation_id() == "inner"
            assert get_correlation_id() == "outer"


class TestValidation:
    """Test input validation."""

    def test_emit_with_empty_event_type_raises_error(self):
        """Emitting event with empty string raises ValueError."""
        bus = EventBus()

        with pytest.raises(ValueError, match="event_type must be a non-empty string"):
            bus.emit("", {"data": "test"})

    def test_emit_with_non_dict_payload_raises_error(self):
        """Emitting event with non-dict payload raises ValueError."""
        bus = EventBus()

        with pytest.raises(ValueError, match="payload must be a dict"):
            bus.emit("test.event", "not a dict")  # type: ignore


class TestGetEventsAndStats:
    """Test event retrieval and statistics."""

    def test_get_events_filters_by_type(self, tmp_path):
        """get_events() filters by event type."""
        log_path = tmp_path / "events.jsonl"
        bus = EventBus(event_log_path=log_path, enable_audit=True)

        bus.emit("alpha.one", {"i": 1})
        bus.emit("beta.two", {"i": 2})
        bus.emit("alpha.one", {"i": 3})

        time.sleep(0.1)

        alphas = bus.get_events(event_type="alpha.one", limit=10)
        assert len(alphas) >= 2
        assert all(e.event_type == "alpha.one" for e in alphas)

    def test_get_stats_includes_subscription_info(self):
        """get_stats() returns subscription counts and audit status."""
        bus = EventBus(enable_audit=True, max_concurrent_handlers=10)

        bus.subscribe(lambda e: None, "event.a")
        bus.subscribe(lambda e: None, "event.b")
        bus.subscribe(lambda e: None, "event.c")

        stats = bus.get_stats()

        assert stats["active_subscriptions"] == 3
        assert stats["total_subscriptions"] == 3
        assert stats["audit_enabled"] is True
        assert stats["max_concurrent_handlers"] == 10

    def test_clear_log(self, tmp_path):
        """clear_log() removes the audit log file."""
        log_path = tmp_path / "audit.jsonl"
        bus = EventBus(event_log_path=log_path, enable_audit=True)

        bus.emit("test.event", {"data": 1})
        time.sleep(0.1)

        assert log_path.exists()
        assert bus.clear_log() is True
        assert not log_path.exists()


class TestWaitForHandlers:
    """Test wait_for_handlers functionality."""

    def test_wait_for_handlers_sync(self, tmp_path):
        """wait_for_handlers=True waits for sync handlers to complete."""
        log_path = tmp_path / "audit.jsonl"
        bus = EventBus(event_log_path=log_path, enable_audit=True)

        order: list[str] = []

        def h1(event):
            time.sleep(0.05)
            order.append("h1")

        def h2(event):
            order.append("h2")

        bus.subscribe(h2, "wait.sync", priority=5)
        bus.subscribe(h1, "wait.sync", priority=10)

        start = time.time()
        bus.emit("wait.sync", {"ok": True}, wait_for_handlers=True)
        elapsed = time.time() - start

        # Handlers must have completed before emit returns
        assert order == ["h1", "h2"]
        assert elapsed >= 0.05


class TestSyncAsyncContexts:
    """Tests for hybrid sync/async context handling (P0 fix)."""

    def test_emit_from_sync_context_with_async_handler(self, tmp_path):
        """
        Verify emit works from sync code even with async handlers.

        This tests the "Phantom Task" fix - previously calling emit()
        from sync code with async handlers would crash with:
        RuntimeError: no running event loop
        """
        bus = EventBus(event_log_path=tmp_path / "events.jsonl")
        results: list[str] = []

        async def async_handler(event):
            results.append(f"async:{event.payload['value']}")

        bus.subscribe(async_handler, "test.async")

        # This should NOT raise RuntimeError
        bus.emit("test.async", {"value": "from_sync"})

        # Give executor time to run the handler
        time.sleep(0.2)

        assert len(results) == 1
        assert results[0] == "async:from_sync"
        bus.shutdown()

    def test_emit_from_sync_with_mixed_handlers(self, tmp_path):
        """Test emit from sync context with both sync and async handlers."""
        bus = EventBus(event_log_path=tmp_path / "events.jsonl")
        results: list[str] = []

        def sync_handler(event):
            results.append(f"sync:{event.payload['value']}")

        async def async_handler(event):
            results.append(f"async:{event.payload['value']}")

        bus.subscribe(sync_handler, "test.mixed", priority=10)
        bus.subscribe(async_handler, "test.mixed", priority=5)

        bus.emit("test.mixed", {"value": "test"})

        # Give handlers time to complete
        time.sleep(0.3)

        assert len(results) == 2
        assert "sync:test" in results
        assert "async:test" in results
        bus.shutdown()

    def test_executor_exceptions_not_swallowed(self, tmp_path, capsys):
        """
        Verify that exceptions in executor tasks are surfaced.

        The _handle_executor_exception callback should print errors
        instead of silently swallowing them.
        """
        bus = EventBus(event_log_path=tmp_path / "events.jsonl")

        def bad_handler(event):
            raise ValueError("intentional failure")

        bus.subscribe(bad_handler, "test.fail")

        # This should not crash, but should log the error
        bus.emit("test.fail", {"trigger": "error"})

        # Give executor time to run
        time.sleep(0.2)

        bus.shutdown()

        # The error should have been logged to audit
        events = bus.get_events(event_type="system.handler_error")
        assert len(events) >= 1
        assert "intentional failure" in events[0].payload["error"]
