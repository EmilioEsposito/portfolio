"""
Portfolio Chatbot Agent using PydanticAI

This agent can answer questions about the developer's portfolio, skills, and projects.
Each information source has its own tool that fetches from a specific URL.
"""
import logfire
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext
from dotenv import load_dotenv
import httpx
import pytest
load_dotenv('.env')



@dataclass
class PortfolioContext:
    """Context for the portfolio chatbot agent"""
    user_name: str = "visitor"


SYSTEM_INSTRUCTIONS = """You are a helpful assistant that can answer questions about Emilio Esposito.

You have tools that fetch information about Emilio from specific sources. Use them to answer questions.

ALWAYS use the fetch_resume tool first if you haven't already — it has the most comprehensive info.
Use other fetch tools as needed for additional context.

There is no need to call the same tool more than once per conversation since the content is static.
"""

agent = Agent(
    "anthropic:claude-haiku-4-5-20251001",
    system_prompt=SYSTEM_INSTRUCTIONS,
    retries=3,
    instrument=True,
    name="chat_emilio",
)


async def _fetch_url(url: str) -> str:
    """Fetch a URL and return its text content."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


@agent.tool_plain
async def fetch_resume() -> str:
    """Fetch Emilio's resume. Contains his full work experience, education, skills, and career summary.
    This is the best starting point for answering questions about Emilio.
    """
    return await _fetch_url("https://resume.eesposito.com/")


@agent.tool_plain
async def fetch_portfolio_website() -> str:
    """Fetch Emilio's portfolio website homepage. Contains an overview of his projects and work."""
    return await _fetch_url("https://eesposito.com")


@agent.tool_plain
async def fetch_interview_ai_launch() -> str:
    """Fetch the TechTarget/SearchCIO interview where Emilio talks about an AI project launched at LegalZoom."""
    return await _fetch_url("https://www.techtarget.com/searchcio/feature/Building-an-internal-AI-call-simulator-Lessons-for-CIOs")


EMILIO_LINKS = {
    "article-about-internal-ai-call-simulator-launched-by-emilio": "https://www.linkedin.com/pulse/powering-next-generation-legal-services-inside-legalzooms-ai-ymlic/",
    "emilio-giving-interview-about-ai": "https://www.techtarget.com/searchcio/feature/Building-an-internal-AI-call-simulator-Lessons-for-CIOs",
    "sernia-capital-website": "https://serniacapital.com",
    "linkedin-profile": "https://www.linkedin.com/in/emilioespositousa",
    "github-profile": "https://github.com/emilioesposito",
    "portfolio-website": "https://eesposito.com",
    "resume-website": "https://resume.eesposito.com",
}

@agent.tool
async def get_emilio_links(ctx: RunContext[PortfolioContext]) -> dict:
    """
    Get Emilio's links to his LinkedIn profile, Github, Sernia Capital LLC, public article/interview references, portfolio website, etc.
    """
    logfire.info("Getting Emilio's links")
    return EMILIO_LINKS


@pytest.mark.asyncio
async def test_agent():
    """Test the agent locally"""
    result = await agent.run("Summarize Emilio's LinkedIn profile")
    print(f"\n\nAgent Response:\n{result}")


if __name__ == "__main__":
    test_agent()
