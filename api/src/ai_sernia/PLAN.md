# Sernia Capital LLC AI Planning

ULTIMATE GOAL: Make an all-encompassing AI agent for Sernia Capital LLC that can handle all of our needs. 

## Technical Structure

Put most of the new code in the `api/src/ai_sernia` folder. There might be some imports from other folders as well to resuse logic from other existing services. Note that some of the pre-existing services might not be as high quality as we'd like (many were build in a rush). We might need to either do some cleanup/refactoring as we go too. 

Use PydanticAI for the agent structure: https://ai.pydantic.dev/

We already have a agent_conversations table that I think we should use to store conversation history. We might need to add some columns to it. 


## Capabilties

### Core Tools
* Quo (aka OpenPhone). For sending SMS, reading SMS and voice transcriptions. Note: I'd like to avoide using the backend table where we store openphone_messages. I think it will be cleaner to retrieve the messages from the Quo API directly. Actually, Quo even released an MCP, which might be even better than our hand-rolled wrapper functions around their APIs. See here: https://support.quo.com/core-concepts/integrations/mcp . It's possible their MCP is trash though, but we should check it out first. OpenPhone was renamed to Quo recenntly, but this repo still refers to it as OpenPhone; that's ok (don't go renaming stuff).
* Google - Search drive, email, calendar, docs, etc. Send emails, create docs, etc. Google has no MCP, so we need to build out some functionality. We already have a service account in this repo we can use. 
* Clickup - For managing our tasks and projects. 
* Search agent_conversations table so it can lookup past conversations of itself across time/users/modalities/conversations. This allows it to pickup context across conversations (which can span across time and modalities).
* Search open_phone_messages table to find relevant information. I don't believe the OpenPhone/Quo MCP has a search capability, but we already store all messages in the database.

### Other capabilities we need
* Memory - We should give the agent a workspace sandbox where it can store long term memories in markdown files (use https://github.com/zby/pydantic-ai-filesystem-sandbox to safely give the AI a sandbox to work in). On localhost, the sandbox should be in a gitignored `.workspace` folder. On Railway, we'll use a volume: https://docs.railway.com/volumes/reference. The agent should have tools to store and retrieve memories. The memory structure should be 3 tiered in this structure:
    * MEMORY.md - Tacit memory of general patterns, rules, and principles. This should be used judiciously since it will injected into every conversation as context.
    * daily_notes/<YYYY-MM-DD>.md - A folder with markdown files for daily notes of the agent's activities, business events, etc.
    * areas/<area_name_ai_decided>/<file_name_ai_decided>.md - The `areas` folder is where the AI can organize its own memory into any category it wants, in any folder structure it wants.
    * skills/<skill_name_ai_decided>/SKILL.md - This should be used for SOP-like documents where the AI learns business processes and procedures. Use https://github.com/DougTrajano/pydantic-ai-skills to implement this.
* Automatic conversation compaction using history processors. See https://ai.pydantic.dev/message-history/#summarize-old-messages

### Sub agents

We should have a folder specifically for sub agents in `api/src/ai_sernia/sub_agents`. Some subagents that we know we'll need:
* history_compactor - This agent should be responsible for summarizing old messages in a conversation.
* summarization agent - This agent will be used to make sure data fetching tool calls don't blow up the main agent's context window. They might return data to the main agent verbatim, or they might return a summary or some truncated data. The main agent should always be aware if the subagent is returning a summary or not. If the summarization agent does not return raw data, it should have a semi-permanent structure to specificy if it's returning verbatim snippets, pararphased summaries, etc. Underneath its parent structure it could retain the structure of the original data so that raw results and summaries are comparable.



## AI Agent Triggers
* Human initiated conversation - This is the primary trigger. Seee Human Interaction Modalities section below.
* The agent should also be triggered by every incoming SMS. It doesn't necessarily need to do anything each time. Maybe just update memory. Don't replace the escalate.py agent for now. 
* The agent should read email on a scheduled basis (use APScheduler for this). Running on every new email event from pubsub is probably too noisy, so think APScheduler pattern is better.

## Human interaction modalities

This AI agent should be able to interact with our employees in multiple different modalities. 
* Email - This is the secondary interaction modality. Each email thread = 1 conversation.
* Web Chat - We will have a frontend in our React Router app that will be used to interact with the agent. Conversation threads here are straightforward. 
* SMS - This is the primary interaction modality. This will need to be compacted often


## Self improving PLAN.md document

As we plan, we should update this document to reflect the updated plan. Your first order of business should be to improve the structure/formatting of this document, and interview me on clarifications/questions.

