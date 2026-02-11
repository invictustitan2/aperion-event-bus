"""
Milestone 2: Observability Tests

Tests for:
- Event type validation ({domain}.{action} format)
- Metrics collection (emit counts, handler latency)
- OpenTelemetry integration (optional)
"""

import time

import pytest

from aperion_event_bus import (
    EVENT_TYPE_PATTERN,
    EventBus,
    EventValidator,
    InMemoryMetrics,
    MetricsCollector,
    ValidationError,
    extract_action,
    extract_domain,
    is_valid_event_type,
)

# ============================================================================
# Event Validation Tests
# ============================================================================


class TestEventValidator:
    """Tests for EventValidator class."""

    def test_valid_event_types(self):
        """Test various valid event type formats."""
        validator = EventValidator()

        valid_types = [
            "user.login",
            "user.logout",
            "chat.message",
            "chat.user_input",
            "order.created",
            "system.startup",
            "api.request.received",
            "metrics.cpu.usage",
        ]

        for event_type in valid_types:
            result = validator.validate_event_type(event_type)
            assert result.valid, f"Expected '{event_type}' to be valid: {result.errors}"

    def test_invalid_event_types(self):
        """Test invalid event type formats."""
        validator = EventValidator()

        invalid_types = [
            "",  # Empty
            "login",  # No dot
            ".login",  # Starts with dot
            "user.",  # Ends with dot
            "User.Login",  # Uppercase
            "user-login",  # Hyphen instead of dot
            "user.Login",  # Uppercase action
            "123.action",  # Starts with number
        ]

        for event_type in invalid_types:
            result = validator.validate_event_type(event_type)
            assert not result.valid, f"Expected '{event_type}' to be invalid"

    def test_strict_mode_raises(self):
        """Test that strict mode raises ValidationError."""
        validator = EventValidator(strict=True)

        # Valid should not raise
        result = validator.validate_event_type("user.login")
        assert result.valid

        # Invalid should raise
        with pytest.raises(ValidationError) as exc_info:
            validator.validate_event_type("invalid")

        assert "must follow {domain}.{action}" in str(exc_info.value)

    def test_allowed_domains(self):
        """Test domain restriction."""
        validator = EventValidator(
            strict=True,
            allowed_domains={"user", "chat", "system"},
        )

        # Allowed domains should pass
        validator.validate_event_type("user.login")
        validator.validate_event_type("chat.message")
        validator.validate_event_type("system.startup")

        # Disallowed domain should fail
        with pytest.raises(ValidationError) as exc_info:
            validator.validate_event_type("order.created")

        assert "not in allowed domains" in str(exc_info.value)

    def test_system_domain_warning(self):
        """Test warning for non-standard system events."""
        validator = EventValidator()

        # Standard system events - no warning
        result = validator.validate_event_type("system.startup")
        assert result.valid
        assert len(result.warnings) == 0

        # Non-standard system event - warning
        result = validator.validate_event_type("system.custom_event")
        assert result.valid  # Still valid
        assert len(result.warnings) > 0
        assert "reserved" in result.warnings[0].lower()

    def test_validate_payload(self):
        """Test payload validation."""
        validator = EventValidator()

        # Valid payload
        result = validator.validate_payload({"key": "value"})
        assert result.valid

        # Invalid payload type (would be caught at runtime typically)
        # Note: This tests the validator, not the bus
        result = validator.validate_payload({"key": "value"}, required_fields={"missing"})
        assert not result.valid
        assert "missing" in str(result.errors)


class TestValidationHelpers:
    """Tests for validation helper functions."""

    def test_is_valid_event_type(self):
        """Test quick validation function."""
        assert is_valid_event_type("user.login")
        assert is_valid_event_type("chat.message.sent")
        assert not is_valid_event_type("invalid")
        assert not is_valid_event_type("")

    def test_extract_domain(self):
        """Test domain extraction."""
        assert extract_domain("user.login") == "user"
        assert extract_domain("chat.message.sent") == "chat"
        assert extract_domain("invalid") is None
        assert extract_domain("") is None

    def test_extract_action(self):
        """Test action extraction."""
        assert extract_action("user.login") == "login"
        assert extract_action("chat.message.sent") == "message.sent"
        assert extract_action("invalid") is None

    def test_event_type_pattern(self):
        """Test the regex pattern directly."""
        assert EVENT_TYPE_PATTERN.match("user.login")
        assert EVENT_TYPE_PATTERN.match("a.b")
        assert EVENT_TYPE_PATTERN.match("user123.login456")
        assert not EVENT_TYPE_PATTERN.match("user")
        assert not EVENT_TYPE_PATTERN.match("User.Login")


# ============================================================================
# Metrics Collection Tests
# ============================================================================


class TestMetricsCollector:
    """Tests for MetricsCollector and InMemoryMetrics."""

    def test_in_memory_metrics_basic(self):
        """Test basic in-memory metrics collection."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)

        # Record some events
        metrics.record_event_emitted("user.login", 0.001)
        metrics.record_event_emitted("user.login", 0.002)
        metrics.record_event_emitted("chat.message", 0.001)

        snapshot = metrics.get_snapshot()
        assert snapshot["events_emitted"]["user.login"] == 2
        assert snapshot["events_emitted"]["chat.message"] == 1
        assert snapshot["total_events_emitted"] == 3

    def test_handler_execution_metrics(self):
        """Test handler execution metrics."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)

        # Record handler executions
        metrics.record_handler_execution("user.login", "on_login", 0.01, success=True)
        metrics.record_handler_execution("user.login", "on_login", 0.02, success=True)
        metrics.record_handler_execution("user.login", "on_login", 0.05, success=False)

        snapshot = metrics.get_snapshot()
        handler_stats = snapshot["handler_stats"]["on_login"]
        assert handler_stats["total_calls"] == 3
        assert handler_stats["success_count"] == 2
        assert handler_stats["failure_count"] == 1

    def test_dropped_event_metrics(self):
        """Test dropped event tracking."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)

        metrics.record_event_dropped("user.login")
        metrics.record_event_dropped("user.login")
        metrics.record_event_dropped("chat.message")

        snapshot = metrics.get_snapshot()
        assert snapshot["events_dropped"]["user.login"] == 2
        assert snapshot["events_dropped"]["chat.message"] == 1


class TestEventBusWithMetrics:
    """Tests for EventBus with metrics integration."""

    def test_eventbus_with_metrics(self):
        """Test that EventBus records metrics when configured."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)
        bus = EventBus(metrics=metrics, enable_audit=False)

        events_received = []

        def handler(event):
            events_received.append(event)

        bus.subscribe(handler, "user.*")
        bus.emit("user.login", {"username": "alice"}, wait_for_handlers=True)
        bus.emit("user.logout", {"username": "alice"}, wait_for_handlers=True)

        snapshot = metrics.get_snapshot()

        # Check emit metrics
        assert snapshot["total_events_emitted"] == 2
        assert snapshot["events_emitted"]["user.login"] == 1
        assert snapshot["events_emitted"]["user.logout"] == 1

        # Check handler metrics
        assert snapshot["handler_stats"]["handler"]["total_calls"] == 2
        assert snapshot["handler_stats"]["handler"]["success_count"] == 2

        bus.shutdown()

    def test_eventbus_metrics_on_handler_error(self):
        """Test that handler errors are recorded in metrics."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)
        bus = EventBus(metrics=metrics, enable_audit=False)

        def failing_handler(event):
            raise ValueError("Test error")

        bus.subscribe(failing_handler, "user.*")
        bus.emit("user.login", {"username": "alice"}, wait_for_handlers=True)

        snapshot = metrics.get_snapshot()
        assert snapshot["handler_stats"]["failing_handler"]["failure_count"] == 1

        bus.shutdown()

    def test_eventbus_stats_includes_metrics(self):
        """Test that get_stats() includes metrics when configured."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)
        bus = EventBus(metrics=metrics, enable_audit=False)

        bus.emit("test.event", {})

        stats = bus.get_stats()
        assert "metrics" in stats
        assert "metrics_enabled" in stats
        assert stats["metrics_enabled"] is True

        bus.shutdown()


# ============================================================================
# Event Validation Integration Tests
# ============================================================================


class TestEventBusWithValidation:
    """Tests for EventBus with event validation enabled."""

    def test_validation_enabled_valid_events(self):
        """Test that valid events pass with validation enabled."""
        bus = EventBus(validate_events=True, enable_audit=False)

        event_id = bus.emit("user.login", {"username": "alice"})
        assert event_id != ""

        bus.shutdown()

    def test_validation_enabled_invalid_events(self):
        """Test that invalid events are rejected with validation enabled."""
        bus = EventBus(validate_events=True, enable_audit=False)

        with pytest.raises(ValidationError) as exc_info:
            bus.emit("invalid", {"data": "test"})

        assert "must follow {domain}.{action}" in str(exc_info.value)

        bus.shutdown()

    def test_validation_disabled_allows_any(self):
        """Test that validation disabled allows any event type."""
        bus = EventBus(validate_events=False, enable_audit=False)

        # Should not raise even with invalid format
        event_id = bus.emit("invalid", {"data": "test"})
        assert event_id != ""

        bus.shutdown()

    def test_custom_validator(self):
        """Test using a custom validator."""
        validator = EventValidator(
            strict=True,
            allowed_domains={"api", "internal"},
        )
        bus = EventBus(validate_events=True, validator=validator, enable_audit=False)

        # Allowed domain
        bus.emit("api.request", {"path": "/users"})

        # Disallowed domain
        with pytest.raises(ValidationError):
            bus.emit("user.login", {"username": "alice"})

        bus.shutdown()


# ============================================================================
# Combined Observability Tests
# ============================================================================


class TestObservabilityIntegration:
    """Integration tests for full observability stack."""

    def test_metrics_and_validation_together(self):
        """Test metrics and validation working together."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)
        bus = EventBus(
            metrics=metrics,
            validate_events=True,
            enable_audit=False,
        )

        events_received = []

        def handler(event):
            events_received.append(event)

        bus.subscribe(handler, "user.*")

        # Valid events should be recorded
        bus.emit("user.login", {"username": "alice"}, wait_for_handlers=True)
        bus.emit("user.logout", {"username": "alice"}, wait_for_handlers=True)

        # Invalid event should raise (not recorded)
        with pytest.raises(ValidationError):
            bus.emit("invalid", {})

        snapshot = metrics.get_snapshot()
        assert snapshot["total_events_emitted"] == 2
        assert len(events_received) == 2

        bus.shutdown()

    def test_stats_shows_observability_config(self):
        """Test that stats show observability configuration."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)
        bus = EventBus(
            metrics=metrics,
            validate_events=True,
            enable_audit=True,
        )

        stats = bus.get_stats()
        assert stats["metrics_enabled"] is True
        assert stats["validation_enabled"] is True
        assert stats["audit_enabled"] is True

        bus.shutdown()

    def test_handler_timing_accuracy(self):
        """Test that handler timing is reasonably accurate."""
        backend = InMemoryMetrics()
        metrics = MetricsCollector(backend)
        bus = EventBus(metrics=metrics, enable_audit=False)

        def slow_handler(event):
            time.sleep(0.1)

        bus.subscribe(slow_handler, "test.*")
        bus.emit("test.slow", {}, wait_for_handlers=True)

        snapshot = metrics.get_snapshot()
        latencies = snapshot["handler_stats"]["slow_handler"]["latencies"]
        assert len(latencies) == 1
        assert latencies[0] >= 0.09  # Allow some timing variance

        bus.shutdown()
