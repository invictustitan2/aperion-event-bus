"""
Phase 0 Stability Tests for Event Bus.

These tests validate:
- Memory stability under load
- No resource leaks (subscriptions, tasks, handlers)
- Handler error isolation
- Concurrent emission safety
- Long-running operation stability

Run with: pytest tests/stability/ -v --tb=short
"""

import asyncio
import gc
import time
import weakref

import pytest

from aperion_event_bus import Event, EventBus


class TestMemoryStability:
    """Tests for memory leak prevention."""

    def test_subscription_cleanup_prevents_leak(self) -> None:
        """Verify that unsubscribed handlers don't leak memory."""
        bus = EventBus(enable_audit=False)

        # Create handlers with weak references to track garbage collection
        collected: list[bool] = []
        refs: list[weakref.ref] = []  # Must keep weakrefs alive for callbacks to fire

        for idx in range(100):
            # Create a handler with trackable lifecycle
            class Handler:
                def __init__(self, index: int):
                    self.index = index

                def __call__(self, event: Event) -> None:
                    pass

            handler = Handler(idx)
            refs.append(weakref.ref(handler, lambda ref: collected.append(True)))

            sub_id = bus.subscribe(handler, f"test.event.{idx}")
            bus.unsubscribe(sub_id)

            # Remove local reference
            del handler

        # Force garbage collection (multiple passes for generational GC)
        for _ in range(3):
            gc.collect()

        # All handlers should be garbage collected
        assert len(collected) == 100, f"Only {len(collected)}/100 handlers were collected"
        assert len(bus.subscriptions) == 0

        bus.shutdown()

    def test_event_emission_no_memory_growth(self) -> None:
        """Verify memory doesn't grow unbounded during emission."""
        bus = EventBus(enable_audit=False)

        received_count = 0

        def handler(event: Event) -> None:
            nonlocal received_count
            received_count += 1

        bus.subscribe(handler, "test.*")

        # Get baseline memory
        gc.collect()
        baseline_objects = len(gc.get_objects())

        # Emit many events
        for i in range(10000):
            bus.emit("test.event", {"index": i}, wait_for_handlers=True)

        # Force cleanup
        gc.collect()
        final_objects = len(gc.get_objects())

        # Allow some growth but not proportional to event count
        growth = final_objects - baseline_objects
        assert growth < 1000, f"Memory grew by {growth} objects (expected < 1000)"
        assert received_count == 10000

        bus.shutdown()

    def test_audit_log_memory_bounded(self) -> None:
        """Verify in-memory audit log doesn't grow unbounded."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            bus = EventBus(enable_audit=True, event_log_path=log_path)

            # Emit many events
            for i in range(1000):
                bus.emit("test.event", {"index": i}, wait_for_handlers=True)

            # Check file was written (events are on disk, not memory)
            assert log_path.exists()
            line_count = sum(1 for _ in log_path.open())
            assert line_count == 1000

            bus.shutdown()


class TestConcurrencyStability:
    """Tests for thread-safety and concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_emission_thread_safe(self) -> None:
        """Verify concurrent emissions don't cause race conditions."""
        bus = EventBus(enable_audit=False, max_concurrent_handlers=20)

        received: list[int] = []
        lock = asyncio.Lock()

        async def handler(event: Event) -> None:
            async with lock:
                received.append(event.payload["index"])

        bus.subscribe(handler, "test.*")

        # Emit concurrently from multiple tasks
        async def emit_batch(start: int, count: int) -> None:
            for i in range(start, start + count):
                bus.emit("test.event", {"index": i})
                await asyncio.sleep(0)  # Yield to other tasks

        tasks = [
            asyncio.create_task(emit_batch(i * 100, 100))
            for i in range(10)
        ]
        await asyncio.gather(*tasks)

        # Wait for handlers to complete
        await asyncio.sleep(0.5)

        # All events should be received (order doesn't matter)
        assert len(received) == 1000
        assert set(received) == set(range(1000))

        bus.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_unsubscribe_safe(self) -> None:
        """Verify concurrent subscription changes don't corrupt state."""
        bus = EventBus(enable_audit=False)

        errors: list[Exception] = []

        async def subscribe_unsubscribe_loop(task_id: int) -> None:
            try:
                for _ in range(100):
                    def handler(event: Event) -> None:
                        pass

                    sub_id = bus.subscribe(handler, f"task{task_id}.event")
                    await asyncio.sleep(0)
                    bus.unsubscribe(sub_id)
            except Exception as e:
                errors.append(e)

        tasks = [
            asyncio.create_task(subscribe_unsubscribe_loop(i))
            for i in range(10)
        ]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Errors during concurrent access: {errors}"
        bus.shutdown()


class TestHandlerIsolation:
    """Tests for handler error isolation."""

    def test_failing_handler_doesnt_crash_bus(self) -> None:
        """Verify one failing handler doesn't affect others."""
        bus = EventBus(enable_audit=False)

        results: list[str] = []

        def handler_good_1(event: Event) -> None:
            results.append("good_1")

        def handler_bad(event: Event) -> None:
            raise RuntimeError("Intentional failure")

        def handler_good_2(event: Event) -> None:
            results.append("good_2")

        # Subscribe in priority order (higher runs first)
        bus.subscribe(handler_good_1, "test.*", priority=300)
        bus.subscribe(handler_bad, "test.*", priority=200)
        bus.subscribe(handler_good_2, "test.*", priority=100)

        # Emit and wait
        bus.emit("test.event", {}, wait_for_handlers=True)

        # Both good handlers should have run
        assert "good_1" in results
        assert "good_2" in results

        bus.shutdown()

    def test_slow_handler_doesnt_block_others(self) -> None:
        """Verify slow handlers don't block faster ones."""
        bus = EventBus(enable_audit=False, max_concurrent_handlers=5)

        results: list[tuple[str, float]] = []
        start_time = time.time()

        def fast_handler(event: Event) -> None:
            results.append(("fast", time.time() - start_time))

        def slow_handler(event: Event) -> None:
            time.sleep(0.5)
            results.append(("slow", time.time() - start_time))

        bus.subscribe(fast_handler, "test.*", priority=100)
        bus.subscribe(slow_handler, "test.*", priority=200)

        # Emit without waiting (async dispatch)
        bus.emit("test.event", {})

        # Wait for all handlers
        time.sleep(1.0)

        # Both should complete
        handlers = [r[0] for r in results]
        assert "fast" in handlers
        assert "slow" in handlers

        bus.shutdown()


class TestLoadStability:
    """High-load stress tests."""

    def test_high_throughput_emission(self) -> None:
        """Test sustained high-throughput event emission."""
        bus = EventBus(enable_audit=False, max_concurrent_handlers=20)

        event_count = 0

        def counter(event: Event) -> None:
            nonlocal event_count
            event_count += 1

        bus.subscribe(counter, "load.*")

        # Emit 10,000 events as fast as possible
        start = time.time()
        for i in range(10000):
            bus.emit("load.test", {"i": i}, wait_for_handlers=True)
        duration = time.time() - start

        assert event_count == 10000
        print(f"\nThroughput: {10000 / duration:.0f} events/sec")

        bus.shutdown()

    @pytest.mark.asyncio
    async def test_burst_emission_stability(self) -> None:
        """Test handling of burst traffic patterns."""
        bus = EventBus(enable_audit=False, max_concurrent_handlers=50)

        received = 0

        async def handler(event: Event) -> None:
            nonlocal received
            received += 1
            await asyncio.sleep(0.001)  # Simulate some work

        bus.subscribe(handler, "burst.*")

        # Burst: 1000 events in rapid succession
        for i in range(1000):
            bus.emit("burst.event", {"i": i})

        # Wait for processing
        await asyncio.sleep(2.0)

        assert received == 1000, f"Only received {received}/1000 events"

        bus.shutdown()

    def test_many_subscriptions_performance(self) -> None:
        """Test performance with many active subscriptions."""
        bus = EventBus(enable_audit=False)

        handlers_called: set[int] = set()

        # Create 1000 subscriptions
        for i in range(1000):
            def make_handler(idx: int):
                def handler(event: Event) -> None:
                    handlers_called.add(idx)
                return handler

            bus.subscribe(make_handler(i), f"perf.event.{i % 10}")

        # Emit to subset
        start = time.time()
        for idx in range(100):
            bus.emit(f"perf.event.{idx % 10}", {"idx": idx}, wait_for_handlers=True)
        duration = time.time() - start

        # Should complete in reasonable time
        assert duration < 5.0, f"Took too long: {duration}s"

        bus.shutdown()


class TestResourceCleanup:
    """Tests for proper resource cleanup."""

    def test_shutdown_cleans_resources(self) -> None:
        """Verify shutdown properly releases resources."""
        bus = EventBus(enable_audit=False)

        def handler(event: Event) -> None:
            pass

        for idx in range(100):
            bus.subscribe(handler, f"test.{idx}")

        bus.shutdown()

        # All subscriptions should be inactive
        for sub in bus.subscriptions:
            assert not sub.active

        # Executor should be shutdown
        assert bus.executor._shutdown

    def test_no_orphaned_tasks_after_emission(self) -> None:
        """Verify async tasks are properly tracked and completed."""
        bus = EventBus(enable_audit=False)

        async def async_handler(event: Event) -> None:
            await asyncio.sleep(0.01)

        bus.subscribe(async_handler, "test.*")

        # Get current task count
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run_test() -> int:
            initial_tasks = len(asyncio.all_tasks())

            for idx in range(100):
                bus.emit("test.event", {"idx": idx})

            # Wait for all handlers
            await asyncio.sleep(0.5)

            final_tasks = len(asyncio.all_tasks())
            return final_tasks - initial_tasks

        task_growth = loop.run_until_complete(run_test())
        loop.close()

        # Should not have accumulated tasks
        assert task_growth <= 1, f"Task count grew by {task_growth}"

        bus.shutdown()


class TestLongRunning:
    """Extended stability tests (run with pytest -v --timeout=0)."""

    @pytest.mark.slow
    def test_extended_operation_stability(self) -> None:
        """Test stability over extended operation (60 seconds)."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            bus = EventBus(enable_audit=True, event_log_path=log_path)

            event_count = 0
            error_count = 0

            def handler(event: Event) -> None:
                nonlocal event_count
                event_count += 1

            def error_handler(event: Event) -> None:
                nonlocal error_count
                if event.event_type == "system.handler_error":
                    error_count += 1

            bus.subscribe(handler, "test.*")
            bus.subscribe(error_handler, "system.handler_error")

            start = time.time()
            duration = 60  # 60 seconds

            iteration = 0
            while time.time() - start < duration:
                bus.emit("test.event", {"iteration": iteration})
                iteration += 1
                time.sleep(0.01)  # ~100 events/sec

            # Final sync
            time.sleep(1.0)

            print("\n60-second test results:")
            print(f"  Events emitted: {iteration}")
            print(f"  Events received: {event_count}")
            print(f"  Errors: {error_count}")
            print(f"  Rate: {iteration / duration:.0f} events/sec")

            assert event_count >= iteration * 0.99, "Lost too many events"
            assert error_count == 0, f"Unexpected errors: {error_count}"

            bus.shutdown()
