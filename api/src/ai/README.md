# Portfolio AI Chatbot

An intelligent portfolio assistant built with PydanticAI and integrated with Vercel AI SDK.

## Quick Start

### Test the Agent Directly
```bash
cd /workspace
source .venv/bin/activate
python -c "from api.src.ai.agent import test_agent; test_agent()"
```

### Run the Frontend
```bash
cd /workspace/apps/web
pnpm dev
```

Visit: http://localhost:3000/portfolio-chat

## Files

- `agent.py` - PydanticAI agent with portfolio information and tools
- `routes.py` - FastAPI endpoints for streaming chat
- `README.md` - This file

## API Endpoints

### POST /api/ai/chat
Main chat endpoint that accepts messages and streams responses.

**Request Body:**
```json
{
  "messages": [
    {"role": "user", "content": "What technologies does Emilio work with?"}
  ]
}
```

**Response:** Server-Sent Events stream using Vercel AI SDK Data Stream Protocol

### GET /api/ai/health
Health check endpoint.

## Architecture

1. Frontend sends messages via `useChat` hook
2. Backend receives request at `/api/ai/chat`
3. Messages converted to PydanticAI format
4. Agent generates streaming response with OpenAI
5. Response streams back to frontend in real-time

## Extending the Agent

Add new tools to the agent:

```python
@agent.tool
async def your_tool_name(ctx: RunContext[PortfolioContext], param: str) -> str:
    """Tool description"""
    # Your logic here
    return "result"
```

The tool will automatically be available to the agent during conversations.

## See Also

- [Full Documentation](/workspace/PYDANTIC_AI_CHATBOT.md)
- [Implementation Summary](/workspace/PORTFOLIO_CHATBOT_SUMMARY.md)
