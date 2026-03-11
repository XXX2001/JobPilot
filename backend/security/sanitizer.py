"""Centralized sanitization module for all LLM-facing code."""
from __future__ import annotations

import logging
import re

from backend.defaults import (
    MAX_LEN_ADDITIONAL_ANSWERS,
    MAX_LEN_APPLY_URL,
    MAX_LEN_COMPANY,
    MAX_LEN_DESCRIPTION,
    MAX_LEN_LOCATION,
    MAX_LEN_SALARY_TEXT,
    MAX_LEN_TITLE,
)

logger = logging.getLogger(__name__)

# ── Injection patterns (case-insensitive, line-level) ─────────────────────────
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous\s+)?instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(the\s+)?(above|previous)", re.IGNORECASE),
    re.compile(r"you are now\b", re.IGNORECASE),
    re.compile(r"new (role|instructions|task)\b", re.IGNORECASE),
    re.compile(r"system:\s*", re.IGNORECASE),
    re.compile(r"assistant:\s*", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"^\s*-{3,}\s*$", re.MULTILINE),
    re.compile(r"^\s*={3,}\s*$", re.MULTILINE),
    re.compile(r"^IMPORTANT:", re.MULTILINE),
    re.compile(r"^CRITICAL:", re.MULTILINE),
]

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_EXCESSIVE_WHITESPACE_RE = re.compile(r"[ \t]{3,}")


def sanitize_for_prompt(text: str, max_len: int, field_name: str = "") -> str:
    """Sanitize user-supplied text before inserting into an LLM prompt.

    Steps:
    1. Truncate to *max_len* characters.
    2. Strip control characters (\\x00-\\x08, \\x0b-\\x0c, \\x0e-\\x1f).
    3. Collapse excessive whitespace (3+ spaces/tabs → single space).
    4. Detect and strip lines matching known injection patterns.

    When an injection pattern is detected the matching line is removed and a
    warning is logged that includes *field_name* and the first 100 chars of the
    suspicious content.
    """
    if not isinstance(text, str):
        text = str(text)

    # 1. Truncate
    text = text[:max_len]

    # 2. Strip control characters
    text = _CONTROL_CHAR_RE.sub("", text)

    # 3. Collapse excessive inline whitespace
    text = _EXCESSIVE_WHITESPACE_RE.sub(" ", text)

    # 4. Strip injection patterns line-by-line
    lines = text.splitlines()
    clean_lines: list[str] = []
    for line in lines:
        matched = False
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(line):
                logger.warning(
                    "Injection pattern detected in field=%r: %r",
                    field_name,
                    line[:100],
                )
                matched = True
                break
        if not matched:
            clean_lines.append(line)

    return "\n".join(clean_lines)


def wrap_untrusted(text: str, label: str) -> str:
    """Wrap *text* in XML-style structural delimiters to isolate untrusted data."""
    return f'<untrusted_data label="{label}">\n{text}\n</untrusted_data>'


def sanitize_url(url: str, max_len: int = MAX_LEN_APPLY_URL) -> str:
    """Validate and sanitize a URL.

    Returns the sanitized URL string, or an empty string if invalid.
    - Only http:// and https:// schemes are permitted.
    - Newlines and control characters are stripped.
    - Truncated to *max_len* (returns empty string if too long).
    """
    if not isinstance(url, str):
        return ""
    # Strip newlines and control chars
    url = _CONTROL_CHAR_RE.sub("", url).replace("\n", "").replace("\r", "")
    url = url.strip()
    if len(url) > max_len:
        logger.warning("URL truncated from %d to %d chars", len(url), max_len)
        return ""
    if not url.startswith(("http://", "https://")):
        if url:
            logger.warning("Rejected URL with invalid scheme: %r", url[:100])
        return ""
    return url
