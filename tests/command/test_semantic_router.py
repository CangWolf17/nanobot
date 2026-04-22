from pathlib import Path

from nanobot.command.semantic_router import SemanticSkillRouter


def _write_registry(workspace: Path) -> None:
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "skill-map.json").write_text(
        """{
  "小结": {
    "path": "skills/session-workflows/SKILL.md",
    "keywords": ["summary", "小结"],
    "description": "Session-aware daily summary workflow."
  },
  "感悟": {
    "path": "skills/session-workflows/SKILL.md",
    "keywords": ["insight", "感悟"],
    "description": "Session-aware insight capture workflow."
  },
  "笔记": {
    "path": "skills/obsidian-notes/SKILL.md",
    "keywords": ["notes", "笔记"],
    "description": "Session-aware note authoring workflow."
  },
  "breadcrumb": {
    "path": "skills/breadcrumb/SKILL.md",
    "keywords": ["breadcrumb", "checkpoint", "context-note", "备忘", "记录要点", "留个痕"],
    "description": "Capture durable breadcrumbs."
  },
  "analyze": {
    "path": "skills/analyze/SKILL.md",
    "keywords": ["analyze", "diagnose", "debug", "root cause", "诊断", "排查"],
    "description": "Structured root-cause analysis workflow."
  },
  "lark-calendar": {
    "path": "skills/lark-calendar/SKILL.md",
    "keywords": ["lark calendar", "飞书日历", "agenda"],
    "description": "Lark calendar adapter skill."
  },
  "github": {
    "path": "skills/github/SKILL.md",
    "keywords": ["github"],
    "description": "GitHub CLI helper skill."
  },
  "lark-workflow-meeting-summary": {
    "path": "skills/lark-workflow-meeting-summary/SKILL.md",
    "keywords": ["整理会议纪要", "meeting summary report"],
    "description": "Meeting summary workflow."
  }
}""",
        encoding="utf-8",
    )


def test_semantic_router_routes_diagnose_state_to_analyze_skill(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("please diagnose this failure")

    assert routed is not None
    assert routed["mode"] == "direct_route"
    assert routed["advisory_only"] is True
    assert routed["matches"][0]["skill"] == "analyze"
    assert "diagnose" in [item.lower() for item in routed["matches"][0]["matched_terms"]]


def test_semantic_router_routes_summary_state_to_note_skill(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("give me a short summary of this thread")

    assert routed is not None
    assert routed["matches"][0]["skill"] == "小结"
    assert "summary" in [item.lower() for item in routed["matches"][0]["matched_terms"]]


def test_semantic_router_routes_insight_state_to_insight_workflow(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("share one insight from today")

    assert routed is not None
    assert routed["matches"][0]["skill"] == "感悟"
    assert "insight" in [item.lower() for item in routed["matches"][0]["matched_terms"]]


def test_semantic_router_routes_notes_state_to_notes_workflow(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("notes 给申论素材补一段")

    assert routed is not None
    assert routed["matches"][0]["skill"] == "笔记"
    assert "notes" in [item.lower() for item in routed["matches"][0]["matched_terms"]]


def test_semantic_router_routes_breadcrumb_state_to_breadcrumb_skill(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("留个痕：下次先检查 hook metadata")

    assert routed is not None
    assert routed["matches"][0]["skill"] == "breadcrumb"
    assert any(term in {"留个痕", "breadcrumb"} for term in routed["matches"][0]["matched_terms"])


def test_semantic_router_routes_explicit_lark_skill_name_conservatively(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("use lark calendar to check tomorrow")

    assert routed is not None
    assert [item["skill"] for item in routed["matches"]] == ["lark-calendar"]
    assert "lark calendar" in [term.lower() for term in routed["matches"][0]["matched_terms"]]


def test_semantic_router_routes_explicit_repo_skill_name_for_github(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("please use github to inspect this PR")

    assert routed is not None
    assert routed["matches"][0]["skill"] == "github"
    assert "github" in [term.lower() for term in routed["matches"][0]["matched_terms"]]


def test_semantic_router_routes_lark_meeting_summary_from_semantic_keyword(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("帮我整理会议纪要并给个总结")

    assert routed is not None
    assert routed["matches"][0]["skill"] == "lark-workflow-meeting-summary"
    assert any(term in {"整理会议纪要", "meeting summary report"} for term in routed["matches"][0]["matched_terms"])


def test_semantic_router_prefers_more_specific_keyword_when_scores_tie(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    skill_map = tmp_path / "skills" / "skill-map.json"
    skill_map.write_text(
        """{
  "lark-vc": {
    "path": "skills/lark-vc/SKILL.md",
    "keywords": ["会议纪要"],
    "description": "General meeting record skill."
  },
  "lark-workflow-meeting-summary": {
    "path": "skills/lark-workflow-meeting-summary/SKILL.md",
    "keywords": ["整理会议纪要"],
    "description": "Meeting summary workflow."
  }
}""",
        encoding="utf-8",
    )

    routed = SemanticSkillRouter(tmp_path).route("帮我整理会议纪要并给个总结")

    assert routed is not None
    assert [item["skill"] for item in routed["matches"]] == [
        "lark-workflow-meeting-summary",
        "lark-vc",
    ]


def test_semantic_router_skips_low_confidence_generic_prompt(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("check tomorrow calendar")

    assert routed is None


def test_semantic_router_no_longer_routes_doctor_install_issue_queries(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("I hit an installation issue during setup")

    assert routed is None


def test_semantic_router_preserves_explicit_slash_commands(tmp_path: Path) -> None:
    _write_registry(tmp_path)

    routed = SemanticSkillRouter(tmp_path).route("/diagnose this failure")

    assert routed is None
