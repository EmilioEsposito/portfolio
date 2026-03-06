"""Tests for _repair_orphaned_tool_calls in ai_demos/models.py."""

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from api.src.ai_demos.models import _repair_orphaned_tool_calls


class TestRepairOrphanedToolCalls:
    def test_no_op_when_no_tool_calls(self):
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="hello")]),
            ModelResponse(parts=[TextPart(content="hi")]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        assert result == msgs

    def test_no_op_when_all_tool_calls_returned(self):
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="send email")]),
            ModelResponse(parts=[ToolCallPart(tool_name="send_email", args='{}', tool_call_id="tc1")]),
            ModelRequest(parts=[ToolReturnPart(tool_name="send_email", content="sent", tool_call_id="tc1")]),
            ModelResponse(parts=[TextPart(content="done")]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        assert len(result) == 4

    def test_does_not_patch_terminal_pending_approval(self):
        """Tool call in last ModelResponse is a pending approval — leave it alone."""
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="send email")]),
            ModelResponse(parts=[ToolCallPart(tool_name="send_email", args='{}', tool_call_id="tc1")]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        # Should NOT inject anything — this is a pending approval
        assert len(result) == 2

    def test_patches_orphan_mid_conversation(self):
        """Tool call without return followed by more messages — truly orphaned."""
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="send email")]),
            ModelResponse(parts=[ToolCallPart(tool_name="send_email", args='{}', tool_call_id="tc1")]),
            # Conversation continued without tool return — orphaned
            ModelRequest(parts=[UserPromptPart(content="what happened?")]),
            ModelResponse(parts=[TextPart(content="sorry, let me try again")]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        assert len(result) == 5  # injected one synthetic ModelRequest
        injected = result[2]
        assert isinstance(injected, ModelRequest)
        assert len(injected.parts) == 1
        part = injected.parts[0]
        assert isinstance(part, ToolReturnPart)
        assert part.tool_call_id == "tc1"
        assert part.tool_name == "send_email"
        assert "interrupted" in part.content

    def test_handles_multiple_orphans_mid_conversation(self):
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="do stuff")]),
            ModelResponse(parts=[
                ToolCallPart(tool_name="tool_a", args='{}', tool_call_id="tc1"),
                ToolCallPart(tool_name="tool_b", args='{}', tool_call_id="tc2"),
            ]),
            # Conversation continued
            ModelRequest(parts=[UserPromptPart(content="next")]),
            ModelResponse(parts=[TextPart(content="ok")]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        assert len(result) == 5
        injected = result[2]
        assert isinstance(injected, ModelRequest)
        assert len(injected.parts) == 2
        ids = {p.tool_call_id for p in injected.parts}
        assert ids == {"tc1", "tc2"}

    def test_only_patches_orphaned_not_returned_mid_conversation(self):
        """One tool call returned, one orphaned mid-conversation."""
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="do stuff")]),
            ModelResponse(parts=[
                ToolCallPart(tool_name="tool_a", args='{}', tool_call_id="tc1"),
                ToolCallPart(tool_name="tool_b", args='{}', tool_call_id="tc2"),
            ]),
            ModelRequest(parts=[ToolReturnPart(tool_name="tool_a", content="ok", tool_call_id="tc1")]),
            ModelResponse(parts=[TextPart(content="partial")]),
            # More messages after — tc2 is orphaned
            ModelRequest(parts=[UserPromptPart(content="continue")]),
            ModelResponse(parts=[TextPart(content="done")]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        # tc2 should be patched (it's in a non-terminal ModelResponse)
        assert len(result) == 7
        injected = result[2]  # right after the ModelResponse with both tool calls
        assert isinstance(injected, ModelRequest)
        assert len(injected.parts) == 1
        assert injected.parts[0].tool_call_id == "tc2"

    def test_idempotent(self):
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="send")]),
            ModelResponse(parts=[ToolCallPart(tool_name="t", args='{}', tool_call_id="tc1")]),
            ModelRequest(parts=[UserPromptPart(content="what happened?")]),
            ModelResponse(parts=[TextPart(content="error")]),
        ]
        first = _repair_orphaned_tool_calls(msgs)
        second = _repair_orphaned_tool_calls(first)
        assert len(second) == len(first) == 5

    def test_empty_messages(self):
        assert _repair_orphaned_tool_calls([]) == []

    def test_terminal_pending_with_earlier_completed(self):
        """Earlier tool call completed, terminal one is pending — don't touch terminal."""
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="step 1")]),
            ModelResponse(parts=[ToolCallPart(tool_name="search", args='{}', tool_call_id="tc1")]),
            ModelRequest(parts=[ToolReturnPart(tool_name="search", content="results", tool_call_id="tc1")]),
            ModelResponse(parts=[
                TextPart(content="Found it. Let me send the email."),
                ToolCallPart(tool_name="send_email", args='{}', tool_call_id="tc2"),
            ]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        # tc2 is in the last ModelResponse — pending approval, don't patch
        assert len(result) == 4
