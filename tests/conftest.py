"""
Pytest configuration for Event Bus tests.
"""

from __future__ import annotations

import contextlib

import pytest


@pytest.fixture
def event_bus():
    """Create a fresh EventBus instance for testing."""
    from aperion_event_bus import EventBus

    bus = EventBus(enable_audit=False)
    yield bus
    # Cleanup
    with contextlib.suppress(Exception):
        bus.shutdown()


@pytest.fixture
def audited_bus(tmp_path):
    """Create an EventBus with audit logging enabled."""
    from aperion_event_bus import EventBus

    log_path = tmp_path / "events.jsonl"
    bus = EventBus(enable_audit=True, event_log_path=log_path)
    yield bus, log_path
    # Cleanup
    with contextlib.suppress(Exception):
        bus.shutdown()
