"""
Metrics collection for the Event Bus.

Provides hooks for emitting metrics about event bus operations.
Supports multiple backends: callback-based, Prometheus, StatsD.

Usage:
    from aperion_event_bus import EventBus
    from aperion_event_bus.metrics import MetricsCollector, PrometheusMetrics

    # Callback-based metrics
    def on_metric(name, value, tags):
        print(f"{name}={value} tags={tags}")

    collector = MetricsCollector(callback=on_metric)
    bus = EventBus(metrics=collector)

    # Or with Prometheus (if prometheus_client installed)
    collector = PrometheusMetrics(prefix="event_bus")
    bus = EventBus(metrics=collector)
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MetricTags:
    """Common tags for metrics."""

    event_type: str | None = None
    source: str | None = None
    handler_name: str | None = None
    status: str | None = None  # "success", "error", "timeout", "dropped"

    def to_dict(self) -> dict[str, str]:
        """Convert to dict, excluding None values."""
        return {k: v for k, v in vars(self).items() if v is not None}


class MetricsBackend(ABC):
    """Abstract base class for metrics backends."""

    @abstractmethod
    def increment(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        """Increment a counter."""
        pass

    @abstractmethod
    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Set a gauge value."""
        pass

    @abstractmethod
    def histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a histogram value."""
        pass

    @abstractmethod
    def timing(self, name: str, value_ms: float, tags: dict[str, str] | None = None) -> None:
        """Record a timing in milliseconds."""
        pass


class NoopMetrics(MetricsBackend):
    """No-op metrics backend (default when metrics disabled)."""

    def increment(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        pass

    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        pass

    def histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        pass

    def timing(self, name: str, value_ms: float, tags: dict[str, str] | None = None) -> None:
        pass


class CallbackMetrics(MetricsBackend):
    """
    Callback-based metrics backend.

    Useful for integrating with custom metrics systems or testing.

    Usage:
        def my_callback(metric_type, name, value, tags):
            print(f"{metric_type}: {name}={value}")

        metrics = CallbackMetrics(callback=my_callback)
    """

    def __init__(
        self,
        callback: Callable[[str, str, float, dict[str, str] | None], None],
    ):
        """
        Initialize callback metrics.

        Args:
            callback: Function called for each metric.
                      Signature: (metric_type, name, value, tags) -> None
        """
        self.callback = callback

    def increment(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        self.callback("counter", name, float(value), tags)

    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        self.callback("gauge", name, value, tags)

    def histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        self.callback("histogram", name, value, tags)

    def timing(self, name: str, value_ms: float, tags: dict[str, str] | None = None) -> None:
        self.callback("timing", name, value_ms, tags)


@dataclass
class InMemoryMetrics(MetricsBackend):
    """
    In-memory metrics backend for testing and debugging.

    Stores all metrics in memory for later inspection.
    """

    counters: dict[str, int] = field(default_factory=dict)
    gauges: dict[str, float] = field(default_factory=dict)
    histograms: dict[str, list[float]] = field(default_factory=dict)
    timings: dict[str, list[float]] = field(default_factory=dict)

    def _key(self, name: str, tags: dict[str, str] | None) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"

    def increment(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        key = self._key(name, tags)
        self.counters[key] = self.counters.get(key, 0) + value

    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        key = self._key(name, tags)
        self.gauges[key] = value

    def histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        key = self._key(name, tags)
        if key not in self.histograms:
            self.histograms[key] = []
        self.histograms[key].append(value)

    def timing(self, name: str, value_ms: float, tags: dict[str, str] | None = None) -> None:
        key = self._key(name, tags)
        if key not in self.timings:
            self.timings[key] = []
        self.timings[key].append(value_ms)

    def reset(self) -> None:
        """Clear all stored metrics."""
        self.counters.clear()
        self.gauges.clear()
        self.histograms.clear()
        self.timings.clear()

    def get_counter(self, name: str, tags: dict[str, str] | None = None) -> int:
        """Get counter value."""
        return self.counters.get(self._key(name, tags), 0)

    def get_gauge(self, name: str, tags: dict[str, str] | None = None) -> float | None:
        """Get gauge value."""
        return self.gauges.get(self._key(name, tags))

    def get_histogram_values(self, name: str, tags: dict[str, str] | None = None) -> list[float]:
        """Get histogram values."""
        return self.histograms.get(self._key(name, tags), [])

    def get_timing_values(self, name: str, tags: dict[str, str] | None = None) -> list[float]:
        """Get timing values."""
        return self.timings.get(self._key(name, tags), [])


class MetricsCollector:
    """
    Main metrics collector for the Event Bus.

    Wraps a backend and provides named metrics for event bus operations.
    """

    # Metric names
    EVENTS_EMITTED = "events_emitted_total"
    EVENTS_DROPPED = "events_dropped_total"
    HANDLER_EXECUTIONS = "handler_executions_total"
    HANDLER_ERRORS = "handler_errors_total"
    HANDLER_TIMEOUTS = "handler_timeouts_total"
    EMIT_LATENCY = "emit_latency_ms"
    HANDLER_LATENCY = "handler_latency_ms"
    PENDING_EVENTS = "pending_events"
    SUBSCRIPTIONS = "subscriptions"

    def __init__(
        self,
        backend: MetricsBackend | None = None,
        prefix: str = "event_bus",
    ):
        """
        Initialize metrics collector.

        Args:
            backend: Metrics backend (defaults to NoopMetrics)
            prefix: Prefix for all metric names
        """
        self.backend = backend or NoopMetrics()
        self.prefix = prefix

    def _name(self, metric: str) -> str:
        """Get prefixed metric name."""
        return f"{self.prefix}_{metric}" if self.prefix else metric

    def record_event_emitted(
        self,
        event_type: str,
        latency_seconds: float,
        source: str | None = None,
    ) -> None:
        """Record an event emission (convenience method for EventBus)."""
        tags = MetricTags(event_type=event_type, source=source).to_dict()
        self.backend.increment(self._name(self.EVENTS_EMITTED), tags=tags)
        self.backend.timing(self._name(self.EMIT_LATENCY), latency_seconds * 1000, tags=tags)

    def record_event_dropped(self, event_type: str, source: str | None = None) -> None:
        """Record a dropped event (convenience method for EventBus)."""
        tags = MetricTags(event_type=event_type, source=source).to_dict()
        self.backend.increment(self._name(self.EVENTS_DROPPED), tags=tags)

    def record_handler_execution(
        self,
        event_type: str,
        handler_name: str,
        latency_seconds: float,
        success: bool = True,
    ) -> None:
        """Record a handler execution (convenience method for EventBus)."""
        status = "success" if success else "error"
        tags = MetricTags(
            event_type=event_type,
            handler_name=handler_name,
            status=status,
        ).to_dict()

        self.backend.increment(self._name(self.HANDLER_EXECUTIONS), tags=tags)
        self.backend.timing(
            self._name(self.HANDLER_LATENCY), latency_seconds * 1000, tags=tags
        )

        if not success:
            self.backend.increment(self._name(self.HANDLER_ERRORS), tags=tags)

    def record_emit(
        self,
        event_type: str,
        source: str | None,
        latency_ms: float,
        dropped: bool = False,
    ) -> None:
        """Record an event emission (full API)."""
        tags = MetricTags(event_type=event_type, source=source).to_dict()

        if dropped:
            self.backend.increment(self._name(self.EVENTS_DROPPED), tags=tags)
        else:
            self.backend.increment(self._name(self.EVENTS_EMITTED), tags=tags)
            self.backend.timing(self._name(self.EMIT_LATENCY), latency_ms, tags=tags)

    def update_pending_count(self, count: int) -> None:
        """Update pending events gauge."""
        self.backend.gauge(self._name(self.PENDING_EVENTS), float(count))

    def update_subscription_count(self, count: int) -> None:
        """Update subscriptions gauge."""
        self.backend.gauge(self._name(self.SUBSCRIPTIONS), float(count))

    def get_snapshot(self) -> dict[str, Any]:
        """
        Get a snapshot of current metrics.

        Works best with InMemoryMetrics backend.

        Returns:
            Dictionary with metrics summary
        """
        if not isinstance(self.backend, InMemoryMetrics):
            return {"backend": type(self.backend).__name__}

        backend = self.backend

        # Parse counters to build summary
        events_emitted: dict[str, int] = {}
        events_dropped: dict[str, int] = {}
        handler_stats: dict[str, dict[str, Any]] = {}

        # Process counters
        emitted_prefix = self._name(self.EVENTS_EMITTED)
        dropped_prefix = self._name(self.EVENTS_DROPPED)
        handler_prefix = self._name(self.HANDLER_EXECUTIONS)

        for key, value in backend.counters.items():
            if key.startswith(emitted_prefix):
                # Extract event type from tags
                event_type = self._extract_tag(key, "event_type")
                if event_type:
                    events_emitted[event_type] = value
            elif key.startswith(dropped_prefix):
                event_type = self._extract_tag(key, "event_type")
                if event_type:
                    events_dropped[event_type] = value
            elif key.startswith(handler_prefix):
                handler_name = self._extract_tag(key, "handler_name")
                status = self._extract_tag(key, "status")
                if handler_name:
                    if handler_name not in handler_stats:
                        handler_stats[handler_name] = {
                            "total_calls": 0,
                            "success_count": 0,
                            "failure_count": 0,
                            "latencies": [],
                        }
                    handler_stats[handler_name]["total_calls"] += value
                    if status == "success":
                        handler_stats[handler_name]["success_count"] += value
                    elif status == "error":
                        handler_stats[handler_name]["failure_count"] += value

        # Process latencies
        latency_prefix = self._name(self.HANDLER_LATENCY)
        for key, values in backend.timings.items():
            if key.startswith(latency_prefix):
                handler_name = self._extract_tag(key, "handler_name")
                if handler_name and handler_name in handler_stats:
                    # Convert ms back to seconds for consistency
                    handler_stats[handler_name]["latencies"].extend(
                        v / 1000 for v in values
                    )

        return {
            "events_emitted": events_emitted,
            "events_dropped": events_dropped,
            "total_events_emitted": sum(events_emitted.values()),
            "total_events_dropped": sum(events_dropped.values()),
            "handler_stats": handler_stats,
        }

    def _extract_tag(self, key: str, tag_name: str) -> str | None:
        """Extract a tag value from a metric key."""
        # Keys look like: prefix_metric{tag1=val1,tag2=val2}
        if "{" not in key:
            return None
        tag_part = key.split("{", 1)[1].rstrip("}")
        for pair in tag_part.split(","):
            if "=" in pair:
                name, value = pair.split("=", 1)
                if name == tag_name:
                    return value
        return None


class TimingContext:
    """Context manager for timing operations."""

    def __init__(self):
        self.start_time: float = 0
        self.elapsed_ms: float = 0

    def __enter__(self) -> TimingContext:
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000


# Optional Prometheus integration
try:
    from prometheus_client import Counter, Gauge, Histogram

    class PrometheusMetrics(MetricsBackend):
        """
        Prometheus metrics backend.

        Requires prometheus_client to be installed.
        """

        def __init__(self, prefix: str = "event_bus"):
            self.prefix = prefix
            self._counters: dict[str, Counter] = {}
            self._gauges: dict[str, Gauge] = {}
            self._histograms: dict[str, Histogram] = {}

        def _get_counter(self, name: str, labels: list[str]) -> Counter:
            key = f"{self.prefix}_{name}"
            if key not in self._counters:
                self._counters[key] = Counter(key, f"{name} counter", labels)
            return self._counters[key]

        def _get_gauge(self, name: str, labels: list[str]) -> Gauge:
            key = f"{self.prefix}_{name}"
            if key not in self._gauges:
                self._gauges[key] = Gauge(key, f"{name} gauge", labels)
            return self._gauges[key]

        def _get_histogram(self, name: str, labels: list[str]) -> Histogram:
            key = f"{self.prefix}_{name}"
            if key not in self._histograms:
                self._histograms[key] = Histogram(key, f"{name} histogram", labels)
            return self._histograms[key]

        def increment(
            self, name: str, value: int = 1, tags: dict[str, str] | None = None
        ) -> None:
            labels = list(tags.keys()) if tags else []
            counter = self._get_counter(name, labels)
            if tags:
                counter.labels(**tags).inc(value)
            else:
                counter.inc(value)

        def gauge(
            self, name: str, value: float, tags: dict[str, str] | None = None
        ) -> None:
            labels = list(tags.keys()) if tags else []
            gauge = self._get_gauge(name, labels)
            if tags:
                gauge.labels(**tags).set(value)
            else:
                gauge.set(value)

        def histogram(
            self, name: str, value: float, tags: dict[str, str] | None = None
        ) -> None:
            labels = list(tags.keys()) if tags else []
            hist = self._get_histogram(name, labels)
            if tags:
                hist.labels(**tags).observe(value)
            else:
                hist.observe(value)

        def timing(
            self, name: str, value_ms: float, tags: dict[str, str] | None = None
        ) -> None:
            # Convert to seconds for Prometheus conventions
            self.histogram(name, value_ms / 1000, tags)

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    PrometheusMetrics = None  # type: ignore


def is_prometheus_available() -> bool:
    """Check if Prometheus client is available."""
    return PROMETHEUS_AVAILABLE
