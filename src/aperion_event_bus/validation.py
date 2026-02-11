"""
Event schema validation for Constitution D2 compliance.

Enforces the {domain}.{action} event naming convention and provides
optional payload validation.

Usage:
    from aperion_event_bus.validation import EventValidator, ValidationError

    validator = EventValidator()

    # Validate event type format
    validator.validate_event_type("user.login")  # OK
    validator.validate_event_type("invalid")  # Raises ValidationError

    # With strict mode
    validator = EventValidator(strict=True, allowed_domains={"user", "chat", "system"})
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Event type pattern: {domain}.{action} with optional sub-actions
# Examples: user.login, chat.message.sent, system.health.check
EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# Reserved system event types
SYSTEM_EVENT_TYPES = frozenset(
    {
        "system.startup",
        "system.shutdown",
        "system.health",
        "system.handler_error",
        "system.handler_return_value",
        "system.event_bus_shutdown",
    }
)


class ValidationError(Exception):
    """Raised when event validation fails."""

    def __init__(self, message: str, event_type: str | None = None):
        self.event_type = event_type
        super().__init__(message)


@dataclass
class ValidationResult:
    """Result of event validation."""

    valid: bool
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return self.valid and len(self.errors) == 0


class EventValidator:
    """
    Validates event types and payloads.

    Enforces Constitution D2 naming conventions:
    - All events MUST follow {domain}.{action} format
    - Domain and action should be lowercase with underscores
    - Examples: user.login, chat.message, order.created

    Usage:
        validator = EventValidator()

        # Basic validation
        validator.validate_event_type("user.login")  # OK

        # With strict domain checking
        validator = EventValidator(
            strict=True,
            allowed_domains={"user", "chat", "order"}
        )
        validator.validate_event_type("invalid.event")  # Raises ValidationError
    """

    def __init__(
        self,
        strict: bool = False,
        allowed_domains: set[str] | None = None,
        require_source: bool = False,
        custom_pattern: re.Pattern[str] | None = None,
    ):
        """
        Initialize the validator.

        Args:
            strict: If True, raise errors; if False, return validation result
            allowed_domains: Set of allowed event domains (optional)
            require_source: If True, events must have a source
            custom_pattern: Custom regex pattern for event types
        """
        self.strict = strict
        self.allowed_domains = allowed_domains
        self.require_source = require_source
        self.pattern = custom_pattern or EVENT_TYPE_PATTERN

    def validate_event_type(self, event_type: str) -> ValidationResult:
        """
        Validate an event type string.

        Args:
            event_type: Event type to validate

        Returns:
            ValidationResult with validation status

        Raises:
            ValidationError: If strict mode and validation fails
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Check for empty event type
        if not event_type or not event_type.strip():
            errors.append("Event type cannot be empty")
            return self._result(errors, warnings, event_type)

        # Check format
        if not self.pattern.match(event_type):
            errors.append(
                f"Event type '{event_type}' must follow {{domain}}.{{action}} format "
                f"(lowercase, dots separating parts)"
            )
            return self._result(errors, warnings, event_type)

        # Extract domain (first part before dot)
        domain = event_type.split(".")[0]

        # Check allowed domains
        if self.allowed_domains and domain not in self.allowed_domains:
            errors.append(
                f"Domain '{domain}' not in allowed domains: {sorted(self.allowed_domains)}"
            )

        # Warn about reserved system events
        if event_type.startswith("system.") and event_type not in SYSTEM_EVENT_TYPES:
            warnings.append(
                f"Event type '{event_type}' uses 'system' domain which is reserved"
            )

        return self._result(errors, warnings, event_type)

    def validate_payload(
        self,
        payload: dict[str, Any],
        required_fields: set[str] | None = None,
    ) -> ValidationResult:
        """
        Validate an event payload.

        Args:
            payload: Event payload dictionary
            required_fields: Set of required field names

        Returns:
            ValidationResult with validation status
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not isinstance(payload, dict):
            errors.append(f"Payload must be a dict, got {type(payload).__name__}")
            return self._result(errors, warnings)

        # Check required fields
        if required_fields:
            missing = required_fields - set(payload.keys())
            if missing:
                errors.append(f"Missing required fields: {sorted(missing)}")

        return self._result(errors, warnings)

    def validate_source(self, source: str | None) -> ValidationResult:
        """
        Validate event source.

        Args:
            source: Event source string

        Returns:
            ValidationResult with validation status
        """
        errors: list[str] = []
        warnings: list[str] = []

        if self.require_source and not source:
            errors.append("Event source is required")

        return self._result(errors, warnings)

    def validate(
        self,
        event_type: str,
        payload: dict[str, Any],
        source: str | None = None,
        required_fields: set[str] | None = None,
    ) -> ValidationResult:
        """
        Validate all aspects of an event.

        Args:
            event_type: Event type string
            payload: Event payload
            source: Event source
            required_fields: Required payload fields

        Returns:
            Combined ValidationResult
        """
        all_errors: list[str] = []
        all_warnings: list[str] = []

        # Validate each component
        for result in [
            self.validate_event_type(event_type),
            self.validate_payload(payload, required_fields),
            self.validate_source(source),
        ]:
            all_errors.extend(result.errors)
            all_warnings.extend(result.warnings)

        return self._result(all_errors, all_warnings, event_type)

    def _result(
        self,
        errors: list[str],
        warnings: list[str],
        event_type: str | None = None,
    ) -> ValidationResult:
        """Create result, optionally raising in strict mode."""
        valid = len(errors) == 0

        if self.strict and not valid:
            raise ValidationError("; ".join(errors), event_type)

        return ValidationResult(valid=valid, errors=errors, warnings=warnings)


def extract_domain(event_type: str) -> str | None:
    """
    Extract the domain from an event type.

    Args:
        event_type: Event type string

    Returns:
        Domain string or None if invalid format
    """
    if not event_type or "." not in event_type:
        return None
    return event_type.split(".")[0]


def extract_action(event_type: str) -> str | None:
    """
    Extract the action from an event type.

    Args:
        event_type: Event type string

    Returns:
        Action string (everything after first dot) or None if invalid
    """
    if not event_type or "." not in event_type:
        return None
    parts = event_type.split(".", 1)
    return parts[1] if len(parts) > 1 else None


def is_valid_event_type(event_type: str) -> bool:
    """
    Quick check if event type is valid.

    Args:
        event_type: Event type string

    Returns:
        True if valid, False otherwise
    """
    return bool(event_type and EVENT_TYPE_PATTERN.match(event_type))
