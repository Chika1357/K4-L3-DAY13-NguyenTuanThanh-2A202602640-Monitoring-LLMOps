from __future__ import annotations

import hashlib
import re

# Order matters: longer digit sequences (card, CCCD) are replaced before the
# phone pattern so a fragment of a card number is never labelled as a phone.
PII_PATTERNS: dict[str, str] = {
    "email": r"[\w\.-]+@[\w\.-]+\.\w+",
    # Same separator between all groups, so "<12-digit CCCD> 4111 ..." is not
    # glued into one fake card match that leaves real card digits behind.
    "credit_card": r"\b\d{4}(?P<sep>[- ]?)\d{4}(?P=sep)\d{4}(?P=sep)\d{4}\b",
    "cccd": r"\b\d{12}\b",
    "phone_vn": r"(?<!\d)(?:\+84|0)(?:[ .-]?\d){9}(?!\d)",
    # Vietnamese passport: one uppercase letter followed by 7 digits, e.g. C1234567.
    "passport": r"\b[A-Z]\d{7}\b",
}


def scrub_text(text: str) -> str:
    safe = text
    for name, pattern in PII_PATTERNS.items():
        safe = re.sub(pattern, f"[REDACTED_{name.upper()}]", safe)
    return safe


def summarize_text(text: str, max_len: int = 80) -> str:
    safe = scrub_text(text).strip().replace("\n", " ")
    return safe[:max_len] + ("..." if len(safe) > max_len else "")


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
