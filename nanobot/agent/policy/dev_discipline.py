"""Session-scoped dev discipline policy for nanobot runtime."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

WRITE_REDIRECT_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:echo|printf|cat|python|python3|node|ruby|perl|awk|sed)\b.*(?:>>?|\|\s*tee\b)",
    re.IGNORECASE,
)
GENERIC_REDIRECT_RE = re.compile(r"(^|[^0-9])>>?\s*[^&\s]", re.IGNORECASE)
MUTATING_SHELL_PATTERNS = [
    re.compile(r'\bsed\s+-i(?:[\s\'"]|$)', re.IGNORECASE),
    re.compile(r'\bperl\s+-p[iI](?:[\s\'"]|$)', re.IGNORECASE),
    re.compile(r"\bpython(?:3)?\s+-c\s+[\"\'].*\bopen\s*\([^\)]*,\s*[\"\'][wa+]", re.IGNORECASE),
    re.compile(r"\bnode\s+-e\s+[\"\'].*\bwriteFile(?:Sync)?\s*\(", re.IGNORECASE),
    re.compile(r"\bpython(?:3)?\s+-\s*<<", re.IGNORECASE),
    re.compile(
        r"\b(?:python|python3|node|ruby|perl|bash|sh|zsh)\b.*<<[\s\"\']*[A-Za-z_][A-Za-z0-9_\-]*",
        re.IGNORECASE,
    ),
    re.compile(r"\btee\b", re.IGNORECASE),
    re.compile(r"\btouch\b", re.IGNORECASE),
    re.compile(r"\bmkdir\b", re.IGNORECASE),
    re.compile(r"\bcp\b", re.IGNORECASE),
    re.compile(r"\bmv\b", re.IGNORECASE),
    re.compile(r"\bpatch\b", re.IGNORECASE),
    re.compile(r"\bgit\s+apply\b", re.IGNORECASE),
]
TEST_COMMAND_PATTERNS = [
    re.compile(r"\bpytest\b", re.IGNORECASE),
    re.compile(r"\bpython(?:3)?\s+-m\s+pytest\b", re.IGNORECASE),
    re.compile(r"\bgo\s+test\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+test\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+(?:run\s+)?test\b", re.IGNORECASE),
    re.compile(r"\bpnpm\s+(?:run\s+)?test\b", re.IGNORECASE),
    re.compile(r"\byarn\s+test\b", re.IGNORECASE),
    re.compile(r"\buv\s+run\s+pytest\b", re.IGNORECASE),
]
READONLY_COMMAND_PATTERNS = [
    re.compile(r"^\s*ls\b", re.IGNORECASE),
    re.compile(r"^\s*find\b", re.IGNORECASE),
    re.compile(r"^\s*(?:grep|rg)\b", re.IGNORECASE),
    re.compile(r"^\s*cat\b", re.IGNORECASE),
    re.compile(r"^\s*pwd\b", re.IGNORECASE),
    re.compile(r"^\s*git\s+(?:status|diff|show|log)\b", re.IGNORECASE),
    re.compile(r"^\s*echo\b(?!.*(?:>>?|\|\s*tee\b))", re.IGNORECASE),
]
BUILD_COMMAND_PATTERNS = [
    re.compile(
        r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:build|lint|typecheck|format)\b", re.IGNORECASE
    ),
    re.compile(r"\b(?:ruff|black|mypy|pyright|eslint|prettier|tsc)\b", re.IGNORECASE),
    re.compile(r"\b(?:cargo\s+build|go\s+build|python\s+-m\s+build)\b", re.IGNORECASE),
    re.compile(r"^\s*make(?:\s+[A-Za-z0-9_\-.:/]+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*just\s+[A-Za-z0-9_\-.:/]+\s*$", re.IGNORECASE),
    re.compile(r"^\s*task\s+[A-Za-z0-9_\-.:/]+\s*$", re.IGNORECASE),
]
SAFE_TASK_TARGET_RE = re.compile(
    r"^(?:test|tests|check|lint|build|compile|typecheck|verify|ci|unit|coverage|smoke|dev|start)$",
    re.IGNORECASE,
)
RISKY_TASK_TARGET_RE = re.compile(
    r"(?:fix|fmt|format|write|gen|generate|sync|apply|migrate|seed|bootstrap|release|deploy|publish|install)",
    re.IGNORECASE,
)
JS_RUNNERS = {"npm", "pnpm", "yarn"}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
CODE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".swift",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
}
SPECIAL_CODE_FILENAMES = {"makefile", "dockerfile", "justfile"}
TEST_DIR_PARTS = {"tests", "__tests__", "spec", "specs"}
DOC_DIR_PARTS = {"docs", "memory", "handoffs"}
CODE_DIR_HINTS = {"src", "lib", "app", "nanobot", "scripts"}
PROTOCOL_SCHEMA_VERSION = 1


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_active_session_root(workspace: Path) -> Path | None:
    control = _read_json(workspace / "sessions" / "control.json")
    index = _read_json(workspace / "sessions" / "index.json")
    if not control or not index:
        return None
    active_id = str(control.get("active_session_id") or "")
    if not active_id:
        return None
    session = (index.get("sessions") or {}).get(active_id)
    if not isinstance(session, dict):
        return None
    session_root = session.get("session_root")
    if not session_root:
        return None
    try:
        return Path(str(session_root)).resolve()
    except Exception:
        return None


def load_active_dev_state(workspace: Path) -> dict[str, Any] | None:
    session_root = get_active_session_root(workspace)
    if not session_root:
        return None
    data = _read_json(session_root / "dev_state.json")
    if not isinstance(data, dict):
        return None
    return data


def _gate_summary(gate: dict[str, Any] | None) -> str:
    gate = gate or {}
    if not gate.get("required"):
        return "not-required"
    return "satisfied" if gate.get("satisfied") else "pending"


def build_runtime_protocol(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    gates = state.get("gates") or {}
    return {
        "version": int(
            (state.get("runtime_protocol") or {}).get("version") or PROTOCOL_SCHEMA_VERSION
        ),
        "strict_dev_mode": str(state.get("strict_dev_mode") or "enforce"),
        "task_kind": str(state.get("task_kind") or "idle"),
        "phase": str(state.get("phase") or "idle"),
        "work_mode": str(state.get("work_mode") or "plan"),
        "current_step": str(state.get("current_step") or ""),
        "gates": {
            name: _gate_summary(gates.get(name))
            for name in ("plan", "debug_root_cause", "failing_test", "verification")
        },
    }


def load_runtime_protocol(workspace: Path) -> dict[str, Any] | None:
    state = load_active_dev_state(workspace)
    return build_runtime_protocol(state)


def format_runtime_protocol_block(
    protocol: dict[str, Any] | None,
    *,
    skill_hints: list[str] | None = None,
) -> str:
    if not protocol:
        return ""
    gates = protocol.get("gates") or {}
    lines = [
        "## Runtime Protocol",
        f"version: {protocol.get('version', PROTOCOL_SCHEMA_VERSION)}",
        f"strict_dev_mode: {protocol.get('strict_dev_mode', 'enforce')}",
        f"task_kind: {protocol.get('task_kind', 'idle')}",
        f"phase: {protocol.get('phase', 'idle')}",
        f"work_mode: {protocol.get('work_mode', 'plan')}",
    ]
    current_step = str(protocol.get("current_step") or "")
    if current_step:
        lines.append(f"current_step: {current_step}")
    lines.append(
        "gates: "
        + ", ".join(
            f"{name}={gates.get(name, 'not-required')}"
            for name in ("plan", "debug_root_cause", "failing_test", "verification")
        )
    )
    if skill_hints:
        lines.append(f"required_skills: {', '.join(skill_hints)}")
    return "\n".join(lines)


def is_strict_dev_mode_enforced(workspace: Path) -> bool:
    state = load_active_dev_state(workspace)
    if not state:
        return False
    if str(state.get("strict_dev_mode") or "") != "enforce":
        return False
    task_kind = str(state.get("task_kind") or "idle")
    phase = str(state.get("phase") or "idle")
    return task_kind != "idle" and phase != "idle"


def should_disable_concurrent_tools(workspace: Path) -> bool:
    return is_strict_dev_mode_enforced(workspace)


def format_dev_discipline_block(workspace: Path) -> str:
    state = load_active_dev_state(workspace)
    if not state or not is_strict_dev_mode_enforced(workspace):
        return ""
    gates = state.get("gates") or {}
    lines = [
        "## Work Mode",
        f"Current work mode: {state.get('work_mode', 'plan')}",
        "",
        "## Dev Discipline",
        f"strict_dev_mode: {state.get('strict_dev_mode', 'enforce')}",
        f"task_kind: {state.get('task_kind', 'general')}",
        f"phase: {state.get('phase', 'planning')}",
        "required gates:",
        f"- plan: {_gate_summary(gates.get('plan'))}",
        f"- debug_root_cause: {_gate_summary(gates.get('debug_root_cause'))}",
        f"- failing_test: {_gate_summary(gates.get('failing_test'))}",
        f"- verification: {_gate_summary(gates.get('verification'))}",
        "",
        "Discipline rules:",
        "- Do not bypass required gates.",
        "- In debug_required, investigate root cause before fixes.",
        "- In red_required, write/observe failing tests before implementation.",
        "- In verify_required, run fresh verification before any success claim.",
        "- Runtime tool guards may block writes and shell mutations that violate the current phase.",
    ]
    return "\n".join(lines)


def _gate_block(state: dict[str, Any] | None, gate_name: str) -> bool:
    if not state:
        return False
    gate = (state.get("gates") or {}).get(gate_name) or {}
    return bool(gate.get("required") and not gate.get("satisfied"))


def classify_path(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    parts = {part.lower() for part in path.parts}
    if (
        any(part in TEST_DIR_PARTS for part in parts)
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    ):
        return "test"
    if suffix in DOC_SUFFIXES or any(part in DOC_DIR_PARTS for part in parts):
        return "docs"
    if (
        name in SPECIAL_CODE_FILENAMES
        or suffix in CODE_SUFFIXES
        or any(part in CODE_DIR_HINTS for part in parts)
    ):
        return "code"
    return "other"


def guard_file_mutation(
    workspace: Path | None, path: Path, *, operation: str = "write"
) -> str | None:
    if workspace is None:
        return None
    state = load_active_dev_state(workspace)
    if not state or not is_strict_dev_mode_enforced(workspace):
        return None

    protocol = build_runtime_protocol(state)

    category = classify_path(path)
    phase = str((protocol or {}).get("phase") or state.get("phase") or "planning")
    work_mode = str((protocol or {}).get("work_mode") or state.get("work_mode") or "plan")

    if work_mode == "plan" and category in {"code", "test"}:
        return (
            f"Error: {operation} blocked by strict dev mode. Current work_mode=plan; "
            f"code/test mutation is not allowed yet for {path}. Use /plan exec or stay in docs/planning artifacts only."
        )

    if category == "docs":
        return None

    if category == "other" and phase not in {"build_allowed", "verify_required"}:
        return (
            f"Error: {operation} blocked by strict dev mode. Unclassified file mutation is only allowed during build/verify; "
            f"current phase={phase}, path={path}."
        )

    if category == "test":
        if phase in {"red_required", "build_allowed", "verify_required"}:
            return None
        return (
            f"Error: {operation} blocked by strict dev mode. Test-file writes are only allowed in red/build/verify phases; "
            f"current phase={phase}, path={path}."
        )

    if category == "code":
        if _gate_block(state, "debug_root_cause"):
            return (
                f"Error: {operation} blocked by strict dev mode. Root-cause investigation gate is still pending; "
                "use systematic debugging first, then mark the debug gate satisfied before changing implementation files."
            )
        if _gate_block(state, "failing_test"):
            return (
                f"Error: {operation} blocked by strict dev mode. Failing-test gate is still pending; "
                "write/observe a failing test first, then proceed with implementation changes."
            )
        if phase not in {"build_allowed", "verify_required"}:
            return (
                f"Error: {operation} blocked by strict dev mode. Implementation writes require build/verify phase; "
                f"current phase={phase}, path={path}."
            )
    return None


def _matches_any(command: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(command) for pattern in patterns)


def _has_shell_write_intent(command: str) -> bool:
    if WRITE_REDIRECT_RE.search(command):
        return True
    if GENERIC_REDIRECT_RE.search(command):
        return True
    return any(pattern.search(command) for pattern in MUTATING_SHELL_PATTERNS)


def classify_command(command: str) -> str:
    lower = command.strip().lower()
    if _has_shell_write_intent(lower):
        return "shell_write"
    js_runner = _extract_js_runner_target(command)
    if js_runner:
        _runner, target = js_runner
        if target == "test":
            return "test"
        return "build"
    if _matches_any(lower, TEST_COMMAND_PATTERNS):
        return "test"
    if _matches_any(lower, BUILD_COMMAND_PATTERNS):
        return "build"
    if _matches_any(lower, READONLY_COMMAND_PATTERNS):
        return "inspect"
    return "other"


def _extract_js_runner_target(command: str) -> tuple[str, str] | None:
    parts = command.strip().split()
    if not parts:
        return None
    runner = parts[0].lower()
    if runner not in JS_RUNNERS:
        return None
    idx = 1
    if idx < len(parts) and parts[idx].lower() == "run":
        idx += 1
    if idx >= len(parts):
        return runner, ""
    return runner, parts[idx].lower()


def _extract_task_runner_target(command: str) -> tuple[str, str] | None:
    stripped = command.strip()
    for runner in ("make", "just", "task"):
        if not stripped.lower().startswith(runner):
            continue
        parts = stripped.split()
        if len(parts) == 1:
            return runner, ""
        return runner, parts[1]
    return None


def _guard_task_runner_target(command: str) -> str | None:
    match = _extract_task_runner_target(command)
    if not match:
        return None
    runner, target = match
    if not target:
        return (
            f"Error: exec blocked by strict dev mode. `{runner}` without an explicit safe target is too ambiguous; "
            "run a specific test/build/check target instead."
        )
    if SAFE_TASK_TARGET_RE.fullmatch(target):
        return None
    if RISKY_TASK_TARGET_RE.search(target):
        return (
            f"Error: exec blocked by strict dev mode. `{runner} {target}` looks mutating/risky; "
            "use explicit file tools or a clearly read-only/test/build target."
        )
    return (
        f"Error: exec blocked by strict dev mode. `{runner} {target}` is not in the safe task-runner target set; "
        "allowed examples: test, check, lint, build, compile, typecheck, verify."
    )


def _guard_js_runner_target(command: str) -> str | None:
    match = _extract_js_runner_target(command)
    if not match:
        return None
    runner, target = match
    if not target:
        return (
            f"Error: exec blocked by strict dev mode. `{runner}` without an explicit safe script/command is too ambiguous; "
            "run a specific test/build/check target instead."
        )
    if SAFE_TASK_TARGET_RE.fullmatch(target):
        return None
    if RISKY_TASK_TARGET_RE.search(target):
        return (
            f"Error: exec blocked by strict dev mode. `{runner} {target}` looks mutating/risky; "
            "use explicit file tools or a clearly read-only/test/build script."
        )
    return (
        f"Error: exec blocked by strict dev mode. `{runner} {target}` is not in the safe script target set; "
        "allowed examples: test, check, lint, build, compile, typecheck, verify."
    )


def guard_exec_command(workspace: Path | None, command: str, cwd: str | None = None) -> str | None:
    if workspace is None:
        return None
    state = load_active_dev_state(workspace)
    if not state or not is_strict_dev_mode_enforced(workspace):
        return None

    protocol = build_runtime_protocol(state)

    phase = str((protocol or {}).get("phase") or state.get("phase") or "planning")
    work_mode = str((protocol or {}).get("work_mode") or state.get("work_mode") or "plan")
    category = classify_command(command)

    if category == "inspect":
        return None
    if category == "test":
        return None

    if category == "shell_write":
        if phase not in {"build_allowed", "verify_required"}:
            return (
                f"Error: exec blocked by strict dev mode. Shell mutation is not allowed in phase={phase}; "
                "use write_file/edit_file for controlled edits, or advance the workflow first."
            )
        if _gate_block(state, "debug_root_cause"):
            return (
                "Error: exec blocked by strict dev mode. Root-cause investigation gate is still pending; "
                "finish debugging before mutating implementation through shell commands."
            )
        if _gate_block(state, "failing_test"):
            return (
                "Error: exec blocked by strict dev mode. Failing-test gate is still pending; "
                "observe a failing test before using shell-based implementation mutations."
            )
        return None

    if category == "build":
        task_runner_error = _guard_task_runner_target(command)
        if task_runner_error:
            return task_runner_error
        js_runner_error = _guard_js_runner_target(command)
        if js_runner_error:
            return js_runner_error
        if work_mode != "build":
            return f"Error: exec blocked by strict dev mode. Build/lint commands require work_mode=build; current work_mode={work_mode}."
        if phase not in {"build_allowed", "verify_required"}:
            return f"Error: exec blocked by strict dev mode. Build/lint commands require build/verify phase; current phase={phase}."
        return None

    if phase in {"planning", "debug_required", "docs_only"}:
        return (
            f"Error: exec blocked by strict dev mode. Command category={category} is not allowed during phase={phase}; "
            "stick to inspection/tests or advance the workflow."
        )
    return None
