"""
Audit logging for the Event Bus.

This module handles persistent event logging to JSONL files for compliance,
debugging, and observability. It also provides PII redaction capabilities
per Constitution D4 (No Secrets/PII in logs).

The Redactor class is a placeholder for Phase 1.7 of the ParseGate plan,
where regex-based PII scrubbing will be implemented.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .events import Event

logger = logging.getLogger(__name__)


class Redactor:
    """
    PII and secret redaction for audit logs.

    Constitution D4 mandates that no secrets or PII appear in logs.
    This class provides configurable redaction patterns.

    Phase 1.7 Implementation Notes:
    - Add regex patterns for common PII (emails, SSNs, credit cards)
    - Add secret detection (API keys, tokens, passwords)
    - Support custom patterns via configuration
    - Maintain audit of what was redacted (without exposing the data)

    Usage:
        redactor = Redactor()
        safe_payload = redactor.redact(payload)
    """

    # Default patterns to redact - comprehensive PII/secret detection
    DEFAULT_PATTERNS: list[tuple[str, str]] = [
        # === Email ===
        (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[REDACTED_EMAIL]"),
        # === Credit Card Numbers ===
        # Visa, MasterCard, Amex, Discover (with or without separators)
        (r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b", "[REDACTED_CC]"),
        (r"\b\d{4}[-\s]?\d{6}[-\s]?\d{5}\b", "[REDACTED_CC]"),  # Amex format
        # === SSN ===
        (r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b", "[REDACTED_SSN]"),
        # === Phone Numbers (International) ===
        # US format: (123) 456-7890, 123-456-7890, 123.456.7890
        (r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", "[REDACTED_PHONE]"),
        # International with country code: +1-123-456-7890, +44 20 7123 4567
        (r"\+\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}", "[REDACTED_PHONE]"),
        # E.164 format: +14155551234
        (r"\+\d{10,15}\b", "[REDACTED_PHONE]"),
        # === IP Addresses ===
        # IPv4: 192.168.1.1
        (r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b", "[REDACTED_IP]"),
        # IPv6 (simplified - catches most common formats)
        (r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b", "[REDACTED_IP]"),
        (r"\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b", "[REDACTED_IP]"),
        (r"\b::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}\b", "[REDACTED_IP]"),
        # === API Keys / Tokens (Generic) ===
        (r"\b(sk|pk|api|key|token|secret)[-_]?[a-zA-Z0-9]{20,}\b", "[REDACTED_KEY]"),
        # Bearer tokens
        (r"Bearer\s+[a-zA-Z0-9._-]+", "[REDACTED_BEARER]"),
        # === AWS Credentials ===
        # AWS Access Key ID: AKIA... (20 chars)
        (r"\b(AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}\b", "[REDACTED_AWS_KEY]"),
        # AWS Secret Access Key (40 char base64-like)
        (r"\b[A-Za-z0-9/+=]{40}\b", "[REDACTED_AWS_SECRET]"),
        # AWS ARN
        (r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d*:[a-zA-Z0-9/_-]+", "[REDACTED_AWS_ARN]"),
        # === GCP Credentials ===
        # GCP API Key (39 chars, AIza prefix)
        (r"\bAIza[A-Za-z0-9_-]{35}\b", "[REDACTED_GCP_KEY]"),
        # GCP Service Account JSON key ID
        (r'"private_key_id":\s*"[a-f0-9]{40}"', '"private_key_id": "[REDACTED]"'),
        # GCP OAuth client secret
        (r'"client_secret":\s*"[A-Za-z0-9_-]+"', '"client_secret": "[REDACTED]"'),
        # === JWT Tokens ===
        # JWT format: xxxxx.yyyyy.zzzzz (base64url segments)
        (r"\beyJ[A-Za-z0-9_-]*\.eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\b", "[REDACTED_JWT]"),
        # === GitHub/GitLab Tokens ===
        # GitHub Personal Access Token (ghp_, gho_, ghu_, ghs_, ghr_)
        (r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b", "[REDACTED_GITHUB_TOKEN]"),
        # GitLab tokens (glpat-)
        (r"\bglpat-[A-Za-z0-9_-]{20,}\b", "[REDACTED_GITLAB_TOKEN]"),
        # === Slack Tokens ===
        (r"\bxox[baprs]-[A-Za-z0-9-]+\b", "[REDACTED_SLACK_TOKEN]"),
        # === Stripe Keys ===
        (r"\b(sk|pk|rk)_(test|live)_[A-Za-z0-9]{24,}\b", "[REDACTED_STRIPE_KEY]"),
        # === Generic Base64 secrets (long encoded strings) ===
        # Catches many encoded secrets - be careful, may have false positives
        (r"\b[A-Za-z0-9+/]{64,}={0,2}\b", "[REDACTED_BASE64]"),
        # === Password in URLs ===
        (r"://[^:]+:[^@]+@", "://[REDACTED]:[REDACTED]@"),
    ]

    def __init__(
        self,
        enabled: bool = True,
        patterns: list[tuple[str, str]] | None = None,
        redact_keys: set[str] | None = None,
    ):
        """
        Initialize the redactor.

        Args:
            enabled: Whether redaction is active
            patterns: Regex patterns as (pattern, replacement) tuples
            redact_keys: Set of dictionary keys whose values should always be redacted
        """
        self.enabled = enabled
        self.patterns = patterns or self.DEFAULT_PATTERNS.copy()
        self.redact_keys = redact_keys or {
            # Authentication
            "password",
            "passwd",
            "pwd",
            "secret",
            "token",
            "access_token",
            "refresh_token",
            "id_token",
            "api_key",
            "apikey",
            "api_secret",
            "auth",
            "authorization",
            "credential",
            "credentials",
            "private_key",
            "private_key_id",
            "client_secret",
            # PII
            "ssn",
            "social_security",
            "credit_card",
            "card_number",
            "cvv",
            "cvc",
            "phone",
            "phone_number",
            "mobile",
            "email",
            "address",
            "dob",
            "date_of_birth",
            "bank_account",
            "routing_number",
            # Cloud credentials
            "aws_access_key",
            "aws_secret_key",
            "aws_session_token",
            "gcp_key",
            "azure_key",
            "connection_string",
            # Session/cookies
            "session_id",
            "session_key",
            "cookie",
            "csrf_token",
        }
        # Compile patterns for performance
        self._compiled = [(re.compile(p, re.IGNORECASE), r) for p, r in self.patterns]

    def redact_string(self, value: str) -> str:
        """
        Redact PII/secrets from a string value.

        Args:
            value: String to redact

        Returns:
            String with sensitive data replaced
        """
        if not self.enabled:
            return value

        result = value
        for pattern, replacement in self._compiled:
            result = pattern.sub(replacement, result)
        return result

    def redact(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Redact PII/secrets from a dictionary payload.

        Args:
            data: Dictionary to redact (not modified in place)

        Returns:
            New dictionary with sensitive data redacted
        """
        if not self.enabled:
            return data

        return self._redact_recursive(data)

    def _redact_recursive(self, obj: Any) -> Any:
        """Recursively redact sensitive data from any object."""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                # Check if key itself indicates sensitive data
                if key.lower() in self.redact_keys:
                    result[key] = "[REDACTED]"
                else:
                    result[key] = self._redact_recursive(value)
            return result
        elif isinstance(obj, list):
            return [self._redact_recursive(item) for item in obj]
        elif isinstance(obj, str):
            return self.redact_string(obj)
        else:
            return obj


class AuditLogger:
    """
    Persistent JSONL audit logger for events.

    Writes events to a JSONL file for compliance and debugging.
    Optionally applies PII redaction before writing.
    Supports log rotation to prevent disk exhaustion.
    """

    def __init__(
        self,
        log_path: Path | None = None,
        enabled: bool = True,
        redactor: Redactor | None = None,
        max_size_bytes: int | None = None,
        max_files: int = 5,
    ):
        """
        Initialize the audit logger.

        Args:
            log_path: Path to JSONL audit log file
            enabled: Whether audit logging is active
            redactor: Optional Redactor for PII scrubbing
            max_size_bytes: Max size per log file before rotation (None = no rotation)
            max_files: Number of rotated log files to keep (default: 5)
        """
        self.log_path = log_path
        self.enabled = enabled
        self.redactor = redactor or Redactor(enabled=False)  # Default: no redaction
        self.max_size_bytes = max_size_bytes
        self.max_files = max_files
        self._write_count = 0  # Check rotation every N writes for performance
        self._rotation_check_interval = 100

        # Create parent directory if needed
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _should_rotate(self) -> bool:
        """Check if log file should be rotated."""
        if not self.max_size_bytes or not self.log_path or not self.log_path.exists():
            return False
        try:
            return self.log_path.stat().st_size >= self.max_size_bytes
        except OSError:
            return False

    def _rotate_log(self) -> None:
        """Rotate the log file, keeping max_files backups."""
        if not self.log_path or not self.log_path.exists():
            return

        try:
            # Remove oldest backup if at limit
            oldest = self.log_path.with_suffix(f".{self.max_files}.jsonl")
            if oldest.exists():
                oldest.unlink()

            # Shift existing backups: .4 -> .5, .3 -> .4, etc.
            for i in range(self.max_files - 1, 0, -1):
                old_path = self.log_path.with_suffix(f".{i}.jsonl")
                new_path = self.log_path.with_suffix(f".{i + 1}.jsonl")
                if old_path.exists():
                    old_path.rename(new_path)

            # Move current log to .1
            backup_path = self.log_path.with_suffix(".1.jsonl")
            self.log_path.rename(backup_path)

        except Exception as e:  # nosec - rotation failure should not crash
            logger.warning(f"Log rotation failed: {e}")

    def log_event(self, event: Event) -> None:
        """
        Log an event to the audit file.

        Args:
            event: Event to log
        """
        if not self.enabled or not self.log_path:
            return

        try:
            # Check rotation periodically (not every write for performance)
            self._write_count += 1
            if self._write_count >= self._rotation_check_interval:
                self._write_count = 0
                if self._should_rotate():
                    self._rotate_log()

            # Apply redaction if enabled
            if self.redactor.enabled:
                # Create a new event with redacted payload
                redacted_payload = self.redactor.redact(event.payload)
                log_data = {
                    "event_type": event.event_type,
                    "payload": redacted_payload,
                    "timestamp": event.timestamp,
                    "event_id": event.event_id,
                    "source": event.source,
                    "correlation_id": event.correlation_id,
                }
                line = json.dumps(log_data, separators=(",", ":"))
            else:
                line = event.to_jsonl()

            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

        except Exception as e:  # nosec - logging failure should not crash application
            logger.warning(f"Failed to log event {event.event_id}: {e}")

    def read_events(
        self,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """
        Read events from audit log with optional filtering.

        Args:
            event_type: Filter by event type
            since: Only events after this timestamp
            limit: Maximum number of events to return

        Returns:
            List of events matching criteria (most recent first)
        """
        if not self.log_path or not self.log_path.exists():
            return []

        events: list[Event] = []

        try:
            with open(self.log_path, encoding="utf-8") as f:
                lines = f.readlines()

            # Process most recent events first
            for line in reversed(lines[-1000:]):  # Limit to last 1000 lines
                if not line.strip():
                    continue

                try:
                    event = Event.from_jsonl(line)

                    # Apply filters
                    if event_type and event.event_type != event_type:
                        continue
                    if since and event.timestamp < since:
                        continue

                    events.append(event)

                    if len(events) >= limit:
                        break

                except json.JSONDecodeError:
                    continue  # Skip malformed lines

        except Exception as e:  # nosec - reading audit log must not crash callers
            logger.warning(f"Failed to read event log: {e}")

        return events

    def clear(self) -> bool:
        """
        Clear the audit log.

        Returns:
            True if log was cleared successfully
        """
        if not self.log_path:
            return False

        try:
            if self.log_path.exists():
                self.log_path.unlink()
            return True
        except Exception:  # nosec - failure to clear log should not raise
            return False

    def get_stats(self) -> dict[str, Any]:
        """
        Get audit log statistics.

        Returns:
            Dictionary with log statistics
        """
        if not self.log_path or not self.log_path.exists():
            return {
                "total_events_logged": 0,
                "log_file_size": 0,
            }

        try:
            with open(self.log_path, encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
            return {
                "total_events_logged": line_count,
                "log_file_size": self.log_path.stat().st_size,
            }
        except Exception:  # nosec - best-effort metrics only
            return {
                "total_events_logged": "unknown",
                "log_file_size": "unknown",
            }
