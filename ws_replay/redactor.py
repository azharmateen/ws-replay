"""Redact sensitive data from WebSocket sessions with consistent replacements."""

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

# Built-in redaction patterns
BUILTIN_PATTERNS = {
    "jwt": r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "bearer": r"Bearer\s+[A-Za-z0-9_\-\.]+",
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "api_key": r"(?:api[_-]?key|apikey|token|secret)[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{16,})[\"']?",
    "uuid": r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    "ip_address": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    "phone": r"\b\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    "credit_card": r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "password_field": r"(?:password|passwd|pwd)[\"']?\s*[:=]\s*[\"']?([^\s\"',}]+)[\"']?",
}


class ConsistentRedactor:
    """
    Redact sensitive data with consistent replacements.
    Same input value always produces the same fake replacement.
    """

    def __init__(self, seed: str = "ws-replay-redact"):
        self.seed = seed
        self._cache: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def _make_replacement(self, match: str, pattern_name: str) -> str:
        """Generate a consistent replacement for a matched value."""
        cache_key = f"{pattern_name}:{match}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Generate deterministic hash
        h = hashlib.sha256(f"{self.seed}:{cache_key}".encode()).hexdigest()

        if pattern_name not in self._counters:
            self._counters[pattern_name] = 0
        self._counters[pattern_name] += 1
        idx = self._counters[pattern_name]

        replacements = {
            "jwt": f"eyJREDACTED.{h[:20]}.REDACTED{idx}",
            "bearer": f"Bearer REDACTED_{h[:12]}",
            "email": f"user{idx}@redacted.example.com",
            "api_key": f"REDACTED_KEY_{h[:16]}",
            "uuid": f"{h[:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:32]}",
            "ip_address": f"10.0.{idx // 256}.{idx % 256}",
            "phone": f"+1-555-000-{idx:04d}",
            "credit_card": f"4111-1111-1111-{idx:04d}",
            "ssn": f"000-00-{idx:04d}",
            "password_field": f"REDACTED_PASS_{idx}",
        }

        replacement = replacements.get(pattern_name, f"REDACTED_{h[:8]}")
        self._cache[cache_key] = replacement
        return replacement

    def redact_text(self, text: str, patterns: Optional[dict[str, str]] = None) -> str:
        """Redact sensitive data from a text string."""
        if patterns is None:
            patterns = BUILTIN_PATTERNS

        result = text
        for name, pattern in patterns.items():
            def replacer(m, _name=name):
                return self._make_replacement(m.group(0), _name)
            result = re.sub(pattern, replacer, result, flags=re.IGNORECASE)

        return result

    def redact_json_text(self, text: str, patterns: Optional[dict[str, str]] = None) -> str:
        """Redact sensitive data, with JSON-awareness for structured payloads."""
        # Try to parse as JSON, redact values, re-serialize
        try:
            data = json.loads(text)
            redacted = self._redact_json_value(data, patterns)
            return json.dumps(redacted, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            # Fall back to plain text redaction
            return self.redact_text(text, patterns)

    def _redact_json_value(self, value, patterns: Optional[dict[str, str]] = None):
        """Recursively redact JSON values."""
        if isinstance(value, str):
            return self.redact_text(value, patterns)
        elif isinstance(value, dict):
            return {k: self._redact_json_value(v, patterns) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._redact_json_value(item, patterns) for item in value]
        return value


def redact_session(
    input_path: str,
    output_path: str,
    patterns: Optional[dict[str, str]] = None,
    extra_patterns: Optional[dict[str, str]] = None,
    seed: str = "ws-replay-redact",
) -> dict:
    """
    Redact a session file and write the result.

    Args:
        input_path: Path to input .wslog file
        output_path: Path to output redacted .wslog file
        patterns: Override default patterns (None = use builtins)
        extra_patterns: Additional patterns to add to defaults
        seed: Seed for consistent replacements

    Returns:
        dict with redaction statistics
    """
    active_patterns = dict(patterns or BUILTIN_PATTERNS)
    if extra_patterns:
        active_patterns.update(extra_patterns)

    redactor = ConsistentRedactor(seed=seed)
    stats = {"frames_processed": 0, "frames_modified": 0, "total_redactions": 0}

    with open(input_path) as fin, open(output_path, "w") as fout:
        for i, line in enumerate(fin):
            line = line.strip()
            if not line:
                continue

            data = json.loads(line)

            if data.get("_type") == "session_header":
                # Redact target URL if it contains sensitive params
                if "target_url" in data:
                    data["target_url"] = redactor.redact_text(data["target_url"], active_patterns)
                data["redacted"] = True
                fout.write(json.dumps(data) + "\n")
                continue

            stats["frames_processed"] += 1
            original_payload = data.get("payload", "")

            if data.get("payload_type") == "text":
                redacted_payload = redactor.redact_json_text(original_payload, active_patterns)
            else:
                # Skip binary payloads
                redacted_payload = original_payload

            if redacted_payload != original_payload:
                stats["frames_modified"] += 1
                # Count approximate redactions
                diff_count = sum(
                    1 for a, b in zip(original_payload, redacted_payload) if a != b
                ) // 5 + (1 if redacted_payload != original_payload else 0)
                stats["total_redactions"] += max(1, diff_count)

            data["payload"] = redacted_payload
            fout.write(json.dumps(data) + "\n")

    return stats
