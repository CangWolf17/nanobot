import pytest

from nanobot.agent.tools.message import MessageTool


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
async def test_message_tool_can_block_same_target_direct_delivery_for_workflow() -> None:
    tool = MessageTool(
        default_channel="feishu",
        default_chat_id="chat1",
        default_metadata={"disable_message_tool_same_target": True},
    )
    result = await tool.execute(content="test")
    assert result == "Error: Direct delivery is disabled for this workflow; return the final content normally."
