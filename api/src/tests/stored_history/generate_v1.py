"""Write conversation histories in the JSON pydantic-ai 1.x stored them as.

Not a test, and not runnable in this repo's environment: it needs pydantic-ai
1.107.6, the last 1.x release production ran. It produced
``pydantic_ai_1_107_6.json``, which ``test_stored_history_v1.py`` loads under
the installed pydantic-ai. To regenerate:

    uv venv /tmp/pai1 && VIRTUAL_ENV=/tmp/pai1 uv pip install "pydantic-ai-slim[openai]==1.107.6"
    /tmp/pai1/bin/python api/src/tests/stored_history/generate_v1.py \
        api/src/tests/stored_history/pydantic_ai_1_107_6.json send_lead_reply

The data is made up. The second argument is ``zillow_leads.handle.SEND_REPLY``.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime

import pydantic_ai
from pydantic_ai import Agent, ApprovalRequired, DeferredToolRequests
from pydantic_ai.messages import (
    BuiltinToolCallPart,
    BuiltinToolReturnPart,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

assert pydantic_ai.__version__.startswith("1."), pydantic_ai.__version__
T = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

lead = [
    ModelRequest(parts=[UserPromptPart(content="Case: is 12 Example St available?", timestamp=T)]),
    ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=sys.argv[2],
                args=json.dumps(
                    {
                        "to": "abc123@convo.zillow.com",
                        "subject": "Re: 12 Example St inquiry",
                        "body": "Hello,\n\nSernia Capital Team",
                        "why": "Because.",
                        "team_notes": None,
                    }
                ),
                tool_call_id="lead-0123456789abcdef",
            )
        ],
        model_name="openrouter:test/lead",
        timestamp=T,
    ),
]

step = {"n": 0}


def model(messages, info):
    step["n"] += 1
    if step["n"] == 1:
        return ModelResponse(
            parts=[
                ThinkingPart(
                    content="", id="rs_AAA", signature="ENC_AAA", provider_name="openrouter"
                ),
                ToolCallPart("lookup", {"q": "tenant"}, tool_call_id="call_lookup"),
                ToolCallPart("flaky", {"n": "not-an-int"}, tool_call_id="call_flaky"),
            ]
        )
    if step["n"] == 2:
        return ModelResponse(parts=[ToolCallPart("flaky", {"n": 1}, tool_call_id="call_flaky2")])
    return ModelResponse(
        parts=[
            TextPart("Sending now."),
            ToolCallPart(
                "send_sms", {"to": "+15550001111", "message": "Hi"}, tool_call_id="call_sms"
            ),
        ]
    )


agent = Agent(
    FunctionModel(model), output_type=[str, DeferredToolRequests], instructions="Be brief."
)


@agent.tool_plain
def lookup(q: str) -> str:
    return f"found {q}"


@agent.tool_plain
def flaky(n: int) -> str:
    return "ok"


@agent.tool_plain
def send_sms(to: str, message: str) -> str:
    raise ApprovalRequired()


async def main():
    result = await agent.run("Text the tenant.")
    assert isinstance(result.output, DeferredToolRequests), result.output
    general = list(result.all_messages())
    # Parts older conversations hold: a tool-less retry, and Anthropic's server-side search.
    extra = [
        ModelRequest(
            parts=[RetryPromptPart(content="Please return text or call a tool.", timestamp=T)]
        ),
        ModelResponse(
            parts=[
                BuiltinToolCallPart(
                    tool_name="web_search",
                    args={"query": "rent"},
                    tool_call_id="srv_1",
                    provider_name="anthropic",
                ),
                BuiltinToolReturnPart(
                    tool_name="web_search",
                    content=[{"url": "https://zillow.com"}],
                    tool_call_id="srv_1",
                    provider_name="anthropic",
                    timestamp=T,
                ),
                TextPart("Rents are up."),
            ],
            model_name="claude-sonnet-4-6",
            timestamp=T,
        ),
    ]
    general = general[:1] + general[1:-1] + extra + general[-1:]
    out = {
        "pydantic_ai_version": pydantic_ai.__version__,
        "lead_pending_reply": ModelMessagesTypeAdapter.dump_python(lead, mode="json"),
        "general_pending_approval": ModelMessagesTypeAdapter.dump_python(general, mode="json"),
    }
    json.dump(out, open(sys.argv[1], "w"), indent=2)
    kinds = sorted({p["part_kind"] for m in out["general_pending_approval"] for p in m["parts"]})
    print("wrote", sys.argv[1], kinds)


asyncio.run(main())
