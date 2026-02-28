"""
Unit tests for history processors (summarize_tool_results, compact_history).

All sub-agent calls are mocked — no real API hits.
Uses realistic tool result formats matching actual ClickUp, Gmail, and DB search output.
"""

import json
from unittest.mock import AsyncMock, patch, MagicMock
from dataclasses import dataclass

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
    ToolCallPart,
    ToolReturnPart,
    RequestUsage,
)

from api.src.sernia_ai.config import SUMMARIZATION_CHAR_THRESHOLD, TOKEN_COMPACTION_THRESHOLD
from api.src.sernia_ai.sub_agents.summarize_tool_results import (
    summarize_tool_results,
    _find_current_turn_boundary,
    _MAX_SUMMARIZER_INPUT_CHARS,
)
from api.src.sernia_ai.sub_agents.compact_history import (
    compact_history,
    _estimate_tokens,
    _find_split_point,
    _messages_to_text,
    _MIN_RECENT_MESSAGES,
)


# ---------------------------------------------------------------------------
# Realistic tool result generators
# ---------------------------------------------------------------------------

def _fake_clickup_tasks(n: int = 100) -> str:
    """Generate a realistic ClickUp search_tasks result with N tasks."""
    lines = []
    statuses = ["to do", "in progress", "review", "done", "blocked"]
    priorities = ["urgent", "high", "normal", "low", "none"]
    for i in range(n):
        lines.append(
            f"- Task {i}: Fix plumbing issue at Unit {100 + i} ({('Urgent' if i % 5 == 0 else 'Normal')}) (id: abc{i:04d})\n"
            f"  Status: {statuses[i % len(statuses)]} | Priority: {priorities[i % len(priorities)]} | Due: 2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}\n"
            f"  Assignees: {'Emilio' if i % 3 == 0 else 'John'}\n"
            f"  URL: https://app.clickup.com/t/abc{i:04d}"
        )
    result = "\n".join(lines)
    if n == 100:
        result += "\n\n(Page 0 — 100 tasks returned, more may exist on page 1)"
    return result


def _fake_email_search(n: int = 50) -> str:
    """Generate a realistic Gmail search_emails result with N emails."""
    lines = []
    for i in range(n):
        lines.append(
            f"[2025-06-{(i % 28) + 1:02d} {(i % 12) + 8}:{i % 60:02d} AM] From: tenant{i}@gmail.com\n"
            f"  Subject: {'Rent Payment Confirmation' if i % 3 == 0 else 'Maintenance Request — Unit ' + str(100 + i)}\n"
            f"  Snippet: {'Payment of $1,200 received for June 2025. Thank you for your prompt payment.' if i % 3 == 0 else 'Hi, I wanted to report a leak in the bathroom sink that started yesterday. The water is dripping constantly and...'}\n"
            f"  ID: msg{i:06d}"
        )
    return "\n\n".join(lines)


def _fake_drive_doc() -> str:
    """Generate a realistic long Google Drive document content."""
    sections = []
    for i in range(50):
        sections.append(
            f"## Section {i + 1}: Property Management Policy {i + 1}\n\n"
            f"This policy governs the maintenance procedures for all units in Building {chr(65 + i % 26)}. "
            f"Tenants must submit maintenance requests through the approved portal within 48 hours of discovering "
            f"any issue. Emergency repairs (flooding, gas leaks, electrical hazards) should be reported immediately "
            f"to the on-call maintenance number at (555) 123-{4000 + i}.\n\n"
            f"Response times: Emergency — 2 hours, Urgent — 24 hours, Normal — 5 business days.\n"
            f"Approved vendors: {', '.join(f'Vendor {j}' for j in range(5))}.\n"
        )
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx() -> RunContext:
    """Build a minimal RunContext mock for the processors."""
    ctx = MagicMock(spec=RunContext)
    ctx.deps = MagicMock()
    return ctx


def _user_msg(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant_msg(text: str, input_tokens: int = 0) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content=text)],
        usage=RequestUsage(input_tokens=input_tokens, output_tokens=10),
    )


def _tool_call_response(tool_name: str, call_id: str, args: str = "{}") -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=call_id)],
    )


def _tool_return_request(
    tool_name: str, content: str, call_id: str
) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=call_id)],
    )


@dataclass
class FakeRunResult:
    output: str


# ---------------------------------------------------------------------------
# Smoke Tests
# ---------------------------------------------------------------------------


class TestSmoke:
    """Verify that the agent loads with history processors wired up."""

    def test_agent_import_and_processors_wired(self):
        """sernia_agent should import and have both history_processors attached."""
        from api.src.sernia_ai.agent import sernia_agent
        processors = sernia_agent.history_processors
        assert processors is not None
        assert len(processors) == 2
        # Order: summarization first, compaction second
        assert processors[0] is summarize_tool_results
        assert processors[1] is compact_history

    def test_sub_agent_models_configured(self):
        """Sub-agents should use the configured SUB_AGENT_MODEL."""
        from api.src.sernia_ai.sub_agents.summarize_tool_results import _summarizer
        from api.src.sernia_ai.sub_agents.compact_history import _compactor
        from api.src.sernia_ai.config import SUB_AGENT_MODEL

        assert _summarizer.model.model_name == SUB_AGENT_MODEL.split(":")[-1]
        assert _compactor.model.model_name == SUB_AGENT_MODEL.split(":")[-1]


# ---------------------------------------------------------------------------
# Summarizer Tests
# ---------------------------------------------------------------------------


class TestSummarizeToolResults:
    """Tests for the tool result summarization processor."""

    @pytest.mark.asyncio
    async def test_small_clickup_result_unchanged(self):
        """A small ClickUp result (5 tasks) should pass through unmodified."""
        small_result = _fake_clickup_tasks(5)
        assert len(small_result) < SUMMARIZATION_CHAR_THRESHOLD

        messages: list[ModelMessage] = [
            _user_msg("Show me urgent tasks"),
            _tool_call_response("search_tasks", "tc1", json.dumps({"statuses": ["urgent"]})),
            _tool_return_request("search_tasks", small_result, "tc1"),
            _assistant_msg("Here are 5 urgent tasks"),
            _user_msg("thanks"),
        ]

        ctx = _make_ctx()
        result = await summarize_tool_results(ctx, messages)
        assert result == messages

    @pytest.mark.asyncio
    async def test_large_clickup_dump_gets_summarized(self):
        """100-task ClickUp dump in older messages should be summarized."""
        big_result = _fake_clickup_tasks(100)
        assert len(big_result) > SUMMARIZATION_CHAR_THRESHOLD

        messages: list[ModelMessage] = [
            _user_msg("Show me all tasks"),
            _tool_call_response("search_tasks", "tc1"),
            _tool_return_request("search_tasks", big_result, "tc1"),
            _assistant_msg("Found 100 tasks across all projects. Here's a summary..."),
            _user_msg("Which ones are assigned to Emilio?"),
        ]

        ctx = _make_ctx()
        mock_result = FakeRunResult(
            output="100 ClickUp tasks returned. Mix of to do, in progress, review, done, "
            "and blocked. Assignees: Emilio (34 tasks), John (66 tasks). "
            "Priorities range from urgent to none. Due dates span Jan-Dec 2025."
        )

        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock(return_value=mock_result)
            result = await summarize_tool_results(ctx, messages)

        tool_part = result[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        assert "[Summarized search_tasks result]" in tool_part.content
        assert "100 ClickUp tasks" in tool_part.content
        assert tool_part.tool_call_id == "tc1"
        assert tool_part.tool_name == "search_tasks"
        # Summary should be much shorter than original
        assert len(tool_part.content) < len(big_result)

    @pytest.mark.asyncio
    async def test_large_email_search_gets_summarized(self):
        """50-email Gmail search in older messages should be summarized."""
        big_result = _fake_email_search(50)
        assert len(big_result) > SUMMARIZATION_CHAR_THRESHOLD

        messages: list[ModelMessage] = [
            _user_msg("Search for rent payment emails from June"),
            _tool_call_response("search_emails", "tc1", json.dumps({"query": "rent payment june"})),
            _tool_return_request("search_emails", big_result, "tc1"),
            _assistant_msg("Found 50 emails matching your search"),
            _user_msg("How many tenants paid on time?"),
        ]

        ctx = _make_ctx()
        mock_result = FakeRunResult(
            output="50 emails found for June 2025. ~17 rent payment confirmations, "
            "~33 maintenance requests. Tenants range from tenant0 to tenant49."
        )

        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock(return_value=mock_result)
            result = await summarize_tool_results(ctx, messages)

        tool_part = result[2].parts[0]
        assert "[Summarized search_emails result]" in tool_part.content
        assert "50 emails" in tool_part.content

    @pytest.mark.asyncio
    async def test_large_drive_doc_gets_summarized(self):
        """Long Google Drive doc content should be summarized."""
        big_result = _fake_drive_doc()
        assert len(big_result) > SUMMARIZATION_CHAR_THRESHOLD

        messages: list[ModelMessage] = [
            _user_msg("Get the property management policies doc"),
            _tool_call_response("get_drive_doc", "tc1"),
            _tool_return_request("get_drive_doc", big_result, "tc1"),
            _assistant_msg("Here are the property management policies"),
            _user_msg("What's the emergency response time?"),
        ]

        ctx = _make_ctx()
        mock_result = FakeRunResult(
            output="Property management policies covering 50 sections for Buildings A-Z. "
            "Response times: Emergency 2hrs, Urgent 24hrs, Normal 5 business days."
        )

        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock(return_value=mock_result)
            result = await summarize_tool_results(ctx, messages)

        tool_part = result[2].parts[0]
        assert "[Summarized get_drive_doc result]" in tool_part.content

    @pytest.mark.asyncio
    async def test_current_turn_large_result_not_summarized(self):
        """Large result in the current turn (no assistant reply yet) stays intact."""
        big_result = _fake_clickup_tasks(100)

        messages: list[ModelMessage] = [
            _user_msg("Show me all tasks"),
            _tool_call_response("search_tasks", "tc1"),
            _tool_return_request("search_tasks", big_result, "tc1"),
            # No assistant response — agent is still processing this result
        ]

        ctx = _make_ctx()
        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock()
            result = await summarize_tool_results(ctx, messages)
            mock_agent.run.assert_not_called()

        assert result[2].parts[0].content == big_result

    @pytest.mark.asyncio
    async def test_multi_step_tool_chain_current_turn(self):
        """Multi-step tool chain at the end (email search -> then task search) stays intact."""
        big_emails = _fake_email_search(50)
        big_tasks = _fake_clickup_tasks(100)

        messages: list[ModelMessage] = [
            _user_msg("hello"),
            _assistant_msg("hi there"),
            _user_msg("Find all overdue maintenance and related tasks"),
            _tool_call_response("search_emails", "tc1", json.dumps({"query": "maintenance overdue"})),
            _tool_return_request("search_emails", big_emails, "tc1"),
            _tool_call_response("search_tasks", "tc2", json.dumps({"statuses": ["overdue"]})),
            _tool_return_request("search_tasks", big_tasks, "tc2"),
            # Agent still processing — no final response yet
        ]

        ctx = _make_ctx()
        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock()
            result = await summarize_tool_results(ctx, messages)
            mock_agent.run.assert_not_called()

        assert len(result) == len(messages)
        # Both tool results unchanged
        assert result[4].parts[0].content == big_emails
        assert result[6].parts[0].content == big_tasks

    @pytest.mark.asyncio
    async def test_multiple_oversized_results_in_history(self):
        """Multiple large results across different turns should all be summarized."""
        big_tasks = _fake_clickup_tasks(100)
        big_emails = _fake_email_search(50)

        messages: list[ModelMessage] = [
            # Turn 1: large task search
            _user_msg("Show me all tasks"),
            _tool_call_response("search_tasks", "tc1"),
            _tool_return_request("search_tasks", big_tasks, "tc1"),
            _assistant_msg("Found 100 tasks"),
            # Turn 2: large email search
            _user_msg("Now search for maintenance emails"),
            _tool_call_response("search_emails", "tc2"),
            _tool_return_request("search_emails", big_emails, "tc2"),
            _assistant_msg("Found 50 emails"),
            # Current turn
            _user_msg("Summarize everything"),
        ]

        ctx = _make_ctx()
        call_count = 0

        async def _mock_run(prompt):
            nonlocal call_count
            call_count += 1
            if "search_tasks" in prompt:
                return FakeRunResult(output="100 tasks summary")
            return FakeRunResult(output="50 emails summary")

        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock(side_effect=_mock_run)
            result = await summarize_tool_results(ctx, messages)

        assert call_count == 2
        assert "[Summarized search_tasks result]" in result[2].parts[0].content
        assert "[Summarized search_emails result]" in result[6].parts[0].content

    @pytest.mark.asyncio
    async def test_failure_preserves_original_realistic(self):
        """API failure should preserve the original large tool result."""
        big_result = _fake_clickup_tasks(100)

        messages: list[ModelMessage] = [
            _user_msg("Show me all tasks"),
            _tool_call_response("search_tasks", "tc1"),
            _tool_return_request("search_tasks", big_result, "tc1"),
            _assistant_msg("Found 100 tasks"),
            _user_msg("next question"),
        ]

        ctx = _make_ctx()
        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock(side_effect=RuntimeError("Haiku rate limit"))
            result = await summarize_tool_results(ctx, messages)

        assert result[2].parts[0].content == big_result

    @pytest.mark.asyncio
    async def test_message_structure_preserved(self):
        """Message types, tool_call_id, and tool_name should be preserved after summarization."""
        big_result = _fake_email_search(50)

        messages: list[ModelMessage] = [
            _user_msg("search emails"),
            _tool_call_response("search_emails", "tc42", json.dumps({"query": "rent"})),
            _tool_return_request("search_emails", big_result, "tc42"),
            _assistant_msg("done"),
            _user_msg("ok"),
        ]

        ctx = _make_ctx()
        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock(return_value=FakeRunResult(output="Condensed"))
            result = await summarize_tool_results(ctx, messages)

        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[1], ModelResponse)
        assert isinstance(result[2], ModelRequest)
        assert isinstance(result[3], ModelResponse)
        assert isinstance(result[4], ModelRequest)

        part = result[2].parts[0]
        assert isinstance(part, ToolReturnPart)
        assert part.tool_call_id == "tc42"
        assert part.tool_name == "search_emails"

    @pytest.mark.asyncio
    async def test_input_capped_at_max_chars(self):
        """Summarizer input should be capped at _MAX_SUMMARIZER_INPUT_CHARS."""
        # Generate content way larger than the cap
        huge_content = "A" * (_MAX_SUMMARIZER_INPUT_CHARS + 50_000)

        messages: list[ModelMessage] = [
            _user_msg("get doc"),
            _tool_call_response("get_drive_doc", "tc1"),
            _tool_return_request("get_drive_doc", huge_content, "tc1"),
            _assistant_msg("got it"),
            _user_msg("next"),
        ]

        ctx = _make_ctx()
        captured_prompt = None

        async def _capture_run(prompt):
            nonlocal captured_prompt
            captured_prompt = prompt
            return FakeRunResult(output="summary")

        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock(side_effect=_capture_run)
            await summarize_tool_results(ctx, messages)

        # The prompt sent to summarizer should be capped
        assert len(captured_prompt) <= _MAX_SUMMARIZER_INPUT_CHARS + 200  # some overhead for prefix

    @pytest.mark.asyncio
    async def test_mixed_small_and_large_results(self):
        """Only oversized results get summarized; small ones in same history are untouched."""
        small_result = _fake_clickup_tasks(3)
        big_result = _fake_email_search(50)

        messages: list[ModelMessage] = [
            # Turn 1: small result (should stay)
            _user_msg("Show urgent tasks"),
            _tool_call_response("search_tasks", "tc1"),
            _tool_return_request("search_tasks", small_result, "tc1"),
            _assistant_msg("Found 3 tasks"),
            # Turn 2: big result (should be summarized)
            _user_msg("Search all emails"),
            _tool_call_response("search_emails", "tc2"),
            _tool_return_request("search_emails", big_result, "tc2"),
            _assistant_msg("Found 50 emails"),
            _user_msg("Continue"),
        ]

        ctx = _make_ctx()
        with patch(
            "api.src.sernia_ai.sub_agents.summarize_tool_results._summarizer"
        ) as mock_agent:
            mock_agent.run = AsyncMock(return_value=FakeRunResult(output="email summary"))
            result = await summarize_tool_results(ctx, messages)
            # Only called once (for the big email result)
            assert mock_agent.run.call_count == 1

        # Small result untouched
        assert result[2].parts[0].content == small_result
        # Big result summarized
        assert "[Summarized search_emails result]" in result[6].parts[0].content


class TestFindCurrentTurnBoundary:
    """Tests for the _find_current_turn_boundary helper."""

    def test_empty_messages(self):
        assert _find_current_turn_boundary([]) == 0

    def test_single_user_message(self):
        msgs = [_user_msg("hi")]
        assert _find_current_turn_boundary(msgs) == 0

    def test_user_then_assistant(self):
        msgs = [_user_msg("hi"), _assistant_msg("hello")]
        boundary = _find_current_turn_boundary(msgs)
        assert boundary <= 1

    def test_tool_cycle_at_end(self):
        """Tool call/return at the end should all be current turn."""
        msgs: list[ModelMessage] = [
            _user_msg("old question"),
            _assistant_msg("old answer"),
            _user_msg("new question"),
            _tool_call_response("search_tasks", "tc1"),
            _tool_return_request("search_tasks", "result", "tc1"),
        ]
        boundary = _find_current_turn_boundary(msgs)
        assert boundary == 2

    def test_multiple_tool_cycles_at_end(self):
        """Multiple tool cycles at end are all current turn."""
        msgs: list[ModelMessage] = [
            _user_msg("old"),
            _assistant_msg("old answer"),
            _user_msg("complex request"),
            _tool_call_response("search_emails", "tc1"),
            _tool_return_request("search_emails", "emails", "tc1"),
            _tool_call_response("search_tasks", "tc2"),
            _tool_return_request("search_tasks", "tasks", "tc2"),
        ]
        boundary = _find_current_turn_boundary(msgs)
        assert boundary == 2

    def test_completed_turn_then_new_user_msg(self):
        """If the last message is a user prompt, it starts a new current turn."""
        msgs: list[ModelMessage] = [
            _user_msg("q1"),
            _tool_call_response("tool", "tc1"),
            _tool_return_request("tool", "r1", "tc1"),
            _assistant_msg("answer 1"),
            _user_msg("q2"),
        ]
        boundary = _find_current_turn_boundary(msgs)
        assert boundary == 4


# ---------------------------------------------------------------------------
# Compactor Tests
# ---------------------------------------------------------------------------


class TestCompactHistory:
    """Tests for the history compaction processor."""

    @pytest.mark.asyncio
    async def test_under_threshold_unchanged(self):
        """Conversation with low token count should pass through unchanged."""
        messages: list[ModelMessage] = [
            _user_msg("Show me all tasks"),
            _assistant_msg("Here are 5 tasks...", input_tokens=5000),
            _user_msg("Which ones are overdue?"),
            _assistant_msg("Tasks 2 and 4 are overdue", input_tokens=6000),
            _user_msg("Mark them as urgent"),
        ]

        ctx = _make_ctx()
        result = await compact_history(ctx, messages)
        assert result == messages

    @pytest.mark.asyncio
    async def test_no_usage_info_unchanged(self):
        """If no ModelResponse has input_tokens, should pass through."""
        messages: list[ModelMessage] = [
            _user_msg("hello"),
            _assistant_msg("hi", input_tokens=0),
            _user_msg("bye"),
        ]

        ctx = _make_ctx()
        result = await compact_history(ctx, messages)
        assert result == messages

    @pytest.mark.asyncio
    async def test_long_conversation_triggers_compaction(self):
        """Realistic long conversation approaching token limit should compact."""
        messages: list[ModelMessage] = [
            # Older turns with tool use
            _user_msg("Search all tasks"),
            _tool_call_response("search_tasks", "tc1"),
            _tool_return_request("search_tasks", _fake_clickup_tasks(20), "tc1"),
            _assistant_msg("Found 20 tasks across your projects", input_tokens=30_000),
            _user_msg("Show me maintenance emails from last month"),
            _tool_call_response("search_emails", "tc2"),
            _tool_return_request("search_emails", _fake_email_search(10), "tc2"),
            _assistant_msg("Found 10 maintenance-related emails", input_tokens=60_000),
            _user_msg("Check the property management policy doc"),
            _tool_call_response("get_drive_doc", "tc3"),
            _tool_return_request("get_drive_doc", "Long policy document...", "tc3"),
            _assistant_msg("Here are the key policies...", input_tokens=100_000),
            # Recent turns approaching the limit
            _user_msg("What's the SLA for emergency repairs?"),
            _assistant_msg("Emergency repairs have a 2-hour SLA", input_tokens=120_000),
            _user_msg("Draft an email to all tenants about the new policy"),
            _assistant_msg("Here's a draft email...", input_tokens=TOKEN_COMPACTION_THRESHOLD + 5_000),
            _user_msg("Send it"),
        ]

        ctx = _make_ctx()
        mock_result = FakeRunResult(
            output="- User searched ClickUp tasks (20 found) and maintenance emails (10 found)\n"
            "- Reviewed property management policy document\n"
            "- Emergency repair SLA is 2 hours\n"
            "- Draft email to tenants about new policy was prepared"
        )

        with patch(
            "api.src.sernia_ai.sub_agents.compact_history._compactor"
        ) as mock_agent:
            mock_agent.run = AsyncMock(return_value=mock_result)
            result = await compact_history(ctx, messages)

        # First message is the summary
        assert isinstance(result[0], ModelRequest)
        summary_part = result[0].parts[0]
        assert isinstance(summary_part, UserPromptPart)
        assert "[Conversation summary" in summary_part.content
        assert "ClickUp tasks" in summary_part.content

        # Fewer messages than original
        assert len(result) < len(messages)
        # Recent messages preserved
        assert any(
            isinstance(m, ModelRequest)
            and any(isinstance(p, UserPromptPart) and "Send it" in p.content for p in m.parts)
            for m in result
        )

    @pytest.mark.asyncio
    async def test_at_least_min_recent_messages_kept(self):
        """At least _MIN_RECENT_MESSAGES should be kept in recent portion."""
        messages: list[ModelMessage] = []
        for i in range(20):
            messages.append(_user_msg(f"Question about unit {100 + i}"))
            tokens = TOKEN_COMPACTION_THRESHOLD + 5000 if i == 19 else 1000
            messages.append(_assistant_msg(f"Answer about unit {100 + i}", input_tokens=tokens))
        messages.append(_user_msg("One more question"))

        ctx = _make_ctx()
        with patch(
            "api.src.sernia_ai.sub_agents.compact_history._compactor"
        ) as mock_agent:
            mock_agent.run = AsyncMock(return_value=FakeRunResult(output="Summary"))
            result = await compact_history(ctx, messages)

        # summary + at least MIN_RECENT messages
        assert len(result) >= _MIN_RECENT_MESSAGES + 1

    @pytest.mark.asyncio
    async def test_split_on_model_request_boundary(self):
        """Split point should land on a ModelRequest, not mid-turn."""
        messages: list[ModelMessage] = [
            _user_msg("msg 1"),
            _assistant_msg("resp 1"),
            _user_msg("msg 2"),
            _assistant_msg("resp 2"),
            _user_msg("msg 3"),
            _assistant_msg("resp 3"),
            _user_msg("msg 4"),
            _assistant_msg("resp 4", input_tokens=TOKEN_COMPACTION_THRESHOLD + 1000),
            _user_msg("msg 5"),
        ]

        ctx = _make_ctx()

        with patch(
            "api.src.sernia_ai.sub_agents.compact_history._compactor"
        ) as mock_agent:
            mock_agent.run = AsyncMock(return_value=FakeRunResult(output="Summary"))
            result = await compact_history(ctx, messages)

        # result[0] is summary (ModelRequest), rest are original messages
        for msg in result[1:]:
            assert isinstance(msg, (ModelRequest, ModelResponse))

    @pytest.mark.asyncio
    async def test_failure_returns_original(self):
        """If compactor sub-agent fails, original messages returned intact."""
        messages: list[ModelMessage] = [
            _user_msg("msg 1"),
            _assistant_msg("resp 1"),
            _user_msg("msg 2"),
            _assistant_msg("resp 2"),
            _user_msg("msg 3"),
            _assistant_msg("resp 3", input_tokens=TOKEN_COMPACTION_THRESHOLD + 1000),
            _user_msg("msg 4"),
        ]

        ctx = _make_ctx()
        with patch(
            "api.src.sernia_ai.sub_agents.compact_history._compactor"
        ) as mock_agent:
            mock_agent.run = AsyncMock(side_effect=RuntimeError("Haiku overloaded"))
            result = await compact_history(ctx, messages)

        assert result == messages

    @pytest.mark.asyncio
    async def test_too_few_messages_unchanged(self):
        """Even with high tokens, fewer than MIN_RECENT messages should pass through."""
        messages: list[ModelMessage] = [
            _user_msg("hello"),
            _assistant_msg("hi", input_tokens=TOKEN_COMPACTION_THRESHOLD + 50_000),
        ]

        ctx = _make_ctx()
        result = await compact_history(ctx, messages)
        assert result == messages

    @pytest.mark.asyncio
    async def test_compaction_with_tool_results_in_transcript(self):
        """Compactor should receive readable transcript including tool results."""
        messages: list[ModelMessage] = [
            _user_msg("Find tasks"),
            _tool_call_response("search_tasks", "tc1"),
            _tool_return_request("search_tasks", "3 tasks found: fix sink, paint wall, replace lock", "tc1"),
            _assistant_msg("Found 3 maintenance tasks", input_tokens=5000),
            _user_msg("What about emails?"),
            _tool_call_response("search_emails", "tc2"),
            _tool_return_request("search_emails", "2 emails about rent", "tc2"),
            _assistant_msg("Found 2 rent emails", input_tokens=TOKEN_COMPACTION_THRESHOLD + 1000),
            _user_msg("OK thanks"),
        ]

        ctx = _make_ctx()
        captured_prompt = None

        async def _capture(prompt):
            nonlocal captured_prompt
            captured_prompt = prompt
            return FakeRunResult(output="Summary")

        with patch(
            "api.src.sernia_ai.sub_agents.compact_history._compactor"
        ) as mock_agent:
            mock_agent.run = AsyncMock(side_effect=_capture)
            await compact_history(ctx, messages)

        # Transcript should contain readable tool results
        assert "TOOL RESULT (search_tasks)" in captured_prompt
        assert "fix sink" in captured_prompt


class TestEstimateTokens:
    """Tests for the _estimate_tokens helper."""

    def test_returns_last_response_input_tokens(self):
        messages: list[ModelMessage] = [
            _user_msg("hello"),
            _assistant_msg("hi", input_tokens=5000),
            _user_msg("more"),
            _assistant_msg("sure", input_tokens=12000),
        ]
        assert _estimate_tokens(messages) == 12000

    def test_skips_intermediate_responses(self):
        """Should use the LAST response, not sum or average."""
        messages: list[ModelMessage] = [
            _user_msg("q1"),
            _assistant_msg("a1", input_tokens=50_000),
            _user_msg("q2"),
            _assistant_msg("a2", input_tokens=150_000),
            _user_msg("q3"),
            _assistant_msg("a3", input_tokens=175_000),
        ]
        assert _estimate_tokens(messages) == 175_000

    def test_returns_none_when_no_responses(self):
        messages: list[ModelMessage] = [_user_msg("hello")]
        assert _estimate_tokens(messages) is None

    def test_returns_none_when_no_input_tokens(self):
        messages: list[ModelMessage] = [
            _user_msg("hello"),
            _assistant_msg("hi", input_tokens=0),
        ]
        assert _estimate_tokens(messages) is None


class TestFindSplitPoint:
    """Tests for the _find_split_point helper."""

    def test_snaps_forward_to_model_request(self):
        messages: list[ModelMessage] = [
            _user_msg("1"),       # 0
            _assistant_msg("2"),  # 1
            _user_msg("3"),       # 2
            _assistant_msg("4"),  # 3
            _user_msg("5"),       # 4
            _assistant_msg("6"),  # 5
        ]
        # Target index 3 (ModelResponse) should snap forward to index 4 (ModelRequest)
        split = _find_split_point(messages, 3)
        assert isinstance(messages[split], ModelRequest)

    def test_respects_min_recent(self):
        messages: list[ModelMessage] = [
            _user_msg("1"),
            _assistant_msg("2"),
            _user_msg("3"),
            _assistant_msg("4"),
            _user_msg("5"),
        ]
        split = _find_split_point(messages, 4)
        remaining = len(messages) - split
        assert remaining >= _MIN_RECENT_MESSAGES or split == 0

    def test_exact_model_request_target(self):
        """If target already is a ModelRequest, use it directly."""
        messages: list[ModelMessage] = [
            _user_msg("1"),       # 0
            _assistant_msg("2"),  # 1
            _user_msg("3"),       # 2
            _assistant_msg("4"),  # 3
            _user_msg("5"),       # 4
            _assistant_msg("6"),  # 5
            _user_msg("7"),       # 6
            _assistant_msg("8"),  # 7
        ]
        split = _find_split_point(messages, 4)
        assert split == 4
        assert isinstance(messages[split], ModelRequest)


class TestMessagesToText:
    """Tests for the _messages_to_text helper."""

    def test_includes_all_message_types(self):
        messages: list[ModelMessage] = [
            _user_msg("Find maintenance tasks"),
            _tool_call_response("search_tasks", "tc1", json.dumps({"query": "maintenance"})),
            _tool_return_request("search_tasks", "3 tasks found", "tc1"),
            _assistant_msg("Here are the maintenance tasks"),
        ]

        text = _messages_to_text(messages)
        assert "USER: Find maintenance tasks" in text
        assert "TOOL CALL (search_tasks)" in text
        assert "TOOL RESULT (search_tasks): 3 tasks found" in text
        assert "ASSISTANT: Here are the maintenance tasks" in text

    def test_truncates_long_tool_results(self):
        """Tool results over 2000 chars should be truncated in transcript."""
        long_content = "x" * 3000
        messages: list[ModelMessage] = [
            _tool_return_request("search_tasks", long_content, "tc1"),
        ]

        text = _messages_to_text(messages)
        assert "...(truncated)" in text
        # Should be much shorter than original
        assert len(text) < len(long_content)
