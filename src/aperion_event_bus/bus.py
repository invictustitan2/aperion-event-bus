"""
Event Bus - The Nervous System.

An asynchronous event system for distributed component communication.
Enforces Constitution D (Eventing & Observability) and Constitution B2 (PII Safety).

This module provides:
- Event emission and subscription with pattern matching
- Sync and async event handlers with priority ordering
- Event filtering and routing ({domain}.* wildcards)
- JSONL audit trail for compliance
- Correlation ID propagation for distributed tracing (Constitution D1)
- Handler error isolation and auditing

Event Naming Convention (Constitution D2 - REQUIRED):
    All events MUST follow {domain}.{action} format:
    - user.login, user.logout
    - chat.user_input, chat.response
    - system.startup, system.shutdown
    - system.handler_error (internal error events)

Usage:
    from aperion_event_bus import EventBus

    bus = EventBus(enable_audit=True, event_log_path=Path("events.jsonl"))

    # Subscribe to events
    def on_chat(event):
        print(f"Chat: {event.payload}")

    bus.subscribe(on_chat, "chat.*", priority=100)

    # Emit events
    bus.emit("chat.user_input", {"text": "hello"}, correlation_id="req-123")

    # Get statistics
    stats = bus.get_stats()
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .audit import AuditLogger, Redactor
from .correlation import get_correlation_id
from .events import Event
from .validation import EventValidator

if TYPE_CHECKING:
    from .dlq import DeadLetterQueue
    from .metrics import MetricsCollector

# Type alias for event handlers (sync or async)
EventHandler = Callable[[Event], Any] | Callable[[Event], Awaitable[Any]]


class OverflowPolicy(Enum):
    """Policy for handling event overflow when backpressure limit is reached."""

    DROP = "drop"  # Silently drop new events
    DROP_OLDEST = "drop_oldest"  # Drop oldest pending event
    RAISE = "raise"  # Raise BackpressureError
    BLOCK = "block"  # Block until space available (only in sync mode)


class BackpressureError(Exception):
    """Raised when event emission is rejected due to backpressure."""

    pass


@dataclass
class Subscription:
    """
    Event subscription details.

    Attributes:
        handler: Function to call when matching events are emitted
        event_types: Set of event type patterns to match
        priority: Handler priority (higher number = runs first)
        active: Whether subscription is currently active
        metadata: Optional metadata for the subscription
        subscription_id: Unique identifier for this subscription
    """

    handler: EventHandler
    event_types: set[str]
    priority: int = 100
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    subscription_id: str = field(
        default_factory=lambda: f"sub_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    )

    def matches(self, event_type: str) -> bool:
        """
        Check if subscription matches event type.

        Supports:
        - Exact match: "chat.user_input" matches "chat.user_input"
        - Wildcard suffix: "chat.*" matches "chat.user_input", "chat.response"
        - Catch-all: "*" matches everything

        Args:
            event_type: Event type to check

        Returns:
            True if subscription matches the event type
        """
        if not self.active:
            return False

        # Check for exact match or catch-all first
        if event_type in self.event_types or "*" in self.event_types:
            return True

        # Check for pattern matches (e.g., "chat.*" matches "chat.user_input")
        for pattern in self.event_types:
            if pattern.endswith(".*"):
                prefix = pattern[:-2]  # Remove ".*"
                if event_type.startswith(prefix + "."):
                    return True

        return False


class EventBus:
    """
    Asynchronous event bus for component communication.

    The "Nervous System" of the Aperion ecosystem - a unified Telemetry Tower
    that enforces Constitution D (Eventing & Observability).

    Features:
    - Sync and async event handlers with priority ordering
    - Pattern-based subscriptions ({domain}.* wildcards)
    - JSONL audit logging with optional PII redaction
    - Correlation ID propagation (Constitution D1)
    - Handler error isolation and auditing

    Usage:
        from pathlib import Path
        from aperion_event_bus import EventBus

        bus = EventBus(enable_audit=True, event_log_path=Path("events.jsonl"))

        def on_chat(event):
            print(f"Chat event: {event.payload}")

        bus.subscribe(on_chat, "chat.*", priority=100)
        bus.emit("chat.user_input", {"text": "hello"})
    """

    def __init__(
        self,
        event_log_path: Path | None = None,
        max_concurrent_handlers: int = 10,
        max_workers: int | None = None,  # Alias for max_concurrent_handlers
        enable_audit: bool = True,
        redactor: Redactor | None = None,
        # Milestone 1: Production Safety parameters
        handler_timeout: float = 30.0,
        max_pending_events: int | None = None,
        overflow_policy: OverflowPolicy | str = OverflowPolicy.DROP,
        max_log_size_bytes: int | None = None,
        max_log_files: int = 5,
        # Milestone 2: Observability parameters
        metrics: MetricsCollector | None = None,
        validate_events: bool = False,
        validator: EventValidator | None = None,
        # Milestone 3: Security & Compliance
        dead_letter_queue: DeadLetterQueue | None = None,
    ):
        """
        Initialize the Event Bus.

        Args:
            event_log_path: Path for JSONL audit log (optional)
            max_concurrent_handlers: Thread pool size for handlers
            max_workers: Alias for max_concurrent_handlers (for compatibility)
            enable_audit: Whether to enable audit logging
            redactor: Optional Redactor for PII scrubbing in audit logs
            handler_timeout: Timeout in seconds for sync handlers (default: 30)
            max_pending_events: Max queued events for backpressure (None = unlimited)
            overflow_policy: What to do when max_pending_events is reached
            max_log_size_bytes: Max size per log file before rotation (None = no rotation)
            max_log_files: Number of rotated log files to keep (default: 5)
            metrics: Optional MetricsCollector for observability
            validate_events: If True, validate event types follow {domain}.{action}
            validator: Custom EventValidator (uses default if validate_events=True)
            dead_letter_queue: Optional DLQ for failed events
        """
        # Support both parameter names for compatibility
        if max_workers is not None:
            max_concurrent_handlers = max_workers

        # Convert string overflow_policy to enum
        if isinstance(overflow_policy, str):
            overflow_policy = OverflowPolicy(overflow_policy)

        self.event_log_path = event_log_path
        self.max_concurrent_handlers = max_concurrent_handlers
        self.enable_audit = enable_audit
        self.handler_timeout = handler_timeout
        self.max_pending_events = max_pending_events
        self.overflow_policy = overflow_policy

        # Milestone 2: Observability - metrics and validation
        self._metrics = metrics
        self._validate_events = validate_events
        self._validator = validator if validator else EventValidator(strict=validate_events)

        # Milestone 3: Security & Compliance - dead letter queue
        self._dlq = dead_letter_queue

        # Thread safety: lock for subscription list and counter access
        self._subscription_lock = threading.RLock()
        self.subscriptions: list[Subscription] = []

        # Backpressure tracking (Milestone 1, Task 2)
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._pending_condition = threading.Condition(self._pending_lock)

        # Track pending async tasks for graceful shutdown
        self._pending_tasks: set[asyncio.Task[None]] = set()
        self._tasks_lock = threading.Lock()

        self.executor = ThreadPoolExecutor(max_workers=max_concurrent_handlers)
        self._event_counter = 0

        # Initialize audit logger with rotation support (Milestone 1, Task 3)
        self._audit_logger = AuditLogger(
            log_path=event_log_path,
            enabled=enable_audit,
            redactor=redactor,
            max_size_bytes=max_log_size_bytes,
            max_files=max_log_files,
        )

    def subscribe(
        self,
        handler: EventHandler,
        event_types: str | list[str],
        priority: int = 100,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Subscribe to events.

        Args:
            handler: Sync or async function to handle events
            event_types: Event type(s) to subscribe to, or "*" for all
            priority: Handler priority (higher number = runs first)
            metadata: Optional metadata for the subscription

        Returns:
            Subscription ID for later unsubscribing

        Example:
            def on_chat(event):
                print(event.payload)

            sub_id = bus.subscribe(on_chat, "chat.*", priority=100)
        """
        if isinstance(event_types, str):
            event_types = [event_types]

        subscription = Subscription(
            handler=handler,
            event_types=set(event_types),
            priority=priority,
            metadata=metadata or {},
        )

        with self._subscription_lock:
            self.subscriptions.append(subscription)
            # Sort by priority (higher number = higher priority, so reverse=True)
            self.subscriptions.sort(key=lambda s: s.priority, reverse=True)

        return subscription.subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """
        Unsubscribe by subscription ID.

        Args:
            subscription_id: ID returned from subscribe()

        Returns:
            True if subscription was found and removed
        """
        with self._subscription_lock:
            original_count = len(self.subscriptions)
            self.subscriptions = [
                s for s in self.subscriptions if s.subscription_id != subscription_id
            ]
            return len(self.subscriptions) < original_count

    def get_subscriptions(self) -> list[Subscription]:
        """
        Get all subscriptions.

        Returns:
            Copy of current subscriptions list
        """
        with self._subscription_lock:
            return self.subscriptions.copy()

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        source: str | None = None,
        correlation_id: str | None = None,
        wait_for_handlers: bool = False,
    ) -> str:
        """
        Emit an event.

        Constitution D1: If correlation_id is not provided, it will be
        automatically retrieved from the current context.

        Constitution D2: event_type MUST follow {domain}.{action} format.

        Args:
            event_type: Type of event (e.g., "chat.user_input")
            payload: Event data (no PII - Constitution D4)
            source: Source component name
            correlation_id: ID for tracking related events
            wait_for_handlers: If True, wait for all handlers to complete

        Returns:
            Event ID (empty string if event was dropped due to backpressure)

        Raises:
            ValueError: If event_type is empty or payload is not a dict
            ValidationError: If validate_events=True and event_type invalid
            BackpressureError: If max_pending_events reached with RAISE policy
        """
        start_time = time.perf_counter()

        # Basic input validation for robustness
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")

        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")

        # Constitution D2: Validate event type format (Milestone 2)
        if self._validate_events:
            # This raises ValidationError in strict mode
            self._validator.validate_event_type(event_type)

        # Backpressure check (Milestone 1, Task 2)
        # Only applies to async dispatch; sync dispatch (wait_for_handlers) blocks naturally
        if (
            self.max_pending_events is not None
            and not wait_for_handlers
            and not self._acquire_pending_slot()
        ):
            # Record dropped event metric
            if self._metrics:
                self._metrics.record_event_dropped(event_type)
            return ""  # Event was dropped

        # Use correlation ID from context if not explicitly provided (Constitution D1)
        if correlation_id is None:
            correlation_id = get_correlation_id()

        # Thread-safe event ID generation
        with self._subscription_lock:
            self._event_counter += 1
            event_id = f"evt_{int(time.time() * 1000)}_{self._event_counter}"

        event = Event(
            event_type=event_type,
            payload=payload,
            timestamp=time.time(),
            event_id=event_id,
            source=source,
            correlation_id=correlation_id,
        )

        # Log event if audit is enabled
        if self.enable_audit:
            self._audit_logger.log_event(event)

        # Record emit metric (Milestone 2)
        if self._metrics:
            emit_latency = time.perf_counter() - start_time
            self._metrics.record_event_emitted(event_type, emit_latency)

        # Find matching subscriptions (thread-safe snapshot)
        with self._subscription_lock:
            matching_subs = [sub for sub in self.subscriptions if sub.matches(event_type)]

        if not matching_subs:
            # Release backpressure slot if no handlers to run
            if self.max_pending_events is not None and not wait_for_handlers:
                self._release_pending_slot()
            return event_id

        if wait_for_handlers:
            # Synchronously wait for all handlers
            self._dispatch_sync(event, matching_subs)
        else:
            # Dispatch asynchronously
            self._dispatch_async(event, matching_subs)

        return event_id

    def _dispatch_sync(self, event: Event, subscriptions: list[Subscription]) -> None:
        """Dispatch event to handlers synchronously with timeout."""
        for sub in subscriptions:
            handler_start = time.perf_counter()
            success = True
            try:
                if asyncio.iscoroutinefunction(sub.handler):
                    # Run async handler in thread
                    future = self.executor.submit(asyncio.run, sub.handler(event))
                    future.result(timeout=self.handler_timeout)
                else:
                    # Run sync handler in thread for timeout enforcement
                    future = self.executor.submit(sub.handler, event)
                    future.result(timeout=self.handler_timeout)

            except TimeoutError:
                success = False
                # Handler timed out - log and continue
                self._log_handler_error(
                    event,
                    sub,
                    TimeoutError(f"Handler timed out after {self.handler_timeout}s"),
                    is_timeout=True,
                )
            except Exception as e:  # nosec - handler errors should not crash the event bus
                success = False
                # Log handler error but continue with other handlers
                self._log_handler_error(event, sub, e)
            finally:
                # Record handler execution metric (Milestone 2)
                if self._metrics:
                    handler_name = getattr(sub.handler, "__name__", "unknown")
                    duration = time.perf_counter() - handler_start
                    self._metrics.record_handler_execution(
                        event.event_type, handler_name, duration, success
                    )

    def _dispatch_async(self, event: Event, subscriptions: list[Subscription]) -> None:
        """
        Dispatch event to handlers asynchronously.

        Handles both sync and async contexts safely:
        - If called from async context: uses asyncio.create_task with tracking
        - If called from sync context: uses ThreadPoolExecutor
        """
        # Separate async and sync handlers
        async_subs: list[Subscription] = []
        sync_subs: list[Subscription] = []

        for sub in subscriptions:
            if asyncio.iscoroutinefunction(sub.handler):
                async_subs.append(sub)
            else:
                sync_subs.append(sub)

        # Schedule async handlers - detect if we're in an async context
        if async_subs:
            try:
                # If we are in an async loop, use it
                loop = asyncio.get_running_loop()
                for sub in async_subs:
                    task = loop.create_task(self._handle_async(event, sub))
                    # Track task for graceful shutdown
                    self._track_task(task)
            except RuntimeError:
                # No running loop - run async handlers in executor
                # This allows emit() to work from sync code
                for sub in async_subs:
                    future = self.executor.submit(asyncio.run, self._handle_async(event, sub))
                    # Add callback to surface exceptions (don't silently swallow)
                    future.add_done_callback(self._handle_executor_exception)

        # Execute sync handlers sequentially in executor to preserve priority order
        if sync_subs:

            def _run_sync_handlers_in_order() -> None:
                for sub in sync_subs:
                    try:
                        self._handle_sync(event, sub)
                    except Exception as e:  # nosec - isolate user handler failures
                        if isinstance(e, (KeyboardInterrupt, SystemExit)):
                            raise
                        self._log_handler_error(event, sub, e)

            future = self.executor.submit(_run_sync_handlers_in_order)
            # Add callback to surface exceptions
            future.add_done_callback(self._handle_executor_exception)

    def _track_task(self, task: asyncio.Task[None]) -> None:
        """Track an async task for lifecycle management."""
        with self._tasks_lock:
            self._pending_tasks.add(task)
        # Auto-remove when done
        task.add_done_callback(self._untrack_task)

    def _untrack_task(self, task: asyncio.Task[None]) -> None:
        """Remove completed task from tracking."""
        with self._tasks_lock:
            self._pending_tasks.discard(task)

    def _handle_executor_exception(self, future: Any) -> None:
        """
        Callback to surface exceptions from ThreadPoolExecutor.

        Without this, exceptions in executor tasks are silently swallowed
        (a known issue with concurrent.futures).
        """
        try:
            # This will raise if the task raised
            future.result()
        except Exception as e:  # nosec - log but don't crash
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            # Log the error - we can't emit an event here as we may be shutting down
            import sys

            print(f"[EventBus] Executor task failed: {e}", file=sys.stderr)
        finally:
            # Release backpressure slot when handler completes
            if self.max_pending_events is not None:
                self._release_pending_slot()

    def _acquire_pending_slot(self) -> bool:
        """
        Acquire a slot for pending event dispatch (backpressure control).

        Returns:
            True if slot acquired, False if event should be dropped.

        Raises:
            BackpressureError: If policy is RAISE and queue is full.
        """
        with self._pending_condition:
            if self._pending_count < self.max_pending_events:
                self._pending_count += 1
                return True

            # Queue is full - apply overflow policy
            if self.overflow_policy == OverflowPolicy.DROP:
                return False

            if self.overflow_policy == OverflowPolicy.RAISE:
                raise BackpressureError(
                    f"Event queue full ({self.max_pending_events} pending events)"
                )

            if self.overflow_policy == OverflowPolicy.BLOCK:
                # Wait for a slot to become available
                while self._pending_count >= self.max_pending_events:
                    self._pending_condition.wait()
                self._pending_count += 1
                return True

            # DROP_OLDEST not implemented yet - fall back to DROP
            return False

    def _release_pending_slot(self) -> None:
        """Release a backpressure slot after handler completion."""
        with self._pending_condition:
            if self._pending_count > 0:
                self._pending_count -= 1
            self._pending_condition.notify()

    def get_pending_count(self) -> int:
        """Get current count of pending events (for monitoring)."""
        with self._tasks_lock:
            return self._pending_count

    async def _handle_async(self, event: Event, subscription: Subscription) -> None:
        """Handle async event handler with metrics."""
        handler_start = time.perf_counter()
        success = True
        handler_name = getattr(subscription.handler, "__name__", "unknown")

        try:
            maybe_awaitable = subscription.handler(event)

            if asyncio.iscoroutine(maybe_awaitable) or isinstance(maybe_awaitable, Awaitable):
                result = await maybe_awaitable
            else:
                result = maybe_awaitable

            if result is not None and self.enable_audit:
                # Log warning if handler returns a value
                warning_event = Event(
                    event_type="system.handler_return_value",
                    payload={
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "subscription_id": subscription.subscription_id,
                        "handler_name": handler_name,
                        "returned_value": repr(result),
                    },
                    timestamp=time.time(),
                    event_id=f"warn_{int(time.time() * 1000)}",
                    source="event_bus",
                )
                self._audit_logger.log_event(warning_event)

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # nosec - handler errors are intentionally isolated
            success = False
            self._log_handler_error(event, subscription, e)
        finally:
            # Record handler execution metric (Milestone 2)
            if self._metrics:
                duration = time.perf_counter() - handler_start
                self._metrics.record_handler_execution(
                    event.event_type, handler_name, duration, success
                )

    def _handle_sync(self, event: Event, subscription: Subscription) -> None:
        """Handle sync event handler with metrics."""
        handler_start = time.perf_counter()
        success = True
        handler_name = getattr(subscription.handler, "__name__", "unknown")

        try:
            subscription.handler(event)
        except Exception as e:  # nosec - protect event bus from user handler exceptions
            success = False
            self._log_handler_error(event, subscription, e)
        finally:
            # Record handler execution metric (Milestone 2)
            if self._metrics:
                duration = time.perf_counter() - handler_start
                self._metrics.record_handler_execution(
                    event.event_type, handler_name, duration, success
                )

    def _log_handler_error(
        self,
        event: Event,
        subscription: Subscription,
        error: Exception,
        is_timeout: bool = False,
    ) -> None:
        """Log handler execution error as a system event and add to DLQ."""
        handler_name = getattr(subscription.handler, "__name__", "unknown")

        error_event = Event(
            event_type="system.handler_error",
            payload={
                "original_event_id": event.event_id,
                "original_event_type": event.event_type,
                "subscription_id": subscription.subscription_id,
                "error": str(error),
                "handler_name": handler_name,
            },
            timestamp=time.time(),
            event_id=f"err_{int(time.time() * 1000)}",
            source="event_bus",
        )

        if self.enable_audit:
            self._audit_logger.log_event(error_event)

        # Add to Dead Letter Queue if configured (Milestone 3)
        if self._dlq:
            from .dlq import FailureReason

            reason = FailureReason.HANDLER_TIMEOUT if is_timeout else FailureReason.HANDLER_ERROR
            self._dlq.add(
                event=event,
                reason=reason,
                error=str(error),
                handler_name=handler_name,
                metadata={"subscription_id": subscription.subscription_id},
            )

    def get_events(
        self,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """
        Get events from audit log.

        Args:
            event_type: Filter by event type
            since: Only events after this timestamp
            limit: Maximum number of events to return

        Returns:
            List of events matching criteria (most recent first)
        """
        return self._audit_logger.read_events(
            event_type=event_type,
            since=since,
            limit=limit,
        )

    def get_stats(self) -> dict[str, Any]:
        """
        Get event bus statistics.

        Returns:
            Dictionary with subscription counts, audit status, log stats, and metrics
        """
        with self._subscription_lock:
            stats: dict[str, Any] = {
                "active_subscriptions": len([s for s in self.subscriptions if s.active]),
                "total_subscriptions": len(self.subscriptions),
                "audit_enabled": self.enable_audit,
                "max_concurrent_handlers": self.max_concurrent_handlers,
                "validation_enabled": self._validate_events,
                "metrics_enabled": self._metrics is not None,
                "dlq_enabled": self._dlq is not None,
            }

            # Collect subscribed event types
            event_types_subscribed: set[str] = set()
            for sub in self.subscriptions:
                if sub.active:
                    event_types_subscribed.update(sub.event_types)

            stats["event_types_subscribed"] = list(event_types_subscribed)

        # Add pending task count
        with self._tasks_lock:
            stats["pending_async_tasks"] = len(self._pending_tasks)

        # Add backpressure stats
        with self._pending_lock:
            stats["pending_events"] = self._pending_count
        stats["max_pending_events"] = self.max_pending_events
        stats["overflow_policy"] = self.overflow_policy.value if self.max_pending_events else None

        # Add audit log stats
        stats.update(self._audit_logger.get_stats())

        # Add metrics if available (Milestone 2)
        if self._metrics:
            stats["metrics"] = self._metrics.get_snapshot()

        # Add DLQ stats if available (Milestone 3)
        if self._dlq:
            stats["dlq"] = self._dlq.get_stats()

        return stats

    def clear_log(self) -> bool:
        """
        Clear event audit log.

        Returns:
            True if log was cleared successfully
        """
        return self._audit_logger.clear()

    def shutdown(self, timeout: float = 30.0) -> None:
        """
        Shutdown event bus and cleanup resources.

        Args:
            timeout: Maximum time to wait for pending tasks (default 30s)
        """
        # Emit shutdown event FIRST (before executor shutdown)
        # This ensures handlers can still process the shutdown notification
        if self.enable_audit:
            self._audit_logger.log_event(
                Event(
                    event_type="system.event_bus_shutdown",
                    payload={"timestamp": time.time()},
                    timestamp=time.time(),
                    event_id=f"shutdown_{int(time.time() * 1000)}",
                    source="event_bus",
                )
            )

        # Deactivate all subscriptions (thread-safe)
        with self._subscription_lock:
            for sub in self.subscriptions:
                sub.active = False

        # Cancel pending async tasks
        with self._tasks_lock:
            for task in self._pending_tasks:
                if not task.done():
                    task.cancel()

        # Shutdown thread pool (wait for pending tasks)
        self.executor.shutdown(wait=True, cancel_futures=False)

    async def wait_for_pending(self, timeout: float = 30.0) -> int:
        """
        Wait for all pending async tasks to complete.

        Args:
            timeout: Maximum time to wait

        Returns:
            Number of tasks that completed
        """
        with self._tasks_lock:
            tasks = list(self._pending_tasks)

        if not tasks:
            return 0

        done, pending = await asyncio.wait(tasks, timeout=timeout)

        # Cancel any still pending
        for task in pending:
            task.cancel()

        return len(done)
