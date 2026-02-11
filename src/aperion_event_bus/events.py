"""
Event data models for the Event Bus.

This module defines the core Event dataclass - an immutable record of something
that happened in the system. Events follow the {domain}.{action} naming convention
as mandated by Constitution D2.

Event Naming Convention (REQUIRED):
    Events MUST follow {domain}.{action} format:
    - user.login, user.logout
    - chat.user_input, chat.response
    - system.startup, system.shutdown

    Pattern subscriptions use wildcards:
    - "chat.*" matches all chat events
    - "*" matches all events (catch-all)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _generate_event_id() -> str:
    """Generate a unique event ID with timestamp prefix for ordering."""
    return f"evt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True, slots=True)
class Event:
    """
    Immutable event record.

    All events are immutable once created, ensuring audit trail integrity.
    Events carry a correlation_id for distributed tracing (Constitution D1).

    Attributes:
        event_type: Event type in {domain}.{action} format (e.g., "chat.user_input")
        payload: Event data as a dictionary (no PII - Constitution D4)
        timestamp: Unix timestamp when event was created
        event_id: Unique identifier for this event
        source: Optional source component that emitted the event
        correlation_id: Optional correlation ID for distributed tracing
    """

    event_type: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=_generate_event_id)
    source: str | None = None
    correlation_id: str | None = None

    def to_jsonl(self) -> str:
        """
        Serialize event to JSONL format for audit logging.

        Returns:
            Compact JSON string (single line, no extra whitespace)
        """
        data = {
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "source": self.source,
            "correlation_id": self.correlation_id,
        }
        return json.dumps(data, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> Event:
        """
        Deserialize event from JSONL format.

        Args:
            line: A single JSON line from audit log

        Returns:
            Reconstructed Event object

        Raises:
            json.JSONDecodeError: If line is not valid JSON
            KeyError: If required fields are missing
        """
        data = json.loads(line.strip())
        return cls(
            event_type=data["event_type"],
            payload=data["payload"],
            timestamp=data["timestamp"],
            event_id=data["event_id"],
            source=data.get("source"),
            correlation_id=data.get("correlation_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Convert event to dictionary representation.

        Returns:
            Dictionary with all event fields
        """
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "source": self.source,
            "correlation_id": self.correlation_id,
        }
