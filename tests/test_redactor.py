"""
Tests for the Redactor (PII safety) component.
"""

from __future__ import annotations

from aperion_event_bus.audit import Redactor


class TestRedactor:
    """Test PII and secret redaction."""

    def test_redactor_disabled(self):
        """When disabled, redactor returns data unchanged."""
        redactor = Redactor(enabled=False)
        data = {"email": "test@example.com", "password": "secret123"}
        result = redactor.redact(data)
        assert result == data

    def test_redact_email_addresses(self):
        """Redactor removes email addresses from strings."""
        redactor = Redactor(enabled=True)
        result = redactor.redact_string("Contact me at user@example.com please")
        assert "user@example.com" not in result
        assert "[REDACTED_EMAIL]" in result

    def test_redact_sensitive_keys(self):
        """Redactor blanks values of sensitive keys."""
        redactor = Redactor(enabled=True)
        data = {
            "username": "alice",
            "password": "supersecret",
            "api_key": "sk-12345678901234567890",
            "token": "jwt.token.here",
        }
        result = redactor.redact(data)

        assert result["username"] == "alice"  # Not a sensitive key
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["token"] == "[REDACTED]"

    def test_redact_nested_data(self):
        """Redactor handles nested dictionaries."""
        redactor = Redactor(enabled=True)
        data = {
            "user": {
                "name": "Alice",
                "auth_info": {
                    "password": "secret",
                    "contact_email": "alice@example.com",
                },
            }
        }
        result = redactor.redact(data)

        assert result["user"]["name"] == "Alice"
        assert result["user"]["auth_info"]["password"] == "[REDACTED]"
        # Email in value (not key) should be pattern-matched
        assert "[REDACTED_EMAIL]" in result["user"]["auth_info"]["contact_email"]

    def test_redact_lists(self):
        """Redactor handles lists."""
        redactor = Redactor(enabled=True)
        data = {
            "emails": ["user1@example.com", "user2@example.com"],
            "tokens": ["token1", "token2"],
        }
        result = redactor.redact(data)

        assert all("[REDACTED_EMAIL]" in email for email in result["emails"])

    def test_custom_patterns(self):
        """Redactor supports custom patterns."""
        custom_patterns = [
            (r"CUSTOM-\d{4}", "[CUSTOM_REDACTED]"),
        ]
        redactor = Redactor(enabled=True, patterns=custom_patterns)

        result = redactor.redact_string("Code: CUSTOM-1234")
        assert "CUSTOM-1234" not in result
        assert "[CUSTOM_REDACTED]" in result

    def test_custom_redact_keys(self):
        """Redactor supports custom sensitive keys."""
        redactor = Redactor(enabled=True, redact_keys={"my_secret_field", "private_data"})
        data = {
            "public": "visible",
            "my_secret_field": "hidden",
            "private_data": "also hidden",
        }
        result = redactor.redact(data)

        assert result["public"] == "visible"
        assert result["my_secret_field"] == "[REDACTED]"
        assert result["private_data"] == "[REDACTED]"


class TestEnhancedPIIPatterns:
    """Tests for enhanced PII detection patterns (Milestone 3)."""

    def test_redact_phone_numbers_us(self):
        """Redact US phone number formats."""
        redactor = Redactor(enabled=True)

        # Various US formats
        test_cases = [
            "Call me at (123) 456-7890",
            "Phone: 123-456-7890",
            "Contact: 123.456.7890",
        ]
        for text in test_cases:
            result = redactor.redact_string(text)
            assert "[REDACTED_PHONE]" in result, f"Failed for: {text}"

    def test_redact_phone_numbers_international(self):
        """Redact international phone numbers."""
        redactor = Redactor(enabled=True)

        test_cases = [
            "UK: +44 20 7123 4567",
            "US: +1-123-456-7890",
            "E164: +14155551234",
        ]
        for text in test_cases:
            result = redactor.redact_string(text)
            assert "[REDACTED_PHONE]" in result, f"Failed for: {text}"

    def test_redact_ipv4_addresses(self):
        """Redact IPv4 addresses."""
        redactor = Redactor(enabled=True)

        test_cases = [
            "Server IP: 192.168.1.1",
            "Connect to 10.0.0.1:8080",
            "Public IP: 8.8.8.8",
        ]
        for text in test_cases:
            result = redactor.redact_string(text)
            assert "[REDACTED_IP]" in result, f"Failed for: {text}"

    def test_redact_ipv6_addresses(self):
        """Redact IPv6 addresses."""
        redactor = Redactor(enabled=True)

        result = redactor.redact_string("IPv6: 2001:0db8:85a3:0000:0000:8a2e:0370:7334")
        assert "[REDACTED_IP]" in result

    def test_redact_aws_access_key(self):
        """Redact AWS access key IDs."""
        redactor = Redactor(enabled=True)

        result = redactor.redact_string("AWS Key: AKIAIOSFODNN7EXAMPLE")
        assert "[REDACTED_AWS_KEY]" in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_redact_gcp_api_key(self):
        """Redact GCP API keys."""
        redactor = Redactor(enabled=True)

        # GCP API keys start with AIza and are 39 chars
        result = redactor.redact_string("GCP Key: AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe")
        assert "[REDACTED_GCP_KEY]" in result

    def test_redact_jwt_tokens(self):
        """Redact JWT tokens."""
        redactor = Redactor(enabled=True)

        # Real JWT format (header.payload.signature, all base64url)
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = redactor.redact_string(f"Token: {jwt}")
        assert "[REDACTED_JWT]" in result
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result

    def test_redact_github_tokens(self):
        """Redact GitHub personal access tokens."""
        redactor = Redactor(enabled=True)

        result = redactor.redact_string("Token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        assert "[REDACTED_GITHUB_TOKEN]" in result

    def test_redact_stripe_keys(self):
        """Redact Stripe API keys."""
        redactor = Redactor(enabled=True)

        test_cases = [
            "sk_test_" + "FAKE" * 6,  # nosec: synthetic test value
            "pk_live_" + "FAKE" * 6,  # nosec: synthetic test value
        ]
        for key in test_cases:
            result = redactor.redact_string(f"Stripe: {key}")
            assert "[REDACTED_STRIPE_KEY]" in result, f"Failed for: {key}"

    def test_redact_password_in_url(self):
        """Redact credentials in URLs."""
        redactor = Redactor(enabled=True)

        result = redactor.redact_string("postgres://user:password123@localhost:5432/db")
        assert "password123" not in result
        assert "[REDACTED]" in result

    def test_redact_bearer_token(self):
        """Redact Bearer tokens."""
        redactor = Redactor(enabled=True)

        result = redactor.redact_string("Authorization: Bearer abc123.xyz.789")
        assert "[REDACTED_BEARER]" in result
        assert "abc123" not in result

    def test_expanded_sensitive_keys(self):
        """Test expanded sensitive key detection."""
        redactor = Redactor(enabled=True)

        data = {
            "access_token": "token123",
            "refresh_token": "refresh456",
            "client_secret": "secret789",
            "aws_secret_key": "awskey",
            "connection_string": "Server=x;Password=y",
            "phone_number": "+1234567890",
            "date_of_birth": "1990-01-01",
            "bank_account": "1234567890",
        }
        result = redactor.redact(data)

        for key in data:
            assert result[key] == "[REDACTED]", f"Key {key} should be redacted"

    def test_credit_card_amex(self):
        """Redact Amex credit card format."""
        redactor = Redactor(enabled=True)

        result = redactor.redact_string("Amex: 3782 822463 10005")
        assert "[REDACTED_CC]" in result
