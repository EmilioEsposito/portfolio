"""
History processor that summarizes oversized tool results in older messages.

Runs before each model request via PydanticAI's history_processors API.
Only touches ToolReturnParts that exceed SUMMARIZATION_CHAR_THRESHOLD and
are NOT in the current turn (the agent is actively using those results).
"""

import logfire
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
)

from api.src.sernia_ai.config import SUB_AGENT_MODEL, SUMMARIZATION_CHAR_THRESHOLD
from api.src.sernia_ai.deps import SerniaDeps

# Module-level summarizer agent (Claude Haiku, no tools)
_summarizer = Agent(
    SUB_AGENT_MODEL,
    instructions=(
        "You are a concise summarizer. Given a tool result, produce a short summary "
        "that preserves all key facts, numbers, names, dates, and actionable details. "
        "Omit boilerplate, repeated headers, and raw formatting. "
        "Keep the summary under 500 words."
    ),
    instrument=True,
    name="tool_result_summarizer",
)

# Cap input to the summarizer to protect Haiku's context
_MAX_SUMMARIZER_INPUT_CHARS = 50_000


def _find_current_turn_boundary(messages: list[ModelMessage]) -> int:
    """Find the index where the current turn starts.

    Walk backward from the end. The current turn is the final sequence of
    messages that form an ongoing tool-call/return cycle:
    - ModelResponse (with tool calls) followed by ModelRequest (with tool returns)
    - The initial ModelRequest (user prompt) that kicked off this turn

    Everything at or after this index is "current turn" and should not be touched.
    """
    if not messages:
        return 0

    i = len(messages) - 1

    # Walk backward over tool-call/return pairs
    while i >= 0:
        msg = messages[i]
        if isinstance(msg, ModelRequest):
            # Check if this request contains tool returns (part of tool cycle)
            has_tool_return = any(isinstance(p, ToolReturnPart) for p in msg.parts)
            if has_tool_return:
                # This is a tool-return request; the response before it is also current turn
                i -= 1
                continue
            else:
                # This is a user prompt — it's the start of the current turn
                break
        elif isinstance(msg, ModelResponse):
            # Could be a tool-call response in the current cycle
            # Check if the next message (i+1) is a tool-return request
            if i + 1 < len(messages) and isinstance(messages[i + 1], ModelRequest):
                next_msg = messages[i + 1]
                has_tool_return = any(isinstance(p, ToolReturnPart) for p in next_msg.parts)
                if has_tool_return:
                    i -= 1
                    continue
            # This response is the latest one (end of conversation), part of current turn
            break
        else:
            break

    return max(i, 0)


async def summarize_tool_results(
    ctx: RunContext[SerniaDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Summarize oversized tool results in older messages.

    - Finds the current turn boundary (those messages are untouched).
    - Scans older ModelRequest messages for ToolReturnParts exceeding the threshold.
    - Calls the summarizer sub-agent for each oversized result.
    - On failure, preserves the original content (fail-safe).
    """
    if len(messages) <= 1:
        return messages

    boundary = _find_current_turn_boundary(messages)
    older_messages = messages[:boundary]
    current_messages = messages[boundary:]

    # Find oversized tool returns in older messages
    oversized: list[tuple[int, int, ToolReturnPart]] = []  # (msg_idx, part_idx, part)
    for msg_idx, msg in enumerate(older_messages):
        if isinstance(msg, ModelRequest):
            for part_idx, part in enumerate(msg.parts):
                if isinstance(part, ToolReturnPart):
                    content_str = str(part.content) if not isinstance(part.content, str) else part.content
                    if len(content_str) > SUMMARIZATION_CHAR_THRESHOLD:
                        oversized.append((msg_idx, part_idx, part))

    if not oversized:
        return messages

    logfire.info(f"Summarizing {len(oversized)} oversized tool results")

    # Build new older_messages with summarized tool returns
    new_older = [msg for msg in older_messages]  # shallow copy of list
    for msg_idx, part_idx, part in oversized:
        content_str = str(part.content) if not isinstance(part.content, str) else part.content
        truncated = content_str[:_MAX_SUMMARIZER_INPUT_CHARS]

        try:
            result = await _summarizer.run(
                f"Summarize this {part.tool_name} tool result:\n\n{truncated}"
            )
            summary_text = f"[Summarized {part.tool_name} result]: {result.output}"

            # Build replacement part preserving metadata
            new_part = ToolReturnPart(
                tool_name=part.tool_name,
                content=summary_text,
                tool_call_id=part.tool_call_id,
                timestamp=part.timestamp,
            )

            # Replace in the request's parts list
            original_request = new_older[msg_idx]
            assert isinstance(original_request, ModelRequest)
            new_parts = list(original_request.parts)
            new_parts[part_idx] = new_part
            new_older[msg_idx] = ModelRequest(parts=new_parts)
        except Exception:
            logfire.exception(f"Failed to summarize tool result {part.tool_name}, keeping original")
            # Fail-safe: keep original

    return new_older + current_messages
