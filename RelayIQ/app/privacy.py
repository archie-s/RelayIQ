"""Privacy gate: detect sensitive data before cloud escalation and require human approval
for non-emergency cases.
"""
from __future__ import annotations

import re

# Patterns are intentionally conservative for demo safety: better to flag than leak.
_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})\b"),
    "ssn_like": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card_like": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "iban_like": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "employee_id": re.compile(
        r"\b(?:employee\s*id|emp\s*id|staff\s*id)\s*[:#-]?\s*[A-Z0-9-]+\b",
        re.IGNORECASE,
    ),
    "account_number": re.compile(
        r"\b(?:account\s*number|acct\s*no\.?|routing\s*number)\s*[:#-]?\s*[A-Z0-9-]+\b",
        re.IGNORECASE,
    ),
}

_EMERGENCY_TERMS = (
    "emergency", "urgent", "critical", "ambulance", "bleeding", "chest pain",
    "not breathing", "collapse", "stroke", "overdose", "anaphylaxis", "seizure",
)


def _redact_prompt(prompt: str) -> str:
    redacted = prompt
    for name, pattern in _PATTERNS.items():
        redacted = pattern.sub(f"[REDACTED_{name.upper()}]", redacted)
    return redacted


def inspect_prompt(prompt: str, verdict: dict) -> dict:
    text = prompt.strip()
    low = text.lower()

    hits: list[str] = []
    for name, pattern in _PATTERNS.items():
        if pattern.search(text):
            hits.append(name)

    # Keyword hints for enterprise-sensitive context where explicit patterns may not match.
    if any(k in low for k in ("confidential", "internal memo", "customer list", "salary", "payroll", "proprietary")):
        hits.append("sensitive_context")

    hits = sorted(set(hits))
    sensitive = len(hits) > 0
    emergency_override = bool(verdict.get("urgency") == "critical" or any(t in low for t in _EMERGENCY_TERMS))

    requires_human_review = sensitive and verdict.get("route") == "cloud"
    redacted_prompt = _redact_prompt(text) if sensitive else text

    if not sensitive:
        reason = "No sensitive markers detected"
    elif emergency_override:
        reason = "Sensitive markers removed locally; immediate local guidance is available while cloud consent waits"
    else:
        reason = "Sensitive markers removed locally; approve the redacted preview before cloud send"

    return {
        "sensitive": sensitive,
        "types": hits,
        "requires_human_review": requires_human_review,
        "emergency_override": emergency_override,
        "reason": reason,
        "redacted_prompt": redacted_prompt,
    }
