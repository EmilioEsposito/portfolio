"""
Analyze what's consuming input tokens in Sernia AI agent conversations.

Breaks down token usage by:
- System prompt (static instructions + dynamic injections)
- Tool descriptions (JSON schemas sent with every LLM call)
- Tool results (returned data from tool calls)
- User messages (human prompts)
- Assistant history (prior assistant turns re-sent as context)
- Tool call args (assistant's tool invocations, small but counted)

Run: cd /Users/eesposito/portfolio && python -m api.src.adhoc.analyze_input_tokens
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
import tiktoken
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

# cl100k_base is a reasonable proxy for Claude token counting (~10% margin)
enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str | None) -> int:
    if not text:
        return 0
    if isinstance(text, dict):
        text = json.dumps(text)
    return len(enc.encode(str(text)))


# ---------------------------------------------------------------------------
# Part 1: Analyze stored conversations
# ---------------------------------------------------------------------------
def analyze_conversations(limit: int = 200):
    """Analyze token breakdown across stored conversations."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, agent_name, messages, estimated_tokens, run_count,
               modality, contact_identifier, created_at
        FROM agent_conversations
        WHERE agent_name = 'sernia'
        ORDER BY updated_at DESC
        LIMIT %s
    """, (limit,))

    rows = cur.fetchall()
    print(f"\n{'='*70}")
    print(f"SERNIA AI INPUT TOKEN ANALYSIS")
    print(f"{'='*70}")
    print(f"Conversations analyzed: {len(rows)}")

    # Aggregate stats
    totals = defaultdict(int)
    per_conversation = []
    part_type_examples = defaultdict(list)  # track largest examples

    for row in rows:
        conv_id, agent_name, messages, est_tokens, run_count, modality, contact, created_at = row
        if not messages:
            continue

        conv_stats = defaultdict(int)
        conv_stats["conversation_id"] = conv_id
        conv_stats["modality"] = modality or "unknown"
        conv_stats["run_count"] = run_count or 1
        conv_stats["message_count"] = len(messages)

        for msg in messages:
            msg_type = msg.get("kind") or msg.get("type", "unknown")
            parts = msg.get("parts", [])

            for part in parts:
                part_type = part.get("part_kind") or part.get("type", "unknown")
                content = ""

                if part_type in ("system-prompt", "SystemPromptPart"):
                    content = part.get("content", "")
                    category = "system_prompt"
                elif part_type in ("user-prompt", "UserPromptPart"):
                    content = part.get("content", "")
                    category = "user_prompt"
                elif part_type in ("tool-return", "ToolReturnPart"):
                    content = part.get("content", "")
                    if isinstance(content, dict):
                        content = json.dumps(content)
                    tool_name = part.get("tool_name", "unknown")
                    category = "tool_result"

                    # Track per-tool breakdown
                    tokens = count_tokens(content)
                    tool_cat = f"tool_result:{tool_name}"
                    conv_stats[tool_cat] += tokens
                    totals[tool_cat] += tokens

                    # Track large examples
                    if tokens > 500:
                        part_type_examples[tool_name].append({
                            "tokens": tokens,
                            "conv_id": conv_id[:8],
                            "preview": str(content)[:120],
                        })

                elif part_type in ("text", "TextPart"):
                    # Assistant text — becomes input on subsequent calls
                    if msg_type in ("response", "ModelResponse"):
                        content = part.get("content", "")
                        category = "assistant_history"
                    else:
                        content = part.get("content", "")
                        category = "user_prompt"
                elif part_type in ("tool-call", "ToolCallPart"):
                    # Tool call from assistant — args become input on re-send
                    args = part.get("args", "")
                    if isinstance(args, dict):
                        args = json.dumps(args)
                    content = args
                    category = "tool_call_args"
                elif part_type in ("retry-prompt", "RetryPromptPart"):
                    content = part.get("content", "")
                    category = "retry_prompt"
                else:
                    content = json.dumps(part)
                    category = f"other:{part_type}"

                tokens = count_tokens(content)
                conv_stats[category] += tokens
                totals[category] += tokens

        per_conversation.append(conv_stats)

    # ---------------------------------------------------------------------------
    # Print summary
    # ---------------------------------------------------------------------------

    # Main categories
    main_categories = [
        "system_prompt", "user_prompt", "tool_result",
        "assistant_history", "tool_call_args", "retry_prompt",
    ]
    other_cats = [k for k in totals if k not in main_categories and not k.startswith("tool_result:")]

    grand_total = sum(totals[c] for c in main_categories) + sum(totals[c] for c in other_cats)

    print(f"\n{'─'*70}")
    print(f"TOKEN BREAKDOWN BY CATEGORY (across {len(per_conversation)} conversations)")
    print(f"{'─'*70}")
    print(f"{'Category':<30} {'Tokens':>12} {'%':>8}")
    print(f"{'─'*30} {'─'*12} {'─'*8}")

    for cat in main_categories:
        t = totals[cat]
        pct = (t / grand_total * 100) if grand_total else 0
        print(f"{cat:<30} {t:>12,} {pct:>7.1f}%")

    for cat in sorted(other_cats):
        t = totals[cat]
        pct = (t / grand_total * 100) if grand_total else 0
        print(f"{cat:<30} {t:>12,} {pct:>7.1f}%")

    print(f"{'─'*30} {'─'*12} {'─'*8}")
    print(f"{'TOTAL':<30} {grand_total:>12,} {'100.0%':>8}")

    print(f"\n  NOTE: system_prompt = 0 is expected. PydanticAI injects system prompt")
    print(f"  and tool descriptions directly into each API call — they're NOT stored")
    print(f"  in the conversation messages. Add ~7,500 tokens/call for those.")
    print(f"  (See sections below for system prompt + tool description estimates.)")

    # User prompt size distribution
    user_sizes = []
    for conv in per_conversation:
        user_sizes.append(conv.get("user_prompt", 0))
    if user_sizes:
        big = [(c.get("user_prompt", 0), c.get("conversation_id", "?")[:8], c.get("modality", "?"))
               for c in per_conversation if c.get("user_prompt", 0) > 1000]
        if big:
            print(f"\n{'─'*70}")
            print(f"USER PROMPT OUTLIERS (>1,000 tokens) — likely trigger payloads")
            print(f"{'─'*70}")
            for tokens, cid, modality in sorted(big, key=lambda x: -x[0])[:10]:
                print(f"  {tokens:>10,} tokens  conv={cid}  modality={modality}")

    # Tool result breakdown
    tool_cats = {k: v for k, v in totals.items() if k.startswith("tool_result:")}
    if tool_cats:
        tool_total = sum(tool_cats.values())
        print(f"\n{'─'*70}")
        print(f"TOOL RESULT BREAKDOWN (what's inside 'tool_result' tokens)")
        print(f"{'─'*70}")
        print(f"{'Tool Name':<40} {'Tokens':>12} {'% of tools':>10} {'Calls':>8}")
        print(f"{'─'*40} {'─'*12} {'─'*10} {'─'*8}")

        # Count calls per tool
        tool_call_counts = defaultdict(int)
        for conv in per_conversation:
            for k in conv:
                if k.startswith("tool_result:"):
                    if conv[k] > 0:
                        tool_call_counts[k] += 1

        for cat, t in sorted(tool_cats.items(), key=lambda x: -x[1]):
            name = cat.replace("tool_result:", "")
            pct = (t / tool_total * 100) if tool_total else 0
            calls = tool_call_counts[cat]
            avg = t // calls if calls else 0
            print(f"{name:<40} {t:>12,} {pct:>9.1f}% {calls:>8}  (avg {avg:,}/call)")

        print(f"{'─'*40} {'─'*12} {'─'*10} {'─'*8}")
        print(f"{'TOTAL':<40} {tool_total:>12,}")

    # Per-conversation stats
    print(f"\n{'─'*70}")
    print(f"PER-CONVERSATION STATS")
    print(f"{'─'*70}")

    conv_tool_results = [c.get("tool_result", 0) for c in per_conversation]
    conv_system = [c.get("system_prompt", 0) for c in per_conversation]
    conv_user = [c.get("user_prompt", 0) for c in per_conversation]
    conv_assistant = [c.get("assistant_history", 0) for c in per_conversation]
    conv_msgs = [c.get("message_count", 0) for c in per_conversation]

    def stats(arr, name):
        if not arr or max(arr) == 0:
            return
        avg = sum(arr) / len(arr) if arr else 0
        mx = max(arr) if arr else 0
        p50 = sorted(arr)[len(arr) // 2] if arr else 0
        p90 = sorted(arr)[int(len(arr) * 0.9)] if arr else 0
        print(f"  {name:<25} avg={avg:>8,.0f}  p50={p50:>8,}  p90={p90:>8,}  max={mx:>8,}")

    stats(conv_tool_results, "tool_results")
    stats(conv_system, "system_prompt")
    stats(conv_user, "user_prompt")
    stats(conv_assistant, "assistant_history")
    stats(conv_msgs, "message_count")

    # Largest tool results
    print(f"\n{'─'*70}")
    print(f"LARGEST TOOL RESULTS (>500 tokens)")
    print(f"{'─'*70}")
    for tool_name, examples in sorted(part_type_examples.items(), key=lambda x: -max(e["tokens"] for e in x[1])):
        top = sorted(examples, key=lambda x: -x["tokens"])[:3]
        print(f"\n  {tool_name}:")
        for ex in top:
            print(f"    {ex['tokens']:>6,} tokens (conv {ex['conv_id']}) — {ex['preview'][:80]}...")

    # Multi-turn analysis: how much is "re-sent history"?
    print(f"\n{'─'*70}")
    print(f"MULTI-TURN HISTORY GROWTH")
    print(f"{'─'*70}")
    print(f"  In multi-turn conversations, ALL prior messages are re-sent as input.")
    print(f"  This means a 5-turn conversation sends turns 1-4 as input for turn 5.")
    print()

    multi_turn = [c for c in per_conversation if c.get("run_count", 1) > 1]
    single_turn = [c for c in per_conversation if c.get("run_count", 1) <= 1]

    if multi_turn:
        mt_total = sum(c.get("tool_result", 0) + c.get("system_prompt", 0) +
                       c.get("user_prompt", 0) + c.get("assistant_history", 0) +
                       c.get("tool_call_args", 0) for c in multi_turn)
        mt_history = sum(c.get("assistant_history", 0) + c.get("tool_call_args", 0)
                         for c in multi_turn)
        print(f"  Multi-turn conversations: {len(multi_turn)}")
        print(f"  Single-turn conversations: {len(single_turn)}")
        print(f"  Multi-turn total tokens: {mt_total:,}")
        print(f"  Multi-turn history tokens: {mt_history:,} ({mt_history/mt_total*100:.1f}% is re-sent history)")

    conn.close()


# ---------------------------------------------------------------------------
# Part 2: Estimate tool description overhead
# ---------------------------------------------------------------------------
def analyze_tool_descriptions():
    """Estimate tokens consumed by tool definitions (sent with every LLM call)."""
    print(f"\n{'='*70}")
    print(f"TOOL DESCRIPTION TOKEN OVERHEAD")
    print(f"{'='*70}")
    print(f"Tool descriptions (JSON schemas) are sent with EVERY LLM API call.")
    print(f"These are NOT stored in conversation messages but count as input tokens.\n")

    # Try to import the sernia agent and inspect tools
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from api.src.sernia_ai.agent import sernia_agent

        tools = sernia_agent._function_tools
        print(f"Registered tools: {len(tools)}")
        print(f"\n{'Tool Name':<40} {'Schema Tokens':>14}")
        print(f"{'─'*40} {'─'*14}")

        tool_tokens = {}
        for name, tool in sorted(tools.items()):
            # Build approximate schema as it would be sent to the API
            schema = {
                "name": name,
                "description": getattr(tool, 'description', '') or '',
            }
            # Try to get parameter schema
            if hasattr(tool, 'parameters_json_schema'):
                schema["input_schema"] = tool.parameters_json_schema
            elif hasattr(tool, '_parameters_json_schema'):
                schema["input_schema"] = tool._parameters_json_schema

            tokens = count_tokens(json.dumps(schema))
            tool_tokens[name] = tokens
            print(f"  {name:<38} {tokens:>12,}")

        total = sum(tool_tokens.values())
        print(f"{'─'*40} {'─'*14}")
        print(f"  {'TOTAL':<38} {total:>12,}")
        print(f"\n  This {total:,} tokens is added to EVERY LLM call in the conversation.")
        print(f"  For a 5-turn conversation with 3 LLM calls/turn, that's ~{total * 15:,} tokens just for tools.")

    except Exception as e:
        print(f"  Could not import sernia agent: {e}")
        print(f"  (This is expected if running outside the full app context)")
        print(f"\n  Based on codebase analysis (~49 tools):")
        print(f"  Estimated tool description overhead: ~3,500-4,000 tokens per LLM call")
        print(f"  With Anthropic prompt caching, subsequent calls in same conversation")
        print(f"  may use cached versions (reducing effective cost significantly).")


# ---------------------------------------------------------------------------
# Part 3: Estimate system prompt overhead
# ---------------------------------------------------------------------------
def analyze_system_prompt():
    """Estimate system prompt token overhead."""
    print(f"\n{'='*70}")
    print(f"SYSTEM PROMPT TOKEN OVERHEAD")
    print(f"{'='*70}")

    instructions_path = Path(__file__).resolve().parents[1] / "sernia_ai" / "instructions.py"
    if not instructions_path.exists():
        print(f"  Could not find {instructions_path}")
        return

    content = instructions_path.read_text()
    # Find the main instructions string
    total_static = count_tokens(content)

    print(f"  instructions.py file: ~{total_static:,} tokens (includes Python code)")
    print(f"\n  The system prompt is composed of:")
    print(f"    - Static instructions: ~2,000-2,500 tokens (role, rules, tool guidance)")
    print(f"    - inject_context(): ~100 tokens (datetime, user, modality)")
    print(f"    - inject_memory(): MEMORY.md verbatim (warns past 100K chars)")
    print(f"    - inject_filetree(): up to ~800 tokens (workspace tree, capped at 3K chars)")
    print(f"    - inject_modality_guidance(): ~200-400 tokens (SMS/email/web rules)")
    print(f"\n  Estimated total system prompt: ~3,500-5,500 tokens per LLM call")
    print(f"  With prompt caching, only the first call pays full cost.")


# ---------------------------------------------------------------------------
# Part 4: Cost projection
# ---------------------------------------------------------------------------
def cost_projection():
    """Show where the money actually goes."""
    print(f"\n{'='*70}")
    print(f"INPUT TOKEN COST DRIVERS (SUMMARY)")
    print(f"{'='*70}")
    print(f"""
  For a SINGLE LLM API call, input tokens come from:

  ┌─────────────────────────────────────────────────────────────┐
  │  System Prompt          ~4,000 tokens  (cached after 1st)   │
  │  Tool Descriptions      ~3,500 tokens  (cached after 1st)   │
  │  ─────────────────────────────────────────────────────       │
  │  Conversation History   VARIABLE (grows each turn)          │
  │    ├── Prior user msgs                                      │
  │    ├── Prior assistant msgs                                 │
  │    ├── Prior tool calls                                     │
  │    └── Prior tool results  ← BIGGEST DRIVER                 │
  │  ─────────────────────────────────────────────────────       │
  │  Current user message   ~50-200 tokens                      │
  └─────────────────────────────────────────────────────────────┘

  Key insight: Tool RESULTS dominate input tokens because:
  1. They're often large (email content, search results, calendar data)
  2. They're re-sent in full on every subsequent LLM call
  3. A single tool result can be 2,000-10,000+ tokens
  4. Multi-tool-call turns compound: 3 tools × 3,000 tokens = 9,000 per turn

  Optimization levers:
  - Truncate/summarize tool results before returning
  - Use history summarization/pruning for long conversations
  - Prompt caching (already enabled) helps with system prompt + tools
  - Consider result_type on tools to return structured, smaller responses
""")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    analyze_conversations(limit=200)
    analyze_tool_descriptions()
    analyze_system_prompt()
    cost_projection()
