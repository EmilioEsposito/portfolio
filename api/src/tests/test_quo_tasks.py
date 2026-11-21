"""Exercise Quo task writes and the real Sernia deletion gate with a synthetic model."""

import json

import httpx
import pytest
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from api.src.sernia_ai.tools import quo_tools


@pytest.mark.asyncio
async def test_task_lifecycle(monkeypatch):
    calls = []
    operations = iter(
        [
            ("quo_create_task", {"title": "Call back", "description": "Tomorrow"}),
            ("quo_update_task", {"task_id": "TK1", "change": {"completed": True}}),
            ("quo_delete_task", {"task_id": "TK1"}),
        ]
    )

    def model(messages, info):
        operation = next(operations, None)
        if operation is None:
            return ModelResponse(parts=[TextPart("Done")])
        name, args = operation
        return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=name)])

    def provider(request):
        calls.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"data": {"taskId": "TK1", "revision": "r1"}})

    async with httpx.AsyncClient(
        base_url="https://quo.test", transport=httpx.MockTransport(provider)
    ) as client:
        monkeypatch.setattr(quo_tools, "_build_quo_client", lambda: client)
        monkeypatch.setattr(
            quo_tools,
            "_fetch_and_patch_spec",
            lambda: {
                "openapi": "3.1.0",
                "info": {"title": "test", "version": "1"},
                "paths": {},
            },
        )
        agent = Agent(
            FunctionModel(model),
            toolsets=[quo_tools._build_quo_toolset().prefixed("quo")],
            output_type=[str, DeferredToolRequests],
        )
        pending = await agent.run("Create, complete and delete a Quo task.")
        assert isinstance(pending.output, DeferredToolRequests)
        assert [call.tool_name for call in pending.output.approvals] == ["quo_delete_task"]
        assert calls == [
            ("POST", "/v1/tasks", {"title": "Call back", "description": "Tomorrow"}),
            ("POST", "/v1/tasks/TK1/complete", None),
        ]
        result = await agent.run(
            message_history=pending.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={"quo_delete_task": True}),
        )
        assert result.output == "Done"
        assert calls[-1] == ("DELETE", "/v1/tasks/TK1", None)
