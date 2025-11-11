# PydanticAI Portfolio Chatbot

This project showcases a modern, production-ready chatbot built with PydanticAI on the backend and Vercel AI SDK on the frontend.

## Overview

A portfolio chatbot that can answer questions about the developer's skills, projects, and experience using:
- **Backend**: PydanticAI with FastAPI
- **Frontend**: Next.js with Vercel AI SDK
- **Model**: OpenAI GPT-4o-mini

## Features

- ✅ **Type-safe AI agents** with PydanticAI
- ✅ **Streaming responses** using Vercel AI SDK protocol
- ✅ **Tool/function calling** support
- ✅ **Beautiful UI** with Shadcn components
- ✅ **Real-time streaming** from Python to TypeScript
- ✅ **Production-ready** architecture

## Architecture

### Backend (`/api/src/ai/`)

#### `agent.py`
- Defines the PydanticAI agent with portfolio information
- Includes a custom tool (`get_portfolio_section`) for retrieving specific information
- Uses OpenAI GPT-4o-mini model
- Type-safe with Pydantic models

#### `routes.py`
- FastAPI endpoints for the chatbot
- Converts between frontend message format and PydanticAI format
- Streams responses using Vercel AI SDK Data Stream Protocol
- Endpoints:
  - `POST /api/ai/chat` - Main chat endpoint
  - `GET /api/ai/health` - Health check

### Frontend (`/apps/web/`)

#### `/app/portfolio-chat/page.tsx`
- Next.js page for the portfolio chatbot

#### `/components/portfolio-chat.tsx`
- Main chat component using `useChat` hook from `@ai-sdk/react`
- Handles message state and streaming
- Connects to `/api/ai/chat` endpoint

#### `/components/portfolio-overview.tsx`
- Welcome screen with suggestions and tech stack info

## Installation

### Backend Dependencies
```bash
cd /workspace
uv add pydantic-ai uvicorn
```

### Frontend Dependencies
```bash
cd /workspace/apps/web
pnpm add ai@latest @ai-sdk/openai@latest zod@latest
```

## Testing

### 1. Direct Python Test
Test the PydanticAI agent directly:
```bash
cd /workspace
source .venv/bin/activate
python -c "from api.src.ai.agent import test_agent; test_agent()"
```

### 2. Endpoint Test
Test the streaming endpoint:
```bash
python test_ai_endpoint.py
```

### 3. HTTP Test
Test via HTTP (requires server running):
```bash
curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What technologies does Emilio work with?"}]}'
```

### 4. Full Stack Test

Start the test API server (without database dependencies):
```bash
cd /workspace
source .venv/bin/activate
python test_api_server.py
```

Start the Next.js frontend:
```bash
cd /workspace/apps/web
pnpm dev
```

Visit: http://localhost:3000/portfolio-chat

## Key Files Created/Modified

### New Files
- `/api/src/ai/agent.py` - PydanticAI agent definition
- `/api/src/ai/routes.py` - FastAPI routes for AI chatbot
- `/apps/web/app/portfolio-chat/page.tsx` - Portfolio chat page
- `/apps/web/components/portfolio-chat.tsx` - Chat component
- `/apps/web/components/portfolio-overview.tsx` - Welcome screen
- `/workspace/test_ai_endpoint.py` - Test script
- `/workspace/test_api_server.py` - Standalone test server

### Modified Files
- `/api/index.py` - Added AI router registration
- `/workspace/pyproject.toml` - Added PydanticAI dependencies
- `/workspace/apps/web/package.json` - Upgraded AI SDK packages

## How It Works

### Backend Flow
1. Client sends message to `POST /api/ai/chat`
2. `routes.py` converts message format and calls PydanticAI agent
3. Agent uses OpenAI model to generate response
4. Response streams back using Vercel AI SDK Data Stream Protocol
5. Format: `0:"text chunk"\n` for text, `e:{...}\n` for end

### Frontend Flow
1. User types message in chat interface
2. `useChat` hook sends POST request to `/api/ai/chat`
3. Hook automatically handles streaming response
4. UI updates in real-time as chunks arrive
5. Messages are stored in state for history

### PydanticAI Integration with Vercel AI SDK

The integration follows the pattern documented at:
https://ai.pydantic.dev/ui/vercel-ai/#usage

Key aspects:
- **Message format conversion**: Converting between Vercel AI SDK format and PydanticAI's `ModelMessage` format
- **Streaming protocol**: Using Vercel's Data Stream Protocol (`0:`, `e:` prefixes)
- **Tool calling**: PydanticAI tools automatically work with the stream

## Example Queries

Try asking:
- "What technologies does Emilio work with?"
- "Tell me about his Python experience"
- "What projects has he built?"
- "Does he have experience with React?"
- "What's his deployment setup?"

## Production Deployment

For production:
1. Set `OPENAI_API_KEY` environment variable
2. Configure database for the main FastAPI app
3. Deploy backend to Railway (or similar)
4. Deploy frontend to Vercel (or similar)
5. Update CORS origins in production

## Technical Highlights

1. **Type Safety**: Full type checking with Pydantic models
2. **Streaming**: Real-time response streaming for better UX
3. **Modularity**: Clean separation between agent logic and API routes
4. **Testability**: Easy to test components independently
5. **Scalability**: Can add more tools/capabilities to the agent
6. **Modern Stack**: Latest versions of PydanticAI and Vercel AI SDK

## Future Enhancements

- Add conversation history persistence
- Implement rate limiting
- Add more portfolio tools (GitHub API, etc.)
- Add authentication
- Implement conversation memory
- Add multi-modal support (images, files)

## Resources

- [PydanticAI Documentation](https://ai.pydantic.dev/)
- [Vercel AI SDK Documentation](https://sdk.vercel.ai/docs)
- [PydanticAI + Vercel AI SDK Integration](https://ai.pydantic.dev/ui/vercel-ai/)
- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [Next.js Documentation](https://nextjs.org/docs)
