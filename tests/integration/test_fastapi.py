"""
FastAPI integration tests for EventBus.

Tests:
- EventBus as FastAPI dependency
- Correlation ID propagation through middleware
- Async handlers with FastAPI endpoints
- Request-scoped event emission
"""

import asyncio
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from aperion_event_bus import (
    DeadLetterQueue,
    Event,
    EventBus,
    extract_correlation_id_from_headers,
    get_correlation_id,
    set_correlation_id,
)

# --- Fixtures ---


@pytest.fixture
def event_bus(tmp_path: Path) -> EventBus:
    """Create a fresh EventBus for each test."""
    bus = EventBus(
        enable_audit=True,
        event_log_path=tmp_path / "events.jsonl",
        handler_timeout=5.0,
    )
    yield bus
    bus.shutdown()


@pytest.fixture
def app_with_bus(event_bus: EventBus) -> FastAPI:
    """Create a FastAPI app with EventBus dependency."""
    app = FastAPI()

    # Store events received by handlers
    received_events: list[Event] = []

    def capture_handler(event: Event) -> None:
        received_events.append(event)

    event_bus.subscribe(capture_handler, "api.*")

    # Dependency to get the event bus
    def get_event_bus() -> EventBus:
        return event_bus

    # Middleware for correlation ID propagation
    class CorrelationMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # Extract or generate correlation ID from headers
            headers = dict(request.headers)
            correlation_id = extract_correlation_id_from_headers(headers)
            set_correlation_id(correlation_id)

            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response

    app.add_middleware(CorrelationMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/users")
    def create_user(
        bus: Annotated[EventBus, Depends(get_event_bus)],
    ):
        correlation_id = get_correlation_id()
        event_id = bus.emit(
            "api.user_created",
            {"username": "testuser"},
            source="api",
            correlation_id=correlation_id,
            wait_for_handlers=True,
        )
        return {"event_id": event_id, "correlation_id": correlation_id}

    @app.post("/async-action")
    async def async_action(
        bus: Annotated[EventBus, Depends(get_event_bus)],
    ):
        correlation_id = get_correlation_id()
        event_id = bus.emit(
            "api.async_action",
            {"action": "test"},
            source="api",
            correlation_id=correlation_id,
        )
        # Simulate async work
        await asyncio.sleep(0.01)
        return {"event_id": event_id}

    # Attach for test inspection
    app.state.received_events = received_events
    app.state.event_bus = event_bus

    return app


@pytest.fixture
def client(app_with_bus: FastAPI) -> TestClient:
    """Create test client."""
    return TestClient(app_with_bus)


# --- Tests ---


class TestFastAPIIntegration:
    """Test EventBus integration with FastAPI."""

    def test_health_endpoint(self, client: TestClient):
        """Basic sanity check that app works."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_event_emission_from_endpoint(self, client: TestClient, app_with_bus: FastAPI):
        """Test that events are emitted from API endpoints."""
        response = client.post("/users")
        assert response.status_code == 200

        data = response.json()
        assert "event_id" in data
        assert data["event_id"].startswith("evt_")

        # Verify event was captured
        received = app_with_bus.state.received_events
        assert len(received) == 1
        assert received[0].event_type == "api.user_created"
        assert received[0].payload["username"] == "testuser"

    def test_correlation_id_propagation(self, client: TestClient):
        """Test correlation ID flows through middleware to events."""
        # Send request with correlation ID header
        correlation_id = "test-correlation-123"
        response = client.post(
            "/users",
            headers={"X-Correlation-ID": correlation_id},
        )
        assert response.status_code == 200

        # Response should echo correlation ID
        assert response.headers["X-Correlation-ID"] == correlation_id

        # Event should have same correlation ID
        data = response.json()
        assert data["correlation_id"] == correlation_id

    def test_correlation_id_generated_if_missing(self, client: TestClient):
        """Test correlation ID is generated if not provided."""
        response = client.post("/users")
        assert response.status_code == 200

        # Should have a generated correlation ID
        correlation_id = response.headers["X-Correlation-ID"]
        assert correlation_id is not None
        assert len(correlation_id) > 0

        # Event should have same ID
        data = response.json()
        assert data["correlation_id"] == correlation_id

    def test_async_endpoint_with_events(self, client: TestClient, app_with_bus: FastAPI):
        """Test async endpoints can emit events."""
        response = client.post("/async-action")
        assert response.status_code == 200

        data = response.json()
        assert "event_id" in data

        # Event should be captured (may need small wait for async dispatch)
        received = app_with_bus.state.received_events
        assert any(e.event_type == "api.async_action" for e in received)

    def test_multiple_requests_isolated_correlation(self, client: TestClient):
        """Test each request gets its own correlation ID."""
        response1 = client.post("/users")
        response2 = client.post("/users")

        corr1 = response1.headers["X-Correlation-ID"]
        corr2 = response2.headers["X-Correlation-ID"]

        # Each request should have unique correlation ID
        assert corr1 != corr2


class TestAsyncHandlersWithFastAPI:
    """Test async event handlers in FastAPI context."""

    def test_async_handler_in_fastapi(self, tmp_path: Path):
        """Test async handlers work correctly in FastAPI."""
        bus = EventBus(enable_audit=False)
        results: list[str] = []

        async def async_handler(event: Event) -> None:
            await asyncio.sleep(0.01)
            results.append(event.event_type)

        bus.subscribe(async_handler, "test.*")

        app = FastAPI()

        @app.post("/trigger")
        async def trigger():
            bus.emit("test.async", {"data": "value"})
            await asyncio.sleep(0.05)  # Give handler time
            return {"triggered": True}

        client = TestClient(app)
        response = client.post("/trigger")
        assert response.status_code == 200

        bus.shutdown()

    def test_mixed_sync_async_handlers(self, tmp_path: Path):
        """Test mix of sync and async handlers."""
        bus = EventBus(enable_audit=False)
        sync_results: list[str] = []
        async_results: list[str] = []

        def sync_handler(event: Event) -> None:
            sync_results.append(event.event_type)

        async def async_handler(event: Event) -> None:
            await asyncio.sleep(0.01)
            async_results.append(event.event_type)

        bus.subscribe(sync_handler, "test.*", priority=100)
        bus.subscribe(async_handler, "test.*", priority=50)

        app = FastAPI()

        @app.post("/trigger")
        async def trigger():
            bus.emit("test.mixed", {}, wait_for_handlers=True)
            await asyncio.sleep(0.05)
            return {"sync": len(sync_results), "async": len(async_results)}

        client = TestClient(app)
        response = client.post("/trigger")
        assert response.status_code == 200

        data = response.json()
        assert data["sync"] >= 1

        bus.shutdown()


class TestDLQWithFastAPI:
    """Test Dead Letter Queue in FastAPI context."""

    def test_failed_handler_adds_to_dlq(self, tmp_path: Path):
        """Test handler failures go to DLQ."""
        dlq = DeadLetterQueue(max_size=100)
        bus = EventBus(dead_letter_queue=dlq, enable_audit=False)

        def failing_handler(event: Event) -> None:
            raise ValueError("API handler error")

        bus.subscribe(failing_handler, "api.*")

        app = FastAPI()

        @app.post("/fail")
        def fail_endpoint():
            bus.emit("api.will_fail", {"error": True}, wait_for_handlers=True)
            return {"dlq_size": dlq.size()}

        client = TestClient(app)
        response = client.post("/fail")
        assert response.status_code == 200

        data = response.json()
        assert data["dlq_size"] == 1

        # Verify DLQ content
        failed = dlq.get_all()
        assert len(failed) == 1
        assert failed[0].event.event_type == "api.will_fail"

        bus.shutdown()


class TestEventBusLifecycle:
    """Test EventBus lifecycle in FastAPI app."""

    def test_shutdown_on_app_shutdown(self, tmp_path: Path):
        """Test EventBus shutdown is called on app shutdown."""
        bus = EventBus(
            enable_audit=True,
            event_log_path=tmp_path / "events.jsonl",
        )
        shutdown_called = {"value": False}

        original_shutdown = bus.shutdown

        def tracked_shutdown():
            shutdown_called["value"] = True
            original_shutdown()

        bus.shutdown = tracked_shutdown

        app = FastAPI()

        @app.on_event("shutdown")
        def shutdown_event():
            bus.shutdown()

        @app.get("/test")
        def test_endpoint():
            bus.emit("test.event", {})
            return {"ok": True}

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200

        # Shutdown should have been called
        assert shutdown_called["value"] is True

    def test_stats_endpoint(self, tmp_path: Path):
        """Test exposing EventBus stats via API."""
        bus = EventBus(enable_audit=True, event_log_path=tmp_path / "events.jsonl")

        app = FastAPI()

        @app.get("/stats")
        def get_stats():
            return bus.get_stats()

        @app.post("/emit")
        def emit_event():
            bus.emit("test.event", {"data": "value"}, wait_for_handlers=True)
            return {"ok": True}

        client = TestClient(app)

        # Initial stats
        response = client.get("/stats")
        assert response.status_code == 200
        stats = response.json()
        assert "active_subscriptions" in stats
        assert "audit_enabled" in stats

        # Emit some events
        client.post("/emit")
        client.post("/emit")

        # Updated stats
        response = client.get("/stats")
        stats = response.json()
        assert stats["total_events_logged"] >= 2

        bus.shutdown()
