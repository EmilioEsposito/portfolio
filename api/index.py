import os
import json
from typing import List
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import OpenAI
from .utils.prompt import ClientMessage, convert_to_openai_messages
from .utils.tools import get_current_weather
from datetime import datetime


load_dotenv(".env")

app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)


class ChatRequest(BaseModel):
    messages: List[ClientMessage]


available_tools = {
    "get_current_weather": get_current_weather,
}


def do_stream(messages: List[ChatCompletionMessageParam]):
    stream = client.chat.completions.create(
        messages=messages,
        model="gpt-4o",
        stream=True,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_current_weather",
                    "description": "Get the current weather at a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "latitude": {
                                "type": "number",
                                "description": "The latitude of the location",
                            },
                            "longitude": {
                                "type": "number",
                                "description": "The longitude of the location",
                            },
                        },
                        "required": ["latitude", "longitude"],
                    },
                },
            }
        ],
    )

    return stream


def stream_text(messages: List[ChatCompletionMessageParam], protocol: str = "data"):
    draft_tool_calls = []
    draft_tool_calls_index = -1

    stream = client.chat.completions.create(
        messages=messages,
        model="gpt-4o",
        stream=True,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_current_weather",
                    "description": "Get the current weather at a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "latitude": {
                                "type": "number",
                                "description": "The latitude of the location",
                            },
                            "longitude": {
                                "type": "number",
                                "description": "The longitude of the location",
                            },
                        },
                        "required": ["latitude", "longitude"],
                    },
                },
            }
        ],
    )

    for chunk in stream:
        for choice in chunk.choices:
            if choice.finish_reason == "stop":
                continue

            elif choice.finish_reason == "tool_calls":
                for tool_call in draft_tool_calls:
                    yield '9:{{"toolCallId":"{id}","toolName":"{name}","args":{args}}}\n'.format(
                        id=tool_call["id"],
                        name=tool_call["name"],
                        args=tool_call["arguments"],
                    )

                for tool_call in draft_tool_calls:
                    tool_result = available_tools[tool_call["name"]](
                        **json.loads(tool_call["arguments"])
                    )

                    yield 'a:{{"toolCallId":"{id}","toolName":"{name}","args":{args},"result":{result}}}\n'.format(
                        id=tool_call["id"],
                        name=tool_call["name"],
                        args=tool_call["arguments"],
                        result=json.dumps(tool_result),
                    )

            elif choice.delta.tool_calls:
                for tool_call in choice.delta.tool_calls:
                    id = tool_call.id
                    name = tool_call.function.name
                    arguments = tool_call.function.arguments

                    if id is not None:
                        draft_tool_calls_index += 1
                        draft_tool_calls.append(
                            {"id": id, "name": name, "arguments": ""}
                        )

                    else:
                        draft_tool_calls[draft_tool_calls_index][
                            "arguments"
                        ] += arguments

            else:
                yield "0:{text}\n".format(text=json.dumps(choice.delta.content))

        if chunk.choices == []:
            usage = chunk.usage
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

            yield 'e:{{"finishReason":"{reason}","usage":{{"promptTokens":{prompt},"completionTokens":{completion}}},"isContinued":false}}\n'.format(
                reason="tool-calls" if len(draft_tool_calls) > 0 else "stop",
                prompt=prompt_tokens,
                completion=completion_tokens,
            )


def test_do_stream():
    messages = [{"role": "user", "content": "hello there"}]
    stream = do_stream(messages)
    print("\rRESPONSE TEXT:\n")

    for chunk in stream:
        text = chunk.choices[0].delta.content
        if text is not None:
            print(text, end="", flush=True)
    print("\n\nDONE")
    assert stream.response.status_code == 200


@app.post("/api/chat")
async def handle_chat_data(request: ChatRequest, protocol: str = Query("data")):
    messages = request.messages
    openai_messages = convert_to_openai_messages(messages)

    response = StreamingResponse(stream_text(openai_messages, protocol))
    response.headers["x-vercel-ai-data-stream"] = "v1"
    return response


@app.get("/api/hello")
def hello_fast_api():
    return {"message": "Hello from FastAPI"}


@app.get("/api/cron_job_example")
async def cron_job_example():

    return {"message": "Cron job executed", "timestamp": datetime.now().isoformat()}


@app.get("/api/cron_job_example_private")
async def cron_job_example_private(request: Request):
    auth_header = request.headers.get("authorization")
    if not auth_header or auth_header != f"Bearer {os.environ.get('CRON_SECRET')}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    return {"message": "Private Cron job executed", "timestamp": datetime.now().isoformat()}

