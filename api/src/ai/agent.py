"""
Portfolio Chatbot Agent using PydanticAI

This agent can answer questions about the developer's portfolio, skills, and projects.
"""
import logfire
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pathlib import Path
from pydantic_ai import BinaryContent
from dotenv import load_dotenv
import pytest
import httpx
load_dotenv('.env.development.local')





@dataclass
class PortfolioContext:
    """Context for the portfolio chatbot agent"""
    user_name: str = "visitor"


# Define the portfolio information
SYSTEM_INSTRUCTIONS = """You are a helpful assistant that can answer questions about Emilio Esposito.

You have access to some tools to give your information about him.

ALWAYS use the read_linkedin_profile tool if you haven't used it already. Use other tools as needed. 

There is no need to call tools more than once per conversation since the tools return static content.

"""


# Create the agent with the OpenAI model
model = OpenAIChatModel("gpt-4o-mini")

agent = Agent(
    model=model,
    system_prompt=SYSTEM_INSTRUCTIONS,
    retries=3,
    instrument=True,
)


folder_url = "https://filebrowser-development-c065.up.railway.app/share/kU8NArvC"
base_download_url = "https://filebrowser-development-c065.up.railway.app/api/public/dl/kU8NArvC"

def _ensure_pdf_exists(pdf_path: Path) -> None:
    """Ensure PDF file exists, logging error and raising FileNotFoundError if not."""
    if not pdf_path.exists():
        logfire.error(
            'PDF not found',
            pdf_path=str(pdf_path.absolute()),
            current_working_directory=str(Path.cwd()),
            file_location=__file__
        )
        raise FileNotFoundError(f"PDF not found at {pdf_path.absolute()}")


@agent.tool_plain
async def read_emilio_linkedin_profile() -> BinaryContent:
    """
    Get Emilio's LinkedIn profile.
    Link: https://www.linkedin.com/in/emilioespositousa/
    """
    pdf_url = f"{base_download_url}/LinkedinProfile.pdf"
    async with httpx.AsyncClient() as client:
        response = await client.get(pdf_url)
        response.raise_for_status()
        return BinaryContent(data=response.content, media_type='application/pdf')


@agent.tool_plain
async def read_emilio_portfolio_website() -> BinaryContent:
    """
    Get Emilio's portfolio website homepage.
    Link: https://eesposito.com
    """
    url = 'https://eesposito.com'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        return BinaryContent(data=response.content, media_type='text/html')

@agent.tool_plain
async def read_emilio_linkedin_skills() -> BinaryContent:
    """
    Get Emilio's LinkedIn skills.
    """
    pdf_url = f"{base_download_url}/LinkedinSkills.pdf"
    async with httpx.AsyncClient() as client:
        response = await client.get(pdf_url)
        response.raise_for_status()
        return BinaryContent(data=response.content, media_type='application/pdf')


@agent.tool_plain
async def read_linkedin_article_ai_launch() -> BinaryContent:
    """
    Get LinkedIn article where Emilio is mentioned as the Engineering lead for the internal AI call simulator.
    Link: https://www.linkedin.com/pulse/powering-next-generation-legal-services-inside-legalzooms-ai-ymlic/?trackingId=cEzrxUhWX4Pc218FiNBjEw%3D%3D
    """
    pdf_url = f"{base_download_url}/LinkedInArticle-LegalZoom-AI-Launch.pdf"
    async with httpx.AsyncClient() as client:
        response = await client.get(pdf_url)
        response.raise_for_status()
        return BinaryContent(data=response.content, media_type='application/pdf')


@agent.tool_plain
async def read_linkedin_interview_ai_launch() -> BinaryContent:
    """
    Get interview transcript where Emilio talks about an AI project launched at LegalZoom.
    Link: https://www.techtarget.com/searchcio/feature/Building-an-internal-AI-call-simulator-Lessons-for-CIOs
    """
    pdf_url = f"{base_download_url}/Search-CIO-Interview-AI.pdf"
    async with httpx.AsyncClient() as client:
        response = await client.get(pdf_url)
        response.raise_for_status()
        return BinaryContent(data=response.content, media_type='application/pdf')


EMILIO_LINKS = {
    "article-about-internal-ai-call-simulator-launched-by-emilio": "https://www.linkedin.com/pulse/powering-next-generation-legal-services-inside-legalzooms-ai-ymlic/?trackingId=cEzrxUhWX4Pc218FiNBjEw%3D%3D",
    "emilio-giving-interview-about-ai": "https://www.techtarget.com/searchcio/feature/Building-an-internal-AI-call-simulator-Lessons-for-CIOs",
    "sernia-capital-website": "https://serniacapital.com",
    "linkedin-profile": "https://www.linkedin.com/in/emilioespositousa",
    "github-profile": "https://github.com/emilioesposito",
    "portfolio-website": "https://eesposito.com",
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
