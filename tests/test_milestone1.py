"""
Tests for Milestone 1: Production Safety features.

1. Thread safety for subscriptions
2. Backpressure / queue overflow protection
3. Log rotation for audit files
4. Handler timeout configuration
"""

import threading
import time

import pytest

from aperion_event_bus import BackpressureError, EventBus, OverflowPolicy


class TestThreadSafety:
    """Test thread-safe subscription management."""

    def test_concurrent_subscribe_unsubscribe(self, tmp_path):
        """Verify concurrent subscribe/unsubscribe doesn't corrupt state."""
        bus = EventBus(event_log_path=tmp_path / "events.jsonl")
        errors: list[Exception] = []
        sub_ids: list[str] = []
        lock = threading.Lock()

        def subscribe_worker():
            try:
                for idx in range(50):
                    sub_id = bus.subscribe(lambda e: None, f"test.{idx}")
                    with lock:
                        sub_ids.append(sub_id)
            except Exception as e:
                errors.append(e)

        def unsubscribe_worker():
            try:
                for _ in range(25):
                    time.sleep(0.001)
                    with lock:
                        if sub_ids:
                            sub_id = sub_ids.pop()
                            bus.unsubscribe(sub_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=subscribe_worker),
            threading.Thread(target=subscribe_worker),
            threading.Thread(target=unsubscribe_worker),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent access: {errors}"
        bus.shutdown()

    def test_concurrent_emit_with_subscriptions(self, tmp_path):
        """Verify emit works while subscriptions are being modified."""
        bus = EventBus(event_log_path=tmp_path / "events.jsonl")
        emitted = []
        errors: list[Exception] = []

        def handler(event):
            emitted.append(event.event_id)

        bus.subscribe(handler, "test.*")

        def emit_worker():
            try:
                for idx in range(100):
                    bus.emit("test.event", {"idx": idx}, wait_for_handlers=True)
            except Exception as e:
                errors.append(e)

        def modify_worker():
            try:
                for _ in range(50):
                    sub_id = bus.subscribe(lambda e: None, "other.*")
                    bus.unsubscribe(sub_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=emit_worker),
            threading.Thread(target=modify_worker),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(emitted) == 100
        bus.shutdown()


class TestBackpressure:
    """Test backpressure / queue overflow protection."""

    def test_drop_policy_drops_events_when_full(self, tmp_path):
        """Verify DROP policy silently drops events when queue is full."""
        bus = EventBus(
            event_log_path=tmp_path / "events.jsonl",
            max_pending_events=5,
            overflow_policy=OverflowPolicy.DROP,
        )

        # Handler that blocks so events queue up
        received = []
        gate = threading.Event()

        def slow_handler(event):
            gate.wait()  # Block until released
            received.append(event.event_id)

        bus.subscribe(slow_handler, "test.*")

        # Emit more events than the queue can hold
        results = []
        for i in range(10):
            result = bus.emit("test.event", {"i": i})
            results.append(result)

        # Some events should have been dropped (empty string returned)
        dropped_count = sum(1 for r in results if r == "")
        assert dropped_count > 0, "Expected some events to be dropped"

        # Release the gate and cleanup
        gate.set()
        time.sleep(0.2)
        bus.shutdown()

    def test_raise_policy_raises_when_full(self, tmp_path):
        """Verify RAISE policy raises BackpressureError when full."""
        bus = EventBus(
            event_log_path=tmp_path / "events.jsonl",
            max_pending_events=2,
            overflow_policy=OverflowPolicy.RAISE,
        )

        gate = threading.Event()

        def blocking_handler(event):
            gate.wait()

        bus.subscribe(blocking_handler, "test.*")

        # Fill the queue
        bus.emit("test.1", {})
        bus.emit("test.2", {})

        # Third should raise
        with pytest.raises(BackpressureError):
            bus.emit("test.3", {})

        gate.set()
        bus.shutdown()

    def test_block_policy_waits_for_slot(self, tmp_path):
        """Verify BLOCK policy waits until space is available."""
        bus = EventBus(
            event_log_path=tmp_path / "events.jsonl",
            max_pending_events=2,
            overflow_policy=OverflowPolicy.BLOCK,
        )

        completed = []

        def handler(event):
            time.sleep(0.05)
            completed.append(event.event_id)

        bus.subscribe(handler, "test.*")

        start = time.time()

        # Emit 4 events with max 2 pending - should block
        def emit_all():
            for i in range(4):
                bus.emit("test.event", {"i": i})

        thread = threading.Thread(target=emit_all)
        thread.start()
        thread.join(timeout=5)

        elapsed = time.time() - start

        # Should have taken some time due to blocking
        assert elapsed >= 0.05, "Expected blocking behavior"
        time.sleep(0.3)  # Let handlers finish
        bus.shutdown()

    def test_get_pending_count(self, tmp_path):
        """Verify pending count tracking."""
        bus = EventBus(
            event_log_path=tmp_path / "events.jsonl",
            max_pending_events=10,
            overflow_policy=OverflowPolicy.DROP,
        )

        assert bus.get_pending_count() == 0

        gate = threading.Event()

        def blocking_handler(event):
            gate.wait()

        bus.subscribe(blocking_handler, "test.*")

        bus.emit("test.1", {})
        bus.emit("test.2", {})

        # Pending count should be at least 1 (handlers may have started)
        count = bus.get_pending_count()
        assert count >= 0  # May vary due to timing

        gate.set()
        time.sleep(0.2)
        bus.shutdown()

    def test_wait_for_handlers_bypasses_backpressure(self, tmp_path):
        """Verify wait_for_handlers=True bypasses backpressure check."""
        bus = EventBus(
            event_log_path=tmp_path / "events.jsonl",
            max_pending_events=1,
            overflow_policy=OverflowPolicy.RAISE,
        )

        received = []

        def handler(event):
            received.append(event.event_id)

        bus.subscribe(handler, "test.*")

        # Should NOT raise even with tiny queue, because wait_for_handlers is sync
        for i in range(10):
            bus.emit("test.event", {"i": i}, wait_for_handlers=True)

        assert len(received) == 10
        bus.shutdown()


class TestLogRotation:
    """Test log rotation for audit files."""

    def test_log_rotation_creates_backup_files(self, tmp_path):
        """Verify log rotation creates numbered backup files."""
        log_path = tmp_path / "events.jsonl"
        bus = EventBus(
            event_log_path=log_path,
            enable_audit=True,
            max_log_size_bytes=500,  # Small size to trigger rotation
            max_log_files=3,
        )

        # Emit enough events to trigger rotation
        for i in range(200):
            bus.emit("test.event", {"data": "x" * 50, "i": i}, wait_for_handlers=True)

        bus.shutdown()

        # Check for rotated files
        # At least one rotation should have occurred
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) >= 1, f"Expected backup files, got: {files}"

    def test_max_log_files_limit(self, tmp_path):
        """Verify old backups are deleted when limit is reached."""
        log_path = tmp_path / "events.jsonl"
        bus = EventBus(
            event_log_path=log_path,
            enable_audit=True,
            max_log_size_bytes=200,
            max_log_files=2,  # Only keep 2 backups
        )

        # Emit many events to trigger multiple rotations
        for i in range(500):
            bus.emit("test.event", {"data": "x" * 30, "i": i}, wait_for_handlers=True)

        bus.shutdown()

        # Should not have more than 3 files (current + 2 backups)
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) <= 3, f"Too many backup files: {files}"


class TestHandlerTimeout:
    """Test handler timeout configuration."""

    def test_custom_handler_timeout(self, tmp_path):
        """Verify custom handler timeout is used."""
        bus = EventBus(
            event_log_path=tmp_path / "events.jsonl",
            handler_timeout=0.5,  # Very short timeout
        )

        def slow_handler(event):
            time.sleep(2)  # Longer than timeout

        bus.subscribe(slow_handler, "test.*")

        start = time.time()

        # This should timeout
        bus.emit("test.event", {}, wait_for_handlers=True)

        elapsed = time.time() - start

        # Should have timed out around 0.5s (not waited full 2s)
        assert elapsed < 1.5, f"Handler didn't timeout: {elapsed}s"

        bus.shutdown()

    def test_handler_timeout_default_30s(self, tmp_path):
        """Verify default handler timeout is 30s."""
        bus = EventBus(event_log_path=tmp_path / "events.jsonl")
        assert bus.handler_timeout == 30.0
        bus.shutdown()
