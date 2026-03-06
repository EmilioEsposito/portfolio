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

    def test_injects_synthetic_return_for_orphan(self):
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="send email")]),
            ModelResponse(parts=[ToolCallPart(tool_name="send_email", args='{}', tool_call_id="tc1")]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        assert len(result) == 3
        # The injected message should be a ModelRequest with ToolReturnPart
        injected = result[2]
        assert isinstance(injected, ModelRequest)
        assert len(injected.parts) == 1
        part = injected.parts[0]
        assert isinstance(part, ToolReturnPart)
        assert part.tool_call_id == "tc1"
        assert part.tool_name == "send_email"
        assert "interrupted" in part.content

    def test_handles_multiple_orphans_in_same_response(self):
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="do stuff")]),
            ModelResponse(parts=[
                ToolCallPart(tool_name="tool_a", args='{}', tool_call_id="tc1"),
                ToolCallPart(tool_name="tool_b", args='{}', tool_call_id="tc2"),
            ]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        assert len(result) == 3
        injected = result[2]
        assert isinstance(injected, ModelRequest)
        assert len(injected.parts) == 2
        ids = {p.tool_call_id for p in injected.parts}
        assert ids == {"tc1", "tc2"}

    def test_only_patches_orphaned_not_returned(self):
        """One tool call returned, one orphaned — only the orphan gets patched."""
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="do stuff")]),
            ModelResponse(parts=[
                ToolCallPart(tool_name="tool_a", args='{}', tool_call_id="tc1"),
                ToolCallPart(tool_name="tool_b", args='{}', tool_call_id="tc2"),
            ]),
            ModelRequest(parts=[ToolReturnPart(tool_name="tool_a", content="ok", tool_call_id="tc1")]),
            ModelResponse(parts=[TextPart(content="partial")]),
        ]
        result = _repair_orphaned_tool_calls(msgs)
        # Should inject a ModelRequest after the first ModelResponse for tc2
        assert len(result) == 5
        injected = result[2]  # right after the ModelResponse with both tool calls
        assert isinstance(injected, ModelRequest)
        assert len(injected.parts) == 1
        assert injected.parts[0].tool_call_id == "tc2"

    def test_idempotent(self):
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="send")]),
            ModelResponse(parts=[ToolCallPart(tool_name="t", args='{}', tool_call_id="tc1")]),
        ]
        first = _repair_orphaned_tool_calls(msgs)
        second = _repair_orphaned_tool_calls(first)
        # Should not add another synthetic return
        assert len(second) == len(first) == 3

    def test_empty_messages(self):
        assert _repair_orphaned_tool_calls([]) == []
