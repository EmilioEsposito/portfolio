from dotenv import load_dotenv
# Load local development variables (does not impact preview/production)
load_dotenv(".env.development.local", override=True)
import os
import json
from typing import List
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam
from pydantic import BaseModel
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from api_src.utils.prompt import ClientMessage, convert_to_openai_messages
from api_src.utils.tools import get_current_weather
from datetime import datetime
import logging
# Import the routers after environment variables are loaded
from api_src.open_phone import router as open_phone_router
from api_src.cron import router as cron_router

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")
# Include namespaces routers
app.include_router(open_phone_router, prefix="/api")
app.include_router(cron_router, prefix="/api")

# Add error handling
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error processing request: {str(exc)}", exc_info=True)
    
    # Handle HTTPException specially
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": str(exc.detail),
                "detail": str(exc.detail),
                "status_code": exc.status_code
            }
        )
    
    # Handle other exceptions
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "detail": "Internal Server Error",
            "status_code": 500
        }
    )

# Initialize OpenAI client after environment variables are loaded
open_ai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
)


class ChatRequest(BaseModel):
    messages: List[ClientMessage]


available_tools = {
    "get_current_weather": get_current_weather,
}


def do_stream(messages: List[ChatCompletionMessageParam]):
    stream = open_ai_client.chat.completions.create(
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

    stream = open_ai_client.chat.completions.create(
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
async def hello_fast_api():
    return {"message": "Hello from FastAPI"}



