"""
Tests for Dead Letter Queue (DLQ) functionality.

Milestone 3: Security & Compliance
"""

import time

from aperion_event_bus import (
    DeadLetterQueue,
    Event,
    EventBus,
    FailedEvent,
    FailureReason,
)


class TestDeadLetterQueue:
    """Unit tests for DeadLetterQueue class."""

    def test_add_failed_event(self):
        """Test adding a failed event to DLQ."""
        dlq = DeadLetterQueue(max_size=100)
        event = Event(event_type="test.event", payload={"data": "value"})

        entry_id = dlq.add(
            event=event,
            handler_name="test_handler",
            reason=FailureReason.HANDLER_ERROR,
            error="Test error",
        )

        assert dlq.size() == 1
        failed = dlq.get(entry_id)
        assert failed is not None
        assert failed.event == event
        assert failed.handler_name == "test_handler"
        assert failed.reason == FailureReason.HANDLER_ERROR
        assert failed.error == "Test error"
        assert failed.retry_count == 0

    def test_max_size_limit(self):
        """Test that DLQ respects max_size limit."""
        dlq = DeadLetterQueue(max_size=3)

        entry_ids = []
        for i in range(5):
            event = Event(event_type=f"test.event{i}", payload={"i": i})
            entry_id = dlq.add(event, FailureReason.HANDLER_ERROR, f"error {i}")
            entry_ids.append(entry_id)

        # Should only have the last 3 events
        assert dlq.size() == 3
        failed = dlq.get_all()
        event_types = [f.event.event_type for f in failed]
        assert "test.event2" in event_types
        assert "test.event3" in event_types
        assert "test.event4" in event_types
        assert "test.event0" not in event_types

    def test_remove_event(self):
        """Test removing an event from DLQ."""
        dlq = DeadLetterQueue(max_size=100)
        event = Event(event_type="test.event", payload={})
        entry_id = dlq.add(event, FailureReason.HANDLER_ERROR, "error")

        assert dlq.size() == 1
        result = dlq.remove(entry_id)
        assert result is True
        assert dlq.size() == 0

    def test_remove_nonexistent_event(self):
        """Test remove returns False for nonexistent event."""
        dlq = DeadLetterQueue(max_size=100)
        result = dlq.remove("nonexistent-id")
        assert result is False

    def test_get_by_event_type(self):
        """Test filtering by event type."""
        dlq = DeadLetterQueue(max_size=100)

        e1 = Event(event_type="domain.action1", payload={})
        e2 = Event(event_type="domain.action1", payload={})
        e3 = Event(event_type="domain.action2", payload={})

        dlq.add(e1, FailureReason.HANDLER_ERROR, "error 1")
        dlq.add(e2, FailureReason.HANDLER_ERROR, "error 2")
        dlq.add(e3, FailureReason.HANDLER_ERROR, "error 3")

        filtered = dlq.get_by_event_type("domain.action1")
        assert len(filtered) == 2
        assert all(f.event.event_type == "domain.action1" for f in filtered)

    def test_get_by_reason(self):
        """Test filtering by failure reason."""
        dlq = DeadLetterQueue(max_size=100)

        e1 = Event(event_type="test.event1", payload={})
        e2 = Event(event_type="test.event2", payload={})
        e3 = Event(event_type="test.event3", payload={})

        dlq.add(e1, FailureReason.HANDLER_ERROR, "error 1")
        dlq.add(e2, FailureReason.HANDLER_TIMEOUT, "timeout")
        dlq.add(e3, FailureReason.HANDLER_ERROR, "error 2")

        filtered = dlq.get_by_reason(FailureReason.HANDLER_ERROR)
        assert len(filtered) == 2
        assert all(f.reason == FailureReason.HANDLER_ERROR for f in filtered)

    def test_clear_older_than(self):
        """Test clearing events older than a threshold."""
        dlq = DeadLetterQueue(max_size=100)

        # Add an event
        old_event = Event(event_type="old.event", payload={})
        dlq.add(old_event, FailureReason.HANDLER_ERROR, "error")

        # Wait a moment and add another
        time.sleep(0.1)
        new_event = Event(event_type="new.event", payload={})
        dlq.add(new_event, FailureReason.HANDLER_ERROR, "error")

        # Clear events older than 0.00001 hours (~ 36ms)
        cleared = dlq.clear_older_than(hours=0.00001)

        assert cleared == 1
        assert dlq.size() == 1
        remaining = dlq.get_all()
        assert remaining[0].event.event_type == "new.event"

    def test_clear_all(self):
        """Test clearing all events."""
        dlq = DeadLetterQueue(max_size=100)

        for i in range(5):
            event = Event(event_type=f"test.event{i}", payload={})
            dlq.add(event, FailureReason.HANDLER_ERROR, f"error {i}")

        assert dlq.size() == 5
        cleared = dlq.clear()
        assert cleared == 5
        assert dlq.size() == 0

    def test_get_stats(self):
        """Test statistics collection."""
        dlq = DeadLetterQueue(max_size=100)

        # Add various failure types
        e1 = Event(event_type="domain.action1", payload={})
        e2 = Event(event_type="domain.action1", payload={})
        e3 = Event(event_type="domain.action2", payload={})
        e4 = Event(event_type="other.action", payload={})

        dlq.add(e1, FailureReason.HANDLER_ERROR, "error 1", handler_name="handler1")
        dlq.add(e2, FailureReason.HANDLER_TIMEOUT, "timeout", handler_name="handler2")
        dlq.add(e3, FailureReason.HANDLER_ERROR, "error 2", handler_name="handler1")
        dlq.add(e4, FailureReason.ALL_HANDLERS_FAILED, "all failed", handler_name="handler1")

        stats = dlq.get_stats()

        assert stats["size"] == 4
        assert stats["max_size"] == 100

        # Check by_reason breakdown (uses enum values, not names)
        assert stats["by_reason"]["handler_error"] == 2
        assert stats["by_reason"]["handler_timeout"] == 1
        assert stats["by_reason"]["all_handlers_failed"] == 1

        # Check by_event_type breakdown
        assert stats["by_event_type"]["domain.action1"] == 2
        assert stats["by_event_type"]["domain.action2"] == 1
        assert stats["by_event_type"]["other.action"] == 1

    def test_failure_reasons(self):
        """Test all failure reason types."""
        dlq = DeadLetterQueue(max_size=100)

        reasons = [
            FailureReason.HANDLER_ERROR,
            FailureReason.HANDLER_TIMEOUT,
            FailureReason.ALL_HANDLERS_FAILED,
            FailureReason.VALIDATION_ERROR,
            FailureReason.SERIALIZATION_ERROR,
        ]

        for i, reason in enumerate(reasons):
            event = Event(event_type=f"test.event{i}", payload={})
            dlq.add(event, reason, f"error {i}")

        assert dlq.size() == 5
        stats = dlq.get_stats()
        for reason in reasons:
            assert stats["by_reason"][reason.value] == 1


class TestDLQIntegration:
    """Integration tests for DLQ with EventBus."""

    def test_failed_handler_adds_to_dlq(self):
        """Test that failed handlers add events to DLQ."""
        dlq = DeadLetterQueue(max_size=100)
        bus = EventBus(dead_letter_queue=dlq)

        def failing_handler(event: Event) -> None:
            raise ValueError("Handler failure")

        bus.subscribe(failing_handler, "test.fail")
        bus.emit("test.fail", {"data": "value"}, wait_for_handlers=True)

        # Event should be in DLQ
        assert dlq.size() == 1
        failed = dlq.get_all()[0]
        assert failed.event.event_type == "test.fail"
        assert failed.reason == FailureReason.HANDLER_ERROR
        assert "Handler failure" in failed.error

    def test_timeout_handler_adds_to_dlq(self):
        """Test that timed-out handlers add events to DLQ."""
        dlq = DeadLetterQueue(max_size=100)
        bus = EventBus(dead_letter_queue=dlq, handler_timeout=0.1)

        def slow_handler(event: Event) -> None:
            time.sleep(1.0)  # Will timeout

        bus.subscribe(slow_handler, "test.slow")
        bus.emit("test.slow", {"data": "value"}, wait_for_handlers=True)

        # Event should be in DLQ with timeout reason
        assert dlq.size() == 1
        failed = dlq.get_all()[0]
        assert failed.reason == FailureReason.HANDLER_TIMEOUT

    def test_dlq_stats_in_bus_stats(self):
        """Test that DLQ stats appear in EventBus.get_stats()."""
        dlq = DeadLetterQueue(max_size=100)
        bus = EventBus(dead_letter_queue=dlq)

        def failing_handler(event: Event) -> None:
            raise RuntimeError("Failure")

        bus.subscribe(failing_handler, "test.fail")
        bus.emit("test.fail", {}, wait_for_handlers=True)

        stats = bus.get_stats()
        assert stats["dlq_enabled"] is True
        assert "dlq" in stats
        assert stats["dlq"]["size"] == 1

    def test_no_dlq_configured(self):
        """Test that EventBus works without DLQ configured."""
        bus = EventBus()  # No DLQ

        def failing_handler(event: Event) -> None:
            raise ValueError("Error")

        bus.subscribe(failing_handler, "test.fail")
        # Should not raise, just log
        bus.emit("test.fail", {}, wait_for_handlers=True)

        stats = bus.get_stats()
        assert stats["dlq_enabled"] is False
        assert "dlq" not in stats

    def test_dlq_retry_workflow(self):
        """Test the retry workflow: fail -> add to DLQ -> retry -> succeed."""
        dlq = DeadLetterQueue(max_size=100)
        bus = EventBus(dead_letter_queue=dlq)

        call_count = 0

        def sometimes_fails(event: Event) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("First call fails")
            # Subsequent calls succeed

        bus.subscribe(sometimes_fails, "test.retry")

        # First emit fails
        bus.emit("test.retry", {"attempt": 1}, wait_for_handlers=True)
        assert dlq.size() == 1
        assert call_count == 1

        # Get the failed event and retry
        failed = dlq.get_all()[0]
        success = dlq.retry(failed.id, bus)

        # Second call should succeed
        assert success is True
        assert call_count == 2
        assert dlq.size() == 0  # Removed on success


class TestFailedEvent:
    """Tests for FailedEvent dataclass."""

    def test_failed_event_fields(self):
        """Test FailedEvent stores all required fields."""
        event = Event(event_type="test.event", payload={"key": "value"})
        failed = FailedEvent(
            id="dlq_123",
            event=event,
            reason=FailureReason.HANDLER_ERROR,
            error="Something went wrong",
            handler_name="test_handler",
            retry_count=2,
        )

        assert failed.id == "dlq_123"
        assert failed.event == event
        assert failed.handler_name == "test_handler"
        assert failed.reason == FailureReason.HANDLER_ERROR
        assert failed.error == "Something went wrong"
        assert failed.retry_count == 2
        assert failed.timestamp is not None

    def test_failed_event_default_timestamp(self):
        """Test FailedEvent gets automatic timestamp."""
        before = time.time()
        event = Event(event_type="test.event", payload={})
        failed = FailedEvent(
            id="dlq_test",
            event=event,
            reason=FailureReason.HANDLER_ERROR,
            error="Test error",
        )
        after = time.time()

        assert before <= failed.timestamp <= after

    def test_failed_event_to_dict(self):
        """Test serialization to dictionary."""
        event = Event(event_type="test.event", payload={"data": "value"})
        failed = FailedEvent(
            id="dlq_123",
            event=event,
            reason=FailureReason.HANDLER_TIMEOUT,
            error="Timed out",
            handler_name="slow_handler",
        )

        result = failed.to_dict()

        assert result["id"] == "dlq_123"
        assert result["reason"] == "handler_timeout"
        assert result["error"] == "Timed out"
        assert result["handler_name"] == "slow_handler"
        assert "event" in result

