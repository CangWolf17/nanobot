"""Helpers for extracting and trimming terminal Key Principle blocks."""

from __future__ import annotations

import re

_TERMINAL_KEY_PRINCIPLE_PATTERNS = (
    re.compile(
        r"(?is)(?P<body>.+?)\n+"
        r"(?P<kp>\*\*\s*Key Principle\s*(?:[:：]\s*\*\*|\*\*\s*[:：]|[:：])\s*.+?)\s*$"
    ),
    re.compile(
        r"(?is)(?P<body>.+?)\n+"
        r"(?P<kp>Key Principle\s*[:：]\s*.+?)\s*$"
    ),
)
_NOTICE_KEY_PRINCIPLE_PREFIX_RE = re.compile(
    r"^\s*(?:\*\*\s*)?(?:key\s*principle|kp)(?:\s*\*\*)?\s*[:：]?\s*",
    re.IGNORECASE,
)
_NOTICE_SURROUNDING_BOLD_RE = re.compile(r"^\*\*(?P<body>.*?)\*\*$", re.DOTALL)


def extract_terminal_key_principle(text: str) -> tuple[str, str | None]:
    source = str(text or "")
    for pattern in _TERMINAL_KEY_PRINCIPLE_PATTERNS:
        match = pattern.search(source)
        if not match:
            continue
        body = str(match.group("body") or "").rstrip()
        kp = str(match.group("kp") or "").strip()
        if kp:
            return body, kp
    return source, None


def trim_terminal_key_principle(text: str, terminal_key_principle: str | None = None) -> str:
    source = str(text or "")
    body, extracted = extract_terminal_key_principle(source)
    if extracted:
        return body

    kp = str(terminal_key_principle or "").strip()
    if not kp:
        return source
    stripped = source.rstrip()
    if stripped.endswith(kp):
        return stripped[: -len(kp)].rstrip()
    return source


def normalize_key_principle_notice_text(text: str) -> str:
    source = str(text or "").strip()
    if not source:
        return ""

    unwrapped = source
    wrapped_match = _NOTICE_SURROUNDING_BOLD_RE.match(source)
    if wrapped_match:
        unwrapped = str(wrapped_match.group("body") or "").strip()

    normalized = _NOTICE_KEY_PRINCIPLE_PREFIX_RE.sub("", unwrapped, count=1).strip()
    normalized = normalized.strip("*").strip()
    return normalized or source
