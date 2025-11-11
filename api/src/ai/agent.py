"""
Portfolio Chatbot Agent using PydanticAI

This agent can answer questions about the developer's portfolio, skills, and projects.
"""
import logging
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel

logger = logging.getLogger(__name__)


@dataclass
class PortfolioContext:
    """Context for the portfolio chatbot agent"""
    user_name: str = "visitor"


# Define the portfolio information
PORTFOLIO_INFO = """
# Emilio Esposito - Portfolio

## About Me
I'm a full-stack developer with expertise in building scalable web applications. I've been doing full-stack 
development for about 2 years, with a strong background in Python and growing expertise in TypeScript.

## Tech Stack

### Frontend
- **Framework**: Next.js with App Router
- **UI Libraries**: Shadcn UI, Radix UI, Tailwind CSS
- **State Management**: React Hooks
- **Languages**: TypeScript, React

### Backend
- **Framework**: FastAPI (Python)
- **Database**: Neon Postgres with SQLAlchemy
- **Authentication**: Clerk
- **API Design**: RESTful APIs, GraphQL with Strawberry
- **Real-time**: WebSockets, Server-Sent Events

### DevOps & Tools
- **Deployment**: Railway
- **Package Managers**: pnpm (frontend), uv (Python)
- **Version Control**: Git
- **Testing**: Playwright (E2E), pytest (backend)

## Current Projects

### Portfolio Website (this project)
A monorepo containing various projects and tools:
- Next.js frontend for public-facing website
- FastAPI backend with multiple microservices
- Features include: Google OAuth integration, email management, scheduling, contact management
- Demonstrates modern full-stack architecture with clean separation of concerns

### Key Features Implemented
- **Google Integration**: Gmail API integration, Google Calendar, Google Sheets
- **Communication**: OpenPhone integration for business communications
- **Real-time Features**: Push notifications, webhook handling
- **Task Automation**: APScheduler for cron jobs, automated email responses
- **GraphQL API**: Strawberry GraphQL for flexible data querying

## Skills
- **Languages**: Python (advanced), TypeScript (intermediate), JavaScript, SQL
- **Frameworks**: FastAPI, Next.js, React
- **Databases**: PostgreSQL, working with ORMs (SQLAlchemy)
- **Cloud Services**: Railway deployment, working with various APIs
- **AI/ML**: OpenAI API integration, building AI-powered features, PydanticAI
- **Tools**: Git, Docker, pnpm, uv package managers

## Professional Background
I run Sernia Capital, a rental business, and have built custom tools to manage operations efficiently. 
I'm experienced in taking projects from concept to production, with a focus on:
- Clean, maintainable code
- Practical solutions over over-engineered abstractions
- Iterative development and continuous improvement
- User-focused design

## Philosophy
I prefer to start simple, understand the fundamentals, then iterate. I value clean code over 
clever abstractions, and I'm always learning new technologies to solve real problems.
"""


# Create the agent with the OpenAI model
model = OpenAIChatModel("gpt-4o-mini")

agent = Agent(
    model=model,
    system_prompt=(
        "You are a helpful portfolio assistant for Emilio Esposito, a full-stack developer. "
        "Your role is to answer questions about his skills, projects, and experience in a friendly, "
        "professional manner. Be concise but informative. If you don't know something specific, "
        "be honest about it but highlight related expertise.\n\n"
        f"{PORTFOLIO_INFO}"
    ),
    retries=2,
)


@agent.tool
async def get_portfolio_section(ctx: RunContext[PortfolioContext], section: str) -> str:
    """
    Get specific information about a portfolio section.
    
    Args:
        ctx: The run context
        section: The section to retrieve (e.g., 'skills', 'projects', 'tech-stack', 'background')
    
    Returns:
        Relevant portfolio information
    """
    logger.info(f"Getting portfolio section: {section}")
    
    section_lower = section.lower()
    
    if "skill" in section_lower:
        return """
        Skills:
        - Python (Advanced): FastAPI, SQLAlchemy, async programming, pytest
        - TypeScript (Intermediate): Next.js, React, modern JavaScript
        - Databases: PostgreSQL, SQL, database design
        - APIs: RESTful design, GraphQL, OpenAI API
        - Cloud: Railway deployment, serverless concepts
        - Tools: Git, Docker, modern package managers (pnpm, uv)
        """
    elif "project" in section_lower:
        return """
        Current Projects:
        - Full-stack portfolio website with Next.js and FastAPI
        - Google Workspace integration (Gmail, Calendar, Sheets)
        - Business communication system with OpenPhone integration
        - Automated email response system
        - Task scheduling and cron job management
        - Contact and user management systems
        """
    elif "tech" in section_lower or "stack" in section_lower:
        return """
        Tech Stack:
        Frontend: Next.js, React, TypeScript, Shadcn UI, Tailwind CSS
        Backend: FastAPI, Python, SQLAlchemy, Strawberry GraphQL
        Database: Neon Postgres
        Deployment: Railway
        Auth: Clerk
        Package Managers: pnpm (frontend), uv (Python)
        """
    elif "background" in section_lower or "about" in section_lower:
        return """
        Background:
        - 2 years of full-stack development experience
        - Runs Sernia Capital (rental business)
        - Builds custom tools for business operations
        - Strong Python background, growing TypeScript expertise
        - Focus on practical, maintainable solutions
        - Experience taking projects from concept to production
        """
    else:
        return "I have information about skills, projects, tech stack, and background. What would you like to know?"


def test_agent():
    """Test the agent locally"""
    import asyncio
    
    async def run_test():
        result = await agent.run("What technologies does Emilio work with?")
        print(f"\n\nAgent Response:\n{result.data}")
    
    asyncio.run(run_test())


if __name__ == "__main__":
    test_agent()
