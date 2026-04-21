"""Conservative semantic skill-routing hints for plain user prompts.

This is intentionally advisory-only: it never auto-executes workflows or
rewrites the user's message. It only suggests relevant skills/context when the
match is high-confidence enough.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SPECIAL_ALIAS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "session-workflows": ("notes", "note", "summary", "insight", "笔记", "小结", "感悟"),
    "self-improving-lite": (
        "diagnose",
        "diagnosis",
        "debug",
        "root cause",
        "问题原因",
        "原因分析",
        "诊断",
        "排查",
    ),
}

ASCII_STOPWORDS = {"skill", "skills", "tool", "tools"}


def _contains_cjk(text: str) -> bool:
    return any(ord(ch) > 127 for ch in text)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _normalize_keyword(value: Any) -> str:
    return _normalize_text(str(value or ""))


def _word_boundary_match(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def _text_match(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    if _contains_cjk(needle):
        return needle in haystack
    return _word_boundary_match(haystack, needle)


def _skill_name_variants(name: str) -> list[str]:
    normalized = _normalize_text(name)
    variants = {
        normalized,
        normalized.replace("-", " "),
        normalized.replace("_", " "),
        normalized.replace("-", ""),
        normalized.replace("_", ""),
    }
    return [item for item in variants if item]


class SemanticSkillRouter:
    """Read the workspace skill registry and derive lightweight prompt hints."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.registry_path = workspace_root / "skills" / "skill-map.json"

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        registry: dict[str, dict[str, Any]] = {}
        for raw_name, payload in data.items():
            if not isinstance(payload, dict):
                continue
            name = str(raw_name or "").strip()
            if not name:
                continue
            keywords = payload.get("keywords")
            if not isinstance(keywords, list):
                keywords = []
            registry[name] = {
                "path": str(payload.get("path") or "").strip(),
                "description": str(payload.get("description") or "").strip(),
                "keywords": [str(item or "").strip() for item in keywords if str(item or "").strip()],
            }
        return registry

    def _score_entry(self, text: str, name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        score = 0
        matched_terms: list[str] = []

        for variant in _skill_name_variants(name):
            if _text_match(text, variant):
                score += 3
                matched_terms.append(variant)
                break

        for alias in SPECIAL_ALIAS_KEYWORDS.get(name, ()):
            normalized = _normalize_keyword(alias)
            if normalized and _text_match(text, normalized):
                score += 2
                matched_terms.append(alias)

        keywords = payload.get("keywords") if isinstance(payload.get("keywords"), list) else []
        for raw_keyword in keywords:
            keyword = _normalize_keyword(raw_keyword)
            if not keyword or keyword in ASCII_STOPWORDS:
                continue
            if _text_match(text, keyword):
                score += 2 if _contains_cjk(keyword) else 1
                matched_terms.append(str(raw_keyword))

        unique_terms: list[str] = []
        seen: set[str] = set()
        for term in matched_terms:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            unique_terms.append(term)

        if score < 2:
            return None

        return {
            "skill": name,
            "path": str(payload.get("path") or "").strip(),
            "description": str(payload.get("description") or "").strip(),
            "matched_terms": unique_terms[:4],
            "score": score,
        }

    def route(self, raw_text: str, *, limit: int = 2) -> dict[str, Any] | None:
        text = _normalize_text(raw_text)
        if not text or text.startswith("/"):
            return None

        registry = self._load_registry()
        if not registry:
            return None

        matches: list[dict[str, Any]] = []
        for name, payload in registry.items():
            scored = self._score_entry(text, name, payload)
            if scored:
                matches.append(scored)

        if not matches:
            return None

        matches.sort(
            key=lambda item: (-int(item.get("score") or 0), -len(item.get("matched_terms") or []), str(item.get("skill") or "")),
        )
        top = matches[:limit]
        return {
            "mode": "direct_route",
            "preserve_explicit_commands": True,
            "advisory_only": True,
            "matches": [{k: v for k, v in item.items() if k != "score"} for item in top],
        }

