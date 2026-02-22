"""
Dynamic instruction functions for the Sernia Capital AI agent.

Each function is registered with @agent.instructions and injects
context at the start of every agent run.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic_ai import Agent, RunContext

from api.src.ai_sernia.deps import SerniaDeps

# Caps to avoid blowing up context window
MEMORY_CHAR_CAP = 5_000
DAILY_NOTES_CHAR_CAP = 2_000


def register_instructions(agent: Agent) -> None:
    """Register all dynamic instruction functions on the agent."""

    @agent.instructions
    def inject_context(ctx: RunContext[SerniaDeps]) -> str:
        now = datetime.now(ZoneInfo("America/New_York"))
        return (
            f"Current: {now.strftime('%Y-%m-%d %I:%M %p ET')}. "
            f"Speaking with {ctx.deps.user_name} via {ctx.deps.modality}."
        )

    @agent.instructions
    def inject_memory(ctx: RunContext[SerniaDeps]) -> str:
        memory_file = ctx.deps.workspace_path / "MEMORY.md"
        if not memory_file.exists():
            return ""
        content = memory_file.read_text(encoding="utf-8")
        if len(content) > MEMORY_CHAR_CAP:
            content = content[:MEMORY_CHAR_CAP] + "\n...(truncated)"
        return f"## Your Long-Term Memory\n{content}"

    @agent.instructions
    def inject_daily_notes(ctx: RunContext[SerniaDeps]) -> str:
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        notes_file = ctx.deps.workspace_path / "daily_notes" / f"{today}.md"
        if not notes_file.exists():
            return ""
        content = notes_file.read_text(encoding="utf-8")
        if len(content) > DAILY_NOTES_CHAR_CAP:
            content = content[:DAILY_NOTES_CHAR_CAP] + "\n...(truncated)"
        return f"## Today's Notes ({today})\n{content}"

    @agent.instructions
    def inject_modality_guidance(ctx: RunContext[SerniaDeps]) -> str:
        guidance = {
            "sms": (
                "You are communicating via SMS. Keep responses SHORT (1-3 sentences). "
                "No markdown formatting. Be direct and casual."
            ),
            "email": (
                "You are communicating via email. Use a professional, slightly formal tone. "
                "Structure with paragraphs. Include greetings/closings when appropriate."
            ),
            "web_chat": (
                "You are in a web chat. Use a natural, conversational tone. "
                "Markdown formatting is supported. Be helpful and thorough."
            ),
        }
        return guidance.get(ctx.deps.modality, "")
