# PII Detection Patterns for Audit Logs

> GDPR-compliant regex patterns for Constitution D4 / B2 enforcement

## Current Implementation Status

The `Redactor` class in `src/event_bus/audit.py` has basic patterns. This document provides enhanced patterns for production use.

## Enhanced Regex Patterns

### Email Addresses

```python
# Current (basic)
r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

# Enhanced (RFC 5322 compliant, handles edge cases)
r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
```

### Phone Numbers

```python
# US format
r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b"

# International (basic)
r"\b\+?[1-9]\d{1,14}\b"

# UK format
r"\b(?:0|\+?44)(?:\d\s?){9,10}\b"
```

### Social Security Numbers

```python
# US SSN
r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"

# With validation (excludes invalid ranges)
r"\b(?!000|666|9\d{2})\d{3}[-\s]?(?!00)\d{2}[-\s]?(?!0000)\d{4}\b"
```

### Credit Card Numbers

```python
# Generic (13-19 digits with optional separators)
r"\b(?:\d[-\s]?){13,19}\b"

# Visa
r"\b4[0-9]{12}(?:[0-9]{3})?\b"

# Mastercard
r"\b(?:5[1-5][0-9]{2}|222[1-9]|22[3-9][0-9]|2[3-6][0-9]{2}|27[01][0-9]|2720)[0-9]{12}\b"

# American Express
r"\b3[47][0-9]{13}\b"
```

### IP Addresses

```python
# IPv4
r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"

# IPv6 (simplified)
r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
```

### API Keys & Secrets

```python
# AWS Access Key
r"\bAKIA[0-9A-Z]{16}\b"

# AWS Secret Key
r"\b[A-Za-z0-9/+=]{40}\b"  # Too broad alone, use with context

# GitHub Token
r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b"

# Generic API Key patterns
r"\b(?:api[_-]?key|apikey|api_secret|access_token)\s*[:=]\s*['\"]?([a-zA-Z0-9_-]{20,})['\"]?"

# Bearer tokens
r"\bBearer\s+[a-zA-Z0-9._-]+\b"

# JWT tokens
r"\beyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*\b"
```

### Passwords in Logs

```python
# Password in key-value contexts
r"(?i)password\s*[:=]\s*['\"]?([^'\"\\s]+)['\"]?"

# Secret in key-value contexts
r"(?i)(?:secret|passwd|pwd)\s*[:=]\s*['\"]?([^'\"\\s]+)['\"]?"
```

### Date of Birth

```python
# Various date formats (potential PII when combined with other data)
r"\b(?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])[-/](?:19|20)\d{2}\b"
r"\b(?:19|20)\d{2}[-/](?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])\b"
```

## Complete Redactor Configuration

```python
PRODUCTION_PII_PATTERNS: list[tuple[str, str]] = [
    # Email
    (r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[REDACTED_EMAIL]"),
    
    # Credit cards
    (r"\b(?:\d[-\s]?){13,19}\b", "[REDACTED_CC]"),
    
    # SSN
    (r"\b(?!000|666|9\d{2})\d{3}[-\s]?(?!00)\d{2}[-\s]?(?!0000)\d{4}\b", "[REDACTED_SSN]"),
    
    # Phone (US)
    (r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b", "[REDACTED_PHONE]"),
    
    # IPv4
    (r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b", "[REDACTED_IP]"),
    
    # AWS keys
    (r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]"),
    
    # GitHub tokens
    (r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b", "[REDACTED_GH_TOKEN]"),
    
    # JWT
    (r"\beyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*\b", "[REDACTED_JWT]"),
    
    # Bearer tokens
    (r"\bBearer\s+[a-zA-Z0-9._-]+\b", "[REDACTED_BEARER]"),
]

SENSITIVE_KEYS: set[str] = {
    "password", "passwd", "pwd", "secret", "token",
    "api_key", "apikey", "api_secret", "access_token",
    "refresh_token", "private_key", "secret_key",
    "ssn", "social_security", "credit_card", "cc_number",
    "auth", "authorization", "credential", "credentials",
}
```

## Usage Example

```python
from aperion_event_bus import EventBus, Redactor

redactor = Redactor(
    enabled=True,
    patterns=PRODUCTION_PII_PATTERNS,
    redact_keys=SENSITIVE_KEYS,
)

bus = EventBus(
    enable_audit=True,
    event_log_path=Path("events.jsonl"),
    redactor=redactor,
)

# This will be redacted in audit log
bus.emit("user.signup", {
    "username": "alice",
    "email": "alice@example.com",  # → [REDACTED_EMAIL]
    "password": "secret123",       # → [REDACTED] (key-based)
    "ip_address": "192.168.1.1",   # → [REDACTED_IP]
})
```

## Limitations of Regex-Only Approach

1. **Names** - Cannot reliably detect without NLP/NER
2. **Addresses** - Too variable for regex
3. **Context** - "John" in `user.name` vs `product.name`
4. **False Positives** - Some patterns match non-PII

## Recommended Libraries

| Library | Approach | Notes |
|---------|----------|-------|
| [OpenRedaction](https://openredaction.com/) | 500+ regex + AI | Most comprehensive |
| [piisa/pii-extract-plg-regex](https://github.com/piisa/pii-extract-plg-regex) | Language-aware regex | Good for i18n |
| [RedactPII](https://github.com/wrannaman/redact-pii-python) | Zero-dependency regex | Minimal footprint |
| [presidio](https://github.com/microsoft/presidio) | ML + regex | Microsoft, heavy |

## GDPR Compliance Notes

1. **Article 30** - Log what data is processed (audit trail)
2. **Article 17** - Right to erasure (must be able to delete)
3. **Article 32** - Security (encrypt at rest if logging PII)
4. **Pseudonymization** - Consider hashing instead of full redaction for analytics

## Testing Redaction

```python
def test_email_redaction():
    redactor = Redactor(patterns=PRODUCTION_PII_PATTERNS)
    
    text = "Contact support@company.com for help"
    result = redactor.redact_string(text)
    
    assert "support@company.com" not in result
    assert "[REDACTED_EMAIL]" in result

def test_no_false_positives():
    redactor = Redactor(patterns=PRODUCTION_PII_PATTERNS)
    
    # Should NOT be redacted
    safe_texts = [
        "Order #12345",
        "Version 2.3.4",
        "Product SKU ABC123",
    ]
    
    for text in safe_texts:
        assert redactor.redact_string(text) == text
```

## Sources

- [OpenRedaction Documentation](https://openredaction.com/docs)
- [piisa PII Regex Plugin](https://github.com/piisa/pii-extract-plg-regex)
- [BigCode PII Detection](https://deepwiki.com/bigcode-project/bigcode-dataset/3.3-regex-based-pii-detection)
- [GDPR Safe RAG](https://dev.to/charles_nwankpa/introducing-gdpr-safe-rag-build-gdpr-compliant-rag-systems-in-minutes-4ap4)
