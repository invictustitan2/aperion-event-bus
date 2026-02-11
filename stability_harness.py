#!/usr/bin/env python3
"""
24-Hour Stability Test Harness for Aperion Event Bus.

Runs continuous load testing with memory profiling to validate
production readiness.

Usage:
    # Quick validation (5 minutes)
    python stability_harness.py --duration 300

    # Full 24-hour test
    python stability_harness.py --duration 86400

Requirements:
    pip install psutil
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import random
import string
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from aperion_event_bus import EventBus, Event

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("Warning: psutil not installed. Memory monitoring disabled.")


@dataclass
class StabilityMetrics:
    """Metrics collected during stability test."""

    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    
    # Counters
    events_emitted: int = 0
    events_received: int = 0
    subscriptions_created: int = 0
    subscriptions_removed: int = 0
    
    # Errors
    exceptions: int = 0
    handler_errors: int = 0
    exception_types: dict[str, int] = field(default_factory=dict)
    
    # Memory (MB)
    initial_memory_mb: float = 0.0
    peak_memory_mb: float = 0.0
    final_memory_mb: float = 0.0
    memory_samples: list[tuple[float, float]] = field(default_factory=list)
    
    # Timing
    avg_emit_time_ms: float = 0.0
    max_emit_time_ms: float = 0.0
    emit_times: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("memory_samples", None)
        d.pop("emit_times", None)
        d["duration_seconds"] = (self.end_time or time.time()) - self.start_time
        d["events_per_second"] = self.events_emitted / max(1, d["duration_seconds"])
        return d


class StabilityHarness:
    """
    24-hour stability test harness for Event Bus.

    Tests:
    - High-frequency event emission
    - Multiple concurrent subscribers
    - Pattern matching (exact, wildcard, catch-all)
    - Subscription churn (add/remove)
    - Handler errors (fault tolerance)
    - Memory stability
    """

    def __init__(
        self,
        duration_seconds: int = 300,
        emitters: int = 4,
        subscribers: int = 10,
        events_per_second: int = 500,
        max_memory_mb: float | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self.duration = duration_seconds
        self.num_emitters = emitters
        self.num_subscribers = subscribers
        self.target_eps = events_per_second
        self.max_memory_mb = max_memory_mb
        self.output_dir = output_dir or Path("stability_results")
        
        self.metrics = StabilityMetrics()
        self._running = False
        self._lock = threading.Lock()
        
        # Event bus under test
        self.bus = EventBus(enable_audit=False)  # Disable audit for perf

    def _get_memory_mb(self) -> float:
        if not HAS_PSUTIL:
            return 0.0
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)

    def _record_exception(self, exc: Exception) -> None:
        with self._lock:
            self.metrics.exceptions += 1
            exc_type = type(exc).__name__
            self.metrics.exception_types[exc_type] = (
                self.metrics.exception_types.get(exc_type, 0) + 1
            )

    def _random_string(self, length: int = 8) -> str:
        return "".join(random.choices(string.ascii_lowercase, k=length))

    def _create_handler(self, handler_id: int, error_rate: float = 0.0):
        """Create a handler that optionally raises errors."""
        def handler(event: Event) -> None:
            with self._lock:
                self.metrics.events_received += 1
            
            if error_rate > 0 and random.random() < error_rate:
                with self._lock:
                    self.metrics.handler_errors += 1
                raise ValueError(f"Simulated error in handler {handler_id}")
        
        return handler

    async def _emitter_worker(self, worker_id: int) -> None:
        """Worker that emits events."""
        delay = 1.0 / (self.target_eps / self.num_emitters)
        event_types = [
            "user.created",
            "user.updated",
            "user.deleted",
            "order.placed",
            "order.shipped",
            "payment.received",
            "system.health",
            "audit.action",
        ]
        
        while self._running:
            try:
                start = time.perf_counter()
                
                event_type = random.choice(event_types)
                payload = {
                    "worker_id": worker_id,
                    "timestamp": time.time(),
                    "data": self._random_string(32),
                }
                
                self.bus.emit(
                    event_type,
                    payload,
                    source=f"worker-{worker_id}",
                )
                
                elapsed_ms = (time.perf_counter() - start) * 1000
                
                with self._lock:
                    self.metrics.events_emitted += 1
                    self.metrics.emit_times.append(elapsed_ms)
                    if elapsed_ms > self.metrics.max_emit_time_ms:
                        self.metrics.max_emit_time_ms = elapsed_ms
                
                await asyncio.sleep(delay)
                
            except Exception as e:
                self._record_exception(e)

    async def _subscription_churner(self) -> None:
        """Periodically add and remove subscriptions to test stability."""
        patterns = ["user.*", "order.*", "system.*", "*.created", "_"]
        active_subs: list[tuple[Any, str]] = []
        
        while self._running:
            try:
                # Add a subscription
                if len(active_subs) < 50 or random.random() < 0.6:
                    pattern = random.choice(patterns)
                    handler = self._create_handler(
                        len(active_subs),
                        error_rate=0.01,  # 1% error rate
                    )
                    self.bus.subscribe(handler, pattern)
                    active_subs.append((handler, pattern))
                    with self._lock:
                        self.metrics.subscriptions_created += 1
                
                # Remove a subscription
                elif active_subs and random.random() < 0.4:
                    handler, pattern = active_subs.pop(random.randrange(len(active_subs)))
                    self.bus.unsubscribe(handler, pattern)
                    with self._lock:
                        self.metrics.subscriptions_removed += 1
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                self._record_exception(e)

    async def _memory_monitor(self) -> None:
        """Monitor memory usage."""
        if not HAS_PSUTIL:
            return
            
        self.metrics.initial_memory_mb = self._get_memory_mb()
        self.metrics.peak_memory_mb = self.metrics.initial_memory_mb
        
        while self._running:
            current = self._get_memory_mb()
            timestamp = time.time() - self.metrics.start_time
            
            with self._lock:
                self.metrics.memory_samples.append((timestamp, current))
                if current > self.metrics.peak_memory_mb:
                    self.metrics.peak_memory_mb = current
            
            if self.max_memory_mb and current > self.max_memory_mb:
                print(f"\n❌ MEMORY LIMIT EXCEEDED: {current:.1f}MB > {self.max_memory_mb}MB")
                self._running = False
                return
            
            await asyncio.sleep(5)

    async def _progress_reporter(self) -> None:
        """Report progress."""
        while self._running:
            await asyncio.sleep(10)
            
            elapsed = time.time() - self.metrics.start_time
            remaining = self.duration - elapsed
            
            with self._lock:
                emitted = self.metrics.events_emitted
                received = self.metrics.events_received
                eps = emitted / max(1, elapsed)
                mem = self._get_memory_mb()
                subs = self.bus.subscriber_count if hasattr(self.bus, 'subscriber_count') else '?'
            
            print(
                f"[{elapsed/60:.1f}m] "
                f"emit={emitted:,} recv={received:,} "
                f"eps={eps:.0f} "
                f"mem={mem:.1f}MB "
                f"subs={subs} "
                f"err={self.metrics.exceptions} "
                f"remaining={remaining/60:.1f}m"
            )

    async def run(self) -> StabilityMetrics:
        """Run the stability test."""
        print(f"🚀 Starting {self.duration/3600:.1f}h Event Bus stability test")
        print(f"   Emitters: {self.num_emitters}")
        print(f"   Target EPS: {self.target_eps}")
        if self.max_memory_mb:
            print(f"   Max Memory: {self.max_memory_mb}MB")
        print()
        
        # Set up initial subscribers
        for i in range(self.num_subscribers):
            pattern = ["user.*", "order.*", "system.*", "_"][i % 4]
            self.bus.subscribe(self._create_handler(i), pattern)
            self.metrics.subscriptions_created += 1
        
        self._running = True
        self.metrics.start_time = time.time()
        
        # Start workers
        tasks = [
            *[asyncio.create_task(self._emitter_worker(i)) for i in range(self.num_emitters)],
            asyncio.create_task(self._subscription_churner()),
            asyncio.create_task(self._memory_monitor()),
            asyncio.create_task(self._progress_reporter()),
        ]
        
        try:
            await asyncio.sleep(self.duration)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            
            for task in tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            # Graceful shutdown
            self.bus.shutdown(timeout=5.0)
        
        self.metrics.end_time = time.time()
        self.metrics.final_memory_mb = self._get_memory_mb()
        
        if self.metrics.emit_times:
            self.metrics.avg_emit_time_ms = (
                sum(self.metrics.emit_times) / len(self.metrics.emit_times)
            )
        
        gc.collect()
        return self.metrics

    def save_results(self) -> Path:
        """Save results."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_file = self.output_dir / f"eventbus_stability_{timestamp}.json"
        
        results = {
            "summary": self.metrics.to_dict(),
            "memory_samples": self.metrics.memory_samples[-1000:],
            "config": {
                "duration_seconds": self.duration,
                "emitters": self.num_emitters,
                "subscribers": self.num_subscribers,
                "target_eps": self.target_eps,
            },
        }
        
        with open(result_file, "w") as f:
            json.dump(results, f, indent=2)
        
        return result_file

    def print_summary(self) -> bool:
        """Print summary and return success."""
        m = self.metrics
        duration = (m.end_time or time.time()) - m.start_time
        
        print("\n" + "=" * 60)
        print("EVENT BUS STABILITY TEST RESULTS")
        print("=" * 60)
        
        print(f"\n⏱️  Duration: {duration/3600:.2f} hours ({duration:.0f}s)")
        print(f"📊 Events Emitted: {m.events_emitted:,}")
        print(f"📥 Events Received: {m.events_received:,}")
        print(f"⚡ Average EPS: {m.events_emitted/duration:.1f}")
        
        print(f"\n📡 Subscriptions:")
        print(f"   Created: {m.subscriptions_created:,}")
        print(f"   Removed: {m.subscriptions_removed:,}")
        
        print(f"\n⏱️  Timing:")
        print(f"   Avg Emit: {m.avg_emit_time_ms:.2f}ms")
        print(f"   Max Emit: {m.max_emit_time_ms:.2f}ms")
        
        if HAS_PSUTIL:
            growth = m.final_memory_mb - m.initial_memory_mb
            print(f"\n💾 Memory:")
            print(f"   Initial: {m.initial_memory_mb:.1f}MB")
            print(f"   Peak: {m.peak_memory_mb:.1f}MB")
            print(f"   Final: {m.final_memory_mb:.1f}MB")
            print(f"   Growth: {growth:+.1f}MB")
        
        print(f"\n❌ Errors:")
        print(f"   Exceptions: {m.exceptions}")
        print(f"   Handler Errors: {m.handler_errors} (expected ~1%)")
        
        # Pass/fail
        passed = True
        failures = []
        
        if m.exceptions > m.handler_errors:
            # Only count unexpected exceptions
            unexpected = m.exceptions - m.handler_errors
            if unexpected > 0:
                passed = False
                failures.append(f"{unexpected} unexpected exceptions")
        
        if HAS_PSUTIL and self.max_memory_mb:
            if m.peak_memory_mb > self.max_memory_mb:
                passed = False
                failures.append(f"Memory exceeded {self.max_memory_mb}MB")
        
        if HAS_PSUTIL and m.initial_memory_mb > 0:
            growth_pct = (m.final_memory_mb - m.initial_memory_mb) / m.initial_memory_mb
            if growth_pct > 0.5:
                passed = False
                failures.append(f"Possible memory leak: {growth_pct*100:.0f}% growth")
        
        # Check delivery ratio
        if m.events_emitted > 0:
            delivery_ratio = m.events_received / m.events_emitted
            if delivery_ratio < 0.1:  # At least 10% should be delivered
                passed = False
                failures.append(f"Low delivery ratio: {delivery_ratio*100:.1f}%")
        
        print("\n" + "=" * 60)
        if passed:
            print("✅ EVENT BUS STABILITY TEST PASSED")
        else:
            print("❌ EVENT BUS STABILITY TEST FAILED")
            for f in failures:
                print(f"   - {f}")
        print("=" * 60)
        
        return passed


async def main() -> int:
    parser = argparse.ArgumentParser(description="Event Bus Stability Test")
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--emitters", type=int, default=4)
    parser.add_argument("--subscribers", type=int, default=10)
    parser.add_argument("--eps", type=int, default=500)
    parser.add_argument("--max-memory-mb", type=float, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("stability_results"))
    
    args = parser.parse_args()
    
    harness = StabilityHarness(
        duration_seconds=args.duration,
        emitters=args.emitters,
        subscribers=args.subscribers,
        events_per_second=args.eps,
        max_memory_mb=args.max_memory_mb,
        output_dir=args.output_dir,
    )
    
    try:
        await harness.run()
    except KeyboardInterrupt:
        print("\n⚠️  Test interrupted")
        harness._running = False
    
    result_file = harness.save_results()
    print(f"\n📁 Results: {result_file}")
    
    return 0 if harness.print_summary() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
