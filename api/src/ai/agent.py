"""
Portfolio Chatbot Agent using PydanticAI

This agent can answer questions about the developer's portfolio, skills, and projects.
"""
import logging
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pathlib import Path
from pydantic_ai import BinaryContent
from dotenv import load_dotenv
import pytest
load_dotenv('.env.development.local')


logger = logging.getLogger(__name__)





@dataclass
class PortfolioContext:
    """Context for the portfolio chatbot agent"""
    user_name: str = "visitor"


# Define the portfolio information
SYSTEM_INSTRUCTIONS = """You are a helpful assistant that can answer questions about Emilio Esposito.

You have access to some tools to give your information about him.

ALWAYS read his LinkedIn Profile at the beginning of the conversation. Use other tools as needed. 

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


@agent.tool_plain
async def read_linkedin_profile() -> BinaryContent:
    """
    Get Emilio's LinkedIn profile.
    Link: https://www.linkedin.com/in/emilioespositousa/
    """
    pdf_path = Path('api/src/ai/pdfs/LinkedinProfile.pdf')
    return BinaryContent(data=pdf_path.read_bytes(), media_type='application/pdf')


@agent.tool_plain
async def read_portfolio_profile() -> BinaryContent:
    """
    Get Emilio's portfolio website homepage.
    Link: https://eesposito.com
    """
    pdf_path = Path('apps/web/app/page.tsx')
    return BinaryContent(data=pdf_path.read_bytes(), media_type='text/plain')

@agent.tool_plain
async def read_linkedin_skills() -> BinaryContent:
    """
    Get Emilio's LinkedIn skills.
    """
    pdf_path = Path('api/src/ai/pdfs/LinkedinSkills.pdf')
    return BinaryContent(data=pdf_path.read_bytes(), media_type='application/pdf')


@agent.tool_plain
async def read_linkedin_article_ai_launch() -> BinaryContent:
    """
    Get LinkedIn article where Emilio is mentioned as the Engineering lead for the internal AI call simulator.
    Link: https://www.linkedin.com/pulse/powering-next-generation-legal-services-inside-legalzooms-ai-ymlic/?trackingId=cEzrxUhWX4Pc218FiNBjEw%3D%3D
    """
    pdf_path = Path('api/src/ai/pdfs/LinkedInArticle-LegalZoom-AI-Launch.pdf')
    return BinaryContent(data=pdf_path.read_bytes(), media_type='application/pdf')


@agent.tool_plain
async def read_linkedin_interview_ai_launch() -> BinaryContent:
    """
    Get interview transcript where Emilio talks about an AI project launched at LegalZoom.
    Link: https://www.techtarget.com/searchcio/feature/Building-an-internal-AI-call-simulator-Lessons-for-CIOs
    """
    pdf_path = Path('api/src/ai/pdfs/Search-CIO-Interview-AI.pdf')
    return BinaryContent(data=pdf_path.read_bytes(), media_type='application/pdf')


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
    logger.info(f"Getting Emilio's links")
    
    return EMILIO_LINKS
   
    

@pytest.mark.asyncio
async def test_agent():
    """Test the agent locally"""
    
    result = await agent.run("Summarize Emilio's LinkedIn profile")
    print(f"\n\nAgent Response:\n{result}")
    


if __name__ == "__main__":
    test_agent()
