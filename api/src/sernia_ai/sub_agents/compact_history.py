"""
History processor that compacts conversation history when approaching token limits.

Runs after summarize_tool_results via PydanticAI's history_processors API.
Uses the last ModelResponse's input_tokens as the best proxy for current context size,
then summarizes the older half of the conversation when approaching 85% of the window.
"""

import logfire
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from api.src.sernia_ai.config import SUB_AGENT_MODEL, TOKEN_COMPACTION_THRESHOLD
from api.src.sernia_ai.deps import SerniaDeps

# Module-level compactor agent (Claude Haiku, no tools)
_compactor = Agent(
    SUB_AGENT_MODEL,
    instructions=(
        "You are a conversation summarizer. Given a conversation transcript, "
        "produce a structured summary that preserves:\n"
        "- All decisions made and their reasoning\n"
        "- Key facts, numbers, names, dates, and outcomes\n"
        "- Tool results and their conclusions\n"
        "- Any pending tasks or open questions\n"
        "- User preferences and instructions expressed\n\n"
        "Be thorough but concise. Use bullet points. "
        "The summary will replace the original messages, so nothing important should be lost."
    ),
    instrument=True,
    name="history_compactor",
)

# Cap input to the compactor to protect Haiku's context
_MAX_COMPACTOR_INPUT_CHARS = 80_000

# Minimum recent messages to keep (never compact too aggressively)
_MIN_RECENT_MESSAGES = 4


def _estimate_tokens(messages: list[ModelMessage]) -> int | None:
    """Get token estimate from the last ModelResponse's input_tokens.

    This is the best proxy for current context size — it reflects what the model
    actually processed, avoiding the double-counting problem of summing all responses.
    """
    for msg in reversed(messages):
        if isinstance(msg, ModelResponse) and msg.usage.input_tokens:
            return msg.usage.input_tokens
    return None


def _find_split_point(messages: list[ModelMessage], target_index: int) -> int:
    """Find a split point near target_index that lands on a ModelRequest boundary.

    Snaps forward to the nearest ModelRequest to avoid splitting mid-turn.
    Ensures at least _MIN_RECENT_MESSAGES are kept in the recent portion.
    """
    max_split = len(messages) - _MIN_RECENT_MESSAGES
    if max_split <= 0:
        return 0

    # Start at target, snap forward to a ModelRequest boundary
    for i in range(target_index, max_split + 1):
        if i < len(messages) and isinstance(messages[i], ModelRequest):
            return i

    # If no ModelRequest found forward, snap backward
    for i in range(min(target_index, max_split), -1, -1):
        if isinstance(messages[i], ModelRequest):
            return i

    return max(target_index, 0)


def _messages_to_text(messages: list[ModelMessage]) -> str:
    """Convert messages to a human-readable transcript for the compactor."""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    lines.append(f"USER: {part.content}")
                elif isinstance(part, ToolReturnPart):
                    content_str = str(part.content) if not isinstance(part.content, str) else part.content
                    # Truncate very long tool results in the transcript
                    if len(content_str) > 2000:
                        content_str = content_str[:2000] + "...(truncated)"
                    lines.append(f"TOOL RESULT ({part.tool_name}): {content_str}")
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    lines.append(f"ASSISTANT: {part.content}")
                elif isinstance(part, ToolCallPart):
                    lines.append(f"TOOL CALL ({part.tool_name}): {part.args}")
    return "\n\n".join(lines)


async def compact_history(
    ctx: RunContext[SerniaDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Compact conversation history when approaching token limits.

    - Estimates current token usage from the last ModelResponse.
    - If under TOKEN_COMPACTION_THRESHOLD, passes through unchanged.
    - Otherwise, splits at ~50% of messages, summarizes the older half,
      and returns [summary_message, ...recent_messages].
    - On failure, returns original messages (fail-safe).
    """
    if len(messages) <= _MIN_RECENT_MESSAGES:
        return messages

    estimated = _estimate_tokens(messages)
    if estimated is None or estimated < TOKEN_COMPACTION_THRESHOLD:
        return messages

    logfire.info(
        f"History compaction triggered: ~{estimated:,} tokens "
        f"(threshold: {TOKEN_COMPACTION_THRESHOLD:,})"
    )

    # Split at ~50% of messages, snapping to a ModelRequest boundary
    target = len(messages) // 2
    split = _find_split_point(messages, target)

    if split == 0:
        # Can't split meaningfully
        return messages

    older = messages[:split]
    recent = messages[split:]

    # Convert older messages to text and cap for Haiku
    transcript = _messages_to_text(older)
    if len(transcript) > _MAX_COMPACTOR_INPUT_CHARS:
        transcript = transcript[:_MAX_COMPACTOR_INPUT_CHARS] + "\n\n...(transcript truncated)"

    try:
        result = await _compactor.run(
            f"Summarize this conversation history:\n\n{transcript}"
        )

        summary_message = ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "[Conversation summary — earlier messages were compacted "
                        "to save context space]\n\n"
                        f"{result.output}"
                    )
                )
            ]
        )

        compacted = [summary_message] + recent
        logfire.info(
            f"History compacted: {len(messages)} messages -> {len(compacted)} messages "
            f"({len(older)} older messages summarized)"
        )
        return compacted
    except Exception:
        logfire.exception("Failed to compact history, keeping original messages")
        return messages
