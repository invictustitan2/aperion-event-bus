"""
Dead Letter Queue for failed events.

Provides a mechanism to store events that failed processing for later
inspection, retry, or manual intervention.

Usage:
    from aperion_event_bus import EventBus
    from aperion_event_bus.dlq import DeadLetterQueue, FailedEvent

    dlq = DeadLetterQueue(max_size=1000)
    bus = EventBus(dead_letter_queue=dlq)

    # Later, inspect failed events
    failed = dlq.get_all()
    for item in failed:
        print(f"Event {item.event.event_id} failed: {item.error}")

    # Retry a failed event
    dlq.retry(failed[0].id, bus)
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bus import EventBus
    from .events import Event

logger = logging.getLogger(__name__)


class FailureReason(Enum):
    """Reason for event failure."""

    HANDLER_ERROR = "handler_error"
    HANDLER_TIMEOUT = "handler_timeout"
    ALL_HANDLERS_FAILED = "all_handlers_failed"
    VALIDATION_ERROR = "validation_error"
    SERIALIZATION_ERROR = "serialization_error"


@dataclass
class FailedEvent:
    """
    Record of a failed event.

    Attributes:
        id: Unique ID for this DLQ entry
        event: The original event that failed
        reason: Why the event failed
        error: Error message or exception details
        handler_name: Name of the handler that failed (if applicable)
        timestamp: When the failure occurred
        retry_count: Number of retry attempts
        metadata: Additional context about the failure
    """

    id: str
    event: Event
    reason: FailureReason
    error: str
    handler_name: str | None = None
    timestamp: float = field(default_factory=time.time)
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "event": self.event.to_dict(),
            "reason": self.reason.value,
            "error": self.error,
            "handler_name": self.handler_name,
            "timestamp": self.timestamp,
            "retry_count": self.retry_count,
            "metadata": self.metadata,
        }


class DeadLetterQueue:
    """
    Dead Letter Queue for failed events.

    Thread-safe queue that stores events that failed processing.
    Supports inspection, retry, and persistence.

    Usage:
        dlq = DeadLetterQueue(max_size=1000)
        bus = EventBus(dead_letter_queue=dlq)

        # Add failed event
        dlq.add(event, FailureReason.HANDLER_ERROR, "ValueError: invalid data")

        # Get all failed events
        failed = dlq.get_all()

        # Retry a specific event
        dlq.retry(failed[0].id, bus)

        # Clear old entries
        dlq.clear_older_than(hours=24)
    """

    def __init__(
        self,
        max_size: int = 10000,
        persist_path: Path | None = None,
    ):
        """
        Initialize the Dead Letter Queue.

        Args:
            max_size: Maximum number of failed events to store
            persist_path: Optional path to persist DLQ to disk
        """
        self.max_size = max_size
        self.persist_path = persist_path
        self._queue: deque[FailedEvent] = deque(maxlen=max_size)
        self._lock = threading.Lock()
        self._by_id: dict[str, FailedEvent] = {}

    def add(
        self,
        event: Event,
        reason: FailureReason,
        error: str,
        handler_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Add a failed event to the queue.

        Args:
            event: The event that failed
            reason: Why it failed
            error: Error details
            handler_name: Handler that caused the failure
            metadata: Additional context

        Returns:
            ID of the DLQ entry
        """
        entry_id = f"dlq_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

        failed_event = FailedEvent(
            id=entry_id,
            event=event,
            reason=reason,
            error=error,
            handler_name=handler_name,
            metadata=metadata or {},
        )

        with self._lock:
            # If queue is at max, oldest will be dropped automatically
            if len(self._queue) >= self.max_size:
                oldest = self._queue[0]
                self._by_id.pop(oldest.id, None)

            self._queue.append(failed_event)
            self._by_id[entry_id] = failed_event

        logger.warning(
            "Event added to DLQ",
            extra={
                "dlq_id": entry_id,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "reason": reason.value,
                "error": error[:200],  # Truncate for logging
            },
        )

        return entry_id

    def get(self, entry_id: str) -> FailedEvent | None:
        """Get a specific failed event by ID."""
        with self._lock:
            return self._by_id.get(entry_id)

    def get_all(self, limit: int = 100) -> list[FailedEvent]:
        """Get all failed events (most recent first)."""
        with self._lock:
            return list(reversed(list(self._queue)))[:limit]

    def get_by_event_type(self, event_type: str, limit: int = 100) -> list[FailedEvent]:
        """Get failed events filtered by event type."""
        with self._lock:
            return [
                fe for fe in reversed(list(self._queue)) if fe.event.event_type == event_type
            ][:limit]

    def get_by_reason(self, reason: FailureReason, limit: int = 100) -> list[FailedEvent]:
        """Get failed events filtered by failure reason."""
        with self._lock:
            return [fe for fe in reversed(list(self._queue)) if fe.reason == reason][:limit]

    def remove(self, entry_id: str) -> bool:
        """
        Remove a failed event from the queue.

        Args:
            entry_id: ID of the DLQ entry to remove

        Returns:
            True if removed, False if not found
        """
        with self._lock:
            if entry_id not in self._by_id:
                return False

            self._by_id.pop(entry_id)
            # Remove from deque (O(n) but necessary)
            self._queue = deque(
                (fe for fe in self._queue if fe.id != entry_id),
                maxlen=self.max_size,
            )
            return True

    def retry(
        self,
        entry_id: str,
        bus: EventBus,
        remove_on_success: bool = True,
    ) -> bool:
        """
        Retry a failed event.

        Args:
            entry_id: ID of the DLQ entry to retry
            bus: EventBus to re-emit the event on
            remove_on_success: Whether to remove from DLQ if retry succeeds

        Returns:
            True if retry succeeded, False otherwise
        """
        failed_event = self.get(entry_id)
        if not failed_event:
            return False

        # Increment retry count
        with self._lock:
            failed_event.retry_count += 1
            failed_event.metadata["last_retry"] = time.time()

        try:
            # Re-emit the event
            bus.emit(
                failed_event.event.event_type,
                failed_event.event.payload,
                source=failed_event.event.source,
                correlation_id=failed_event.event.correlation_id,
                wait_for_handlers=True,  # Sync to catch errors
            )

            if remove_on_success:
                self.remove(entry_id)

            logger.info(
                "DLQ event retry succeeded",
                extra={"dlq_id": entry_id, "event_id": failed_event.event.event_id},
            )
            return True

        except Exception as e:
            # Update error on retry failure
            with self._lock:
                failed_event.error = f"Retry failed: {e}"

            logger.warning(
                "DLQ event retry failed",
                extra={"dlq_id": entry_id, "error": str(e)},
            )
            return False

    def retry_all(
        self,
        bus: EventBus,
        max_retries: int = 3,
        remove_on_success: bool = True,
    ) -> dict[str, bool]:
        """
        Retry all events that haven't exceeded max retries.

        Args:
            bus: EventBus to re-emit events on
            max_retries: Maximum retry attempts per event
            remove_on_success: Whether to remove successful retries

        Returns:
            Dict mapping entry_id to success/failure
        """
        results: dict[str, bool] = {}

        # Get copy of entries to retry
        with self._lock:
            to_retry = [fe for fe in self._queue if fe.retry_count < max_retries]

        for failed_event in to_retry:
            results[failed_event.id] = self.retry(
                failed_event.id, bus, remove_on_success
            )

        return results

    def clear(self) -> int:
        """
        Clear all entries from the queue.

        Returns:
            Number of entries cleared
        """
        with self._lock:
            count = len(self._queue)
            self._queue.clear()
            self._by_id.clear()
            return count

    def clear_older_than(self, hours: float = 24) -> int:
        """
        Clear entries older than specified hours.

        Args:
            hours: Age threshold in hours

        Returns:
            Number of entries cleared
        """
        cutoff = time.time() - (hours * 3600)

        with self._lock:
            old_count = len(self._queue)
            self._queue = deque(
                (fe for fe in self._queue if fe.timestamp >= cutoff),
                maxlen=self.max_size,
            )
            # Rebuild index
            self._by_id = {fe.id: fe for fe in self._queue}
            return old_count - len(self._queue)

    def size(self) -> int:
        """Get current queue size."""
        with self._lock:
            return len(self._queue)

    def get_stats(self) -> dict[str, Any]:
        """Get DLQ statistics."""
        with self._lock:
            by_reason: dict[str, int] = {}
            by_event_type: dict[str, int] = {}

            for fe in self._queue:
                reason_key = fe.reason.value
                by_reason[reason_key] = by_reason.get(reason_key, 0) + 1

                by_event_type[fe.event.event_type] = (
                    by_event_type.get(fe.event.event_type, 0) + 1
                )

            oldest_ts = self._queue[0].timestamp if self._queue else None
            newest_ts = self._queue[-1].timestamp if self._queue else None

            return {
                "size": len(self._queue),
                "max_size": self.max_size,
                "by_reason": by_reason,
                "by_event_type": by_event_type,
                "oldest_timestamp": oldest_ts,
                "newest_timestamp": newest_ts,
            }
