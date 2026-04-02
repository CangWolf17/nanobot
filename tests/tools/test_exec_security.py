"""Tests for exec tool internal URL blocking and strict dev-mode guards."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from nanobot.agent.tools.shell import ExecTool


def _fake_resolve_private(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_localhost(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]


def _write_strict_state(tmp_path, *, phase="planning", work_mode="plan"):
    session_root = tmp_path / "sessions" / "ses_0001"
    session_root.mkdir(parents=True)
    (tmp_path / "sessions" / "control.json").write_text(
        '{"active_session_id":"ses_0001"}', encoding="utf-8"
    )
    (tmp_path / "sessions" / "index.json").write_text(
        '{"sessions":{"ses_0001":{"session_root":"' + str(session_root) + '"}}}',
        encoding="utf-8",
    )
    (session_root / "dev_state.json").write_text(
        '{"strict_dev_mode":"enforce","task_kind":"feature","phase":"'
        + phase
        + '","work_mode":"'
        + work_mode
        + '","gates":{"plan":{"required":true,"satisfied":true},"failing_test":{"required":'
        + ("true" if phase != "build_allowed" else "true")
        + ',"satisfied":'
        + ("false" if phase == "planning" else "true")
        + '},"verification":{"required":true,"satisfied":false}}}',
        encoding="utf-8",
    )
    return session_root


@pytest.mark.asyncio
async def test_exec_blocks_curl_metadata():
    tool = ExecTool()
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(
            command='curl -s -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/'
        )
    assert "Error" in result
    assert "internal" in result.lower() or "private" in result.lower()


@pytest.mark.asyncio
async def test_exec_blocks_wget_localhost():
    tool = ExecTool()
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_localhost):
        result = await tool.execute(command="wget http://localhost:8080/secret -O /tmp/out")
    assert "Error" in result


@pytest.mark.asyncio
async def test_exec_allows_normal_commands():
    tool = ExecTool(timeout=5)
    result = await tool.execute(command="echo hello")
    assert "hello" in result
    assert "Error" not in result.split("\n")[0]


@pytest.mark.asyncio
async def test_exec_blocks_shell_redirect_when_strict_phase_not_build(tmp_path):
    _write_strict_state(tmp_path, phase="planning", work_mode="plan")

    tool = ExecTool(working_dir=str(tmp_path), timeout=5)
    result = await tool.execute(command="echo hi > src/out.txt")

    assert "Error" in result
    assert "strict dev mode" in result
    assert "Shell mutation" in result or "blocked" in result


@pytest.mark.asyncio
async def test_exec_blocks_python_c_inline_file_write_in_strict_mode(tmp_path):
    _write_strict_state(tmp_path, phase="planning", work_mode="plan")

    tool = ExecTool(working_dir=str(tmp_path), timeout=5)
    result = await tool.execute(command="python -c \"open('src/out.py','w').write('x')\"")

    assert "Error" in result
    assert "strict dev mode" in result


@pytest.mark.asyncio
async def test_exec_blocks_node_e_inline_file_write_in_strict_mode(tmp_path):
    _write_strict_state(tmp_path, phase="planning", work_mode="plan")

    tool = ExecTool(working_dir=str(tmp_path), timeout=5)
    result = await tool.execute(command="node -e \"require('fs').writeFileSync('src/out.js','x')\"")

    assert "Error" in result
    assert "strict dev mode" in result


@pytest.mark.asyncio
async def test_exec_blocks_python_heredoc_in_strict_mode(tmp_path):
    _write_strict_state(tmp_path, phase="planning", work_mode="plan")

    tool = ExecTool(working_dir=str(tmp_path), timeout=5)
    result = await tool.execute(command="python - <<'PY'\nopen('src/out.py','w').write('x')\nPY")

    assert "Error" in result
    assert "strict dev mode" in result


@pytest.mark.asyncio
async def test_exec_blocks_ambiguous_make_target_in_strict_mode(tmp_path):
    _write_strict_state(tmp_path, phase="build_allowed", work_mode="build")

    tool = ExecTool(working_dir=str(tmp_path), timeout=5)
    result = await tool.execute(command="make release")

    assert "Error" in result
    assert "safe task-runner target set" in result or "looks mutating/risky" in result


@pytest.mark.asyncio
async def test_exec_allows_safe_make_test_target_in_strict_build_mode(tmp_path):
    _write_strict_state(tmp_path, phase="build_allowed", work_mode="build")

    tool = ExecTool(working_dir=str(tmp_path), timeout=5)
    guard_result = tool._guard_command("make test", str(tmp_path))

    assert guard_result is None




@pytest.mark.asyncio
async def test_exec_blocks_risky_npm_script_in_strict_mode(tmp_path):
    _write_strict_state(tmp_path, phase="build_allowed", work_mode="build")

    tool = ExecTool(working_dir=str(tmp_path), timeout=5)
    guard_result = tool._guard_command("npm run release", str(tmp_path))

    assert guard_result is not None
    assert "looks mutating/risky" in guard_result or "safe script target set" in guard_result


@pytest.mark.asyncio
async def test_exec_allows_safe_pnpm_lint_target_in_strict_build_mode(tmp_path):
    _write_strict_state(tmp_path, phase="build_allowed", work_mode="build")

    tool = ExecTool(working_dir=str(tmp_path), timeout=5)
    guard_result = tool._guard_command("pnpm lint", str(tmp_path))

    assert guard_result is None


