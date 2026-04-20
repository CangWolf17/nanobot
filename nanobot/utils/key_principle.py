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
