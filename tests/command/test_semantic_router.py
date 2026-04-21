from pathlib import Path

from nanobot.command.semantic_router import SemanticSkillRouter


def _write_registry(workspace: Path) -> None:
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "skill-map.json").write_text(
        """{
  "session-workflows": {
    "path": "skills/session-workflows/SKILL.md",
    "keywords": ["notes", "insight", "summary", "笔记", "小结", "感悟"],
    "description": "Session-aware workflows for notes, summaries, and reflections."
  },
  "self-improving-lite": {
    "path": "skills/self-improving-lite/SKILL.md",
    "keywords": ["reflect", "improve"],
    "description": "Structured diagnosis and self-reflection workflow."
  },
  "lark-calendar": {
    "path": "skills/lark-calendar/SKILL.md",
    "keywords": ["lark", "calendar"],
    "description": "Lark calendar adapter skill."
  }
}""",
        encoding="utf-8",
    )


def test_semantic_router_routes_diagnose_state_to_self_improving_skill(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("please diagnose this failure")

    assert routed is not None
    assert routed["mode"] == "direct_route"
    assert routed["advisory_only"] is True
    assert routed["matches"][0]["skill"] == "self-improving-lite"
    assert "diagnose" in [item.lower() for item in routed["matches"][0]["matched_terms"]]


def test_semantic_router_routes_explicit_lark_skill_name_conservatively(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("use lark calendar to check tomorrow")

    assert routed is not None
    assert [item["skill"] for item in routed["matches"]] == ["lark-calendar"]
    assert set(term.lower() for term in routed["matches"][0]["matched_terms"]) >= {
        "lark",
        "calendar",
    }


def test_semantic_router_skips_low_confidence_generic_prompt(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("check tomorrow calendar")

    assert routed is None


def test_semantic_router_preserves_explicit_slash_commands(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("/diagnose this failure")

    assert routed is None
