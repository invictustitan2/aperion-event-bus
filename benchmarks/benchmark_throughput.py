#!/usr/bin/env python3
"""
EventBus Benchmarks

Run with: python benchmarks/benchmark_throughput.py

Measures:
- Event emission throughput (events/sec)
- Handler dispatch latency
- Impact of audit logging
- Backpressure overhead
"""

import asyncio
import gc
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aperion_event_bus import (
    DeadLetterQueue,
    Event,
    EventBus,
    InMemoryMetrics,
    MetricsCollector,
    OverflowPolicy,
)


@dataclass
class BenchmarkResult:
    """Result of a benchmark run."""

    name: str
    iterations: int
    total_time: float
    events_per_second: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float


def percentile(data: list[float], p: float) -> float:
    """Calculate percentile."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def benchmark_emit_throughput(
    num_events: int = 10000,
    with_audit: bool = False,
    with_handlers: bool = True,
    wait_for_handlers: bool = False,
) -> BenchmarkResult:
    """
    Benchmark raw event emission throughput.

    Args:
        num_events: Number of events to emit
        with_audit: Enable audit logging
        with_handlers: Subscribe handlers
        wait_for_handlers: Wait for handler completion
    """
    name = f"emit_throughput(audit={with_audit}, handlers={with_handlers}, wait={wait_for_handlers})"

    with tempfile.TemporaryDirectory() as tmp:
        bus = EventBus(
            enable_audit=with_audit,
            event_log_path=Path(tmp) / "events.jsonl" if with_audit else None,
        )

        handler_count = 0

        def simple_handler(event: Event) -> None:
            nonlocal handler_count
            handler_count += 1

        if with_handlers:
            bus.subscribe(simple_handler, "benchmark.*")

        # Warm up
        for _ in range(100):
            bus.emit("benchmark.warmup", {"i": 0}, wait_for_handlers=wait_for_handlers)

        gc.collect()
        latencies: list[float] = []

        start = time.perf_counter()
        for i in range(num_events):
            emit_start = time.perf_counter()
            bus.emit(
                "benchmark.event",
                {"iteration": i},
                wait_for_handlers=wait_for_handlers,
            )
            latencies.append((time.perf_counter() - emit_start) * 1000)

        total_time = time.perf_counter() - start

        bus.shutdown()

    events_per_second = num_events / total_time

    return BenchmarkResult(
        name=name,
        iterations=num_events,
        total_time=total_time,
        events_per_second=events_per_second,
        avg_latency_ms=statistics.mean(latencies),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        p99_latency_ms=percentile(latencies, 99),
    )


def benchmark_handler_dispatch(
    num_events: int = 5000,
    num_handlers: int = 5,
) -> BenchmarkResult:
    """Benchmark handler dispatch with multiple handlers."""
    name = f"handler_dispatch(handlers={num_handlers})"

    bus = EventBus(enable_audit=False)
    handler_calls = 0

    def handler(event: Event) -> None:
        nonlocal handler_calls
        handler_calls += 1

    for i in range(num_handlers):
        bus.subscribe(handler, "benchmark.*", priority=100 - i)

    # Warm up
    for _ in range(50):
        bus.emit("benchmark.warmup", {}, wait_for_handlers=True)

    gc.collect()
    latencies: list[float] = []

    start = time.perf_counter()
    for i in range(num_events):
        emit_start = time.perf_counter()
        bus.emit("benchmark.event", {"i": i}, wait_for_handlers=True)
        latencies.append((time.perf_counter() - emit_start) * 1000)

    total_time = time.perf_counter() - start
    bus.shutdown()

    return BenchmarkResult(
        name=name,
        iterations=num_events,
        total_time=total_time,
        events_per_second=num_events / total_time,
        avg_latency_ms=statistics.mean(latencies),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        p99_latency_ms=percentile(latencies, 99),
    )


def benchmark_pattern_matching(num_events: int = 5000) -> BenchmarkResult:
    """Benchmark pattern matching overhead."""
    name = "pattern_matching"

    bus = EventBus(enable_audit=False)

    # Subscribe with various patterns
    def handler(event: Event) -> None:
        pass

    bus.subscribe(handler, "user.*")
    bus.subscribe(handler, "chat.*")
    bus.subscribe(handler, "system.*")
    bus.subscribe(handler, "api.request.*")
    bus.subscribe(handler, "*")  # Catch-all

    event_types = [
        "user.login",
        "user.logout",
        "chat.message",
        "system.startup",
        "api.request.get",
        "other.event",
    ]

    gc.collect()
    latencies: list[float] = []

    start = time.perf_counter()
    for i in range(num_events):
        event_type = event_types[i % len(event_types)]
        emit_start = time.perf_counter()
        bus.emit(event_type, {"i": i}, wait_for_handlers=True)
        latencies.append((time.perf_counter() - emit_start) * 1000)

    total_time = time.perf_counter() - start
    bus.shutdown()

    return BenchmarkResult(
        name=name,
        iterations=num_events,
        total_time=total_time,
        events_per_second=num_events / total_time,
        avg_latency_ms=statistics.mean(latencies),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        p99_latency_ms=percentile(latencies, 99),
    )


def benchmark_with_metrics(num_events: int = 5000) -> BenchmarkResult:
    """Benchmark with metrics collection enabled."""
    name = "with_metrics"

    backend = InMemoryMetrics()
    metrics = MetricsCollector(backend)
    bus = EventBus(enable_audit=False, metrics=metrics)

    def handler(event: Event) -> None:
        pass

    bus.subscribe(handler, "benchmark.*")

    gc.collect()
    latencies: list[float] = []

    start = time.perf_counter()
    for i in range(num_events):
        emit_start = time.perf_counter()
        bus.emit("benchmark.event", {"i": i}, wait_for_handlers=True)
        latencies.append((time.perf_counter() - emit_start) * 1000)

    total_time = time.perf_counter() - start
    bus.shutdown()

    return BenchmarkResult(
        name=name,
        iterations=num_events,
        total_time=total_time,
        events_per_second=num_events / total_time,
        avg_latency_ms=statistics.mean(latencies),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        p99_latency_ms=percentile(latencies, 99),
    )


def benchmark_with_dlq(num_events: int = 5000, failure_rate: float = 0.1) -> BenchmarkResult:
    """Benchmark with DLQ and some failing handlers."""
    name = f"with_dlq(failure_rate={failure_rate})"

    dlq = DeadLetterQueue(max_size=1000)
    bus = EventBus(enable_audit=False, dead_letter_queue=dlq)

    def sometimes_fails(event: Event) -> None:
        if event.payload.get("fail"):
            raise ValueError("Simulated failure")

    bus.subscribe(sometimes_fails, "benchmark.*")

    gc.collect()
    latencies: list[float] = []

    start = time.perf_counter()
    for i in range(num_events):
        should_fail = (i % int(1 / failure_rate)) == 0 if failure_rate > 0 else False
        emit_start = time.perf_counter()
        bus.emit("benchmark.event", {"i": i, "fail": should_fail}, wait_for_handlers=True)
        latencies.append((time.perf_counter() - emit_start) * 1000)

    total_time = time.perf_counter() - start
    bus.shutdown()

    return BenchmarkResult(
        name=name,
        iterations=num_events,
        total_time=total_time,
        events_per_second=num_events / total_time,
        avg_latency_ms=statistics.mean(latencies),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        p99_latency_ms=percentile(latencies, 99),
    )


def benchmark_backpressure(num_events: int = 5000) -> BenchmarkResult:
    """Benchmark with backpressure enabled."""
    name = "backpressure"

    bus = EventBus(
        enable_audit=False,
        max_pending_events=100,
        overflow_policy=OverflowPolicy.DROP,
    )

    def handler(event: Event) -> None:
        pass

    bus.subscribe(handler, "benchmark.*")

    gc.collect()
    latencies: list[float] = []

    start = time.perf_counter()
    for i in range(num_events):
        emit_start = time.perf_counter()
        bus.emit("benchmark.event", {"i": i}, wait_for_handlers=True)
        latencies.append((time.perf_counter() - emit_start) * 1000)

    total_time = time.perf_counter() - start
    bus.shutdown()

    return BenchmarkResult(
        name=name,
        iterations=num_events,
        total_time=total_time,
        events_per_second=num_events / total_time,
        avg_latency_ms=statistics.mean(latencies),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        p99_latency_ms=percentile(latencies, 99),
    )


def print_result(result: BenchmarkResult) -> None:
    """Print benchmark result."""
    status = "✅" if result.events_per_second >= 10000 else "⚠️"
    print(f"\n{status} {result.name}")
    print(f"   Events/sec: {result.events_per_second:,.0f}")
    print(f"   Total time: {result.total_time:.3f}s for {result.iterations:,} events")
    print(f"   Latency (ms): avg={result.avg_latency_ms:.3f}, "
          f"p50={result.p50_latency_ms:.3f}, "
          f"p95={result.p95_latency_ms:.3f}, "
          f"p99={result.p99_latency_ms:.3f}")


def main():
    """Run all benchmarks."""
    print("=" * 60)
    print("EventBus Benchmarks")
    print("=" * 60)
    print(f"Target: 10,000 events/sec")

    results: list[BenchmarkResult] = []

    # Core throughput benchmarks
    print("\n--- Emission Throughput ---")
    results.append(benchmark_emit_throughput(with_audit=False, with_handlers=False))
    print_result(results[-1])

    results.append(benchmark_emit_throughput(with_audit=False, with_handlers=True, wait_for_handlers=True))
    print_result(results[-1])

    results.append(benchmark_emit_throughput(with_audit=True, with_handlers=True, wait_for_handlers=True))
    print_result(results[-1])

    # Handler dispatch
    print("\n--- Handler Dispatch ---")
    results.append(benchmark_handler_dispatch(num_handlers=1))
    print_result(results[-1])

    results.append(benchmark_handler_dispatch(num_handlers=5))
    print_result(results[-1])

    results.append(benchmark_handler_dispatch(num_handlers=10))
    print_result(results[-1])

    # Pattern matching
    print("\n--- Pattern Matching ---")
    results.append(benchmark_pattern_matching())
    print_result(results[-1])

    # With features enabled
    print("\n--- With Features ---")
    results.append(benchmark_with_metrics())
    print_result(results[-1])

    results.append(benchmark_with_dlq())
    print_result(results[-1])

    results.append(benchmark_backpressure())
    print_result(results[-1])

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    passing = sum(1 for r in results if r.events_per_second >= 10000)
    total = len(results)
    print(f"\nPassing 10k/sec target: {passing}/{total}")

    if passing == total:
        print("\n✅ All benchmarks meet the 10,000 events/sec target!")
        return 0
    else:
        print(f"\n⚠️ {total - passing} benchmarks below target")
        return 1


if __name__ == "__main__":
    sys.exit(main())
