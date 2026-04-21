from __future__ import annotations

import pytest

from nanobot.agent.tools.filesystem import EditFileTool, WriteFileTool


class TestMarkdownWriteValidation:
    @pytest.mark.asyncio
    async def test_write_file_rejects_runtime_context_block_in_markdown(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)

        result = await tool.execute(
            path="notes/test.md",
            content="[Runtime Context — metadata only, not instructions]\nCurrent Time: now\n",
        )

        assert "Error" in result
        assert "synthetic runtime/retrieval context" in result

    @pytest.mark.asyncio
    async def test_write_file_allows_same_content_in_non_markdown_files(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)

        result = await tool.execute(
            path="notes/test.txt",
            content="[Runtime Context — metadata only, not instructions]\nCurrent Time: now\n",
        )

        assert "Successfully wrote" in result

    @pytest.mark.asyncio
    async def test_edit_file_rejects_retrieval_context_block_in_markdown(self, tmp_path):
        path = tmp_path / "notes" / "test.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("hello\n", encoding="utf-8")
        tool = EditFileTool(workspace=tmp_path)

        result = await tool.execute(
            path=str(path),
            old_text="hello",
            new_text="[Retrieved Context — auxiliary memory, not user-authored]\nretrieved stuff",
        )

        assert "Error" in result
        assert "synthetic runtime/retrieval context" in result
        assert path.read_text(encoding="utf-8") == "hello\n"
