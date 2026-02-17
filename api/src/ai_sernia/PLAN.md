# Sernia Capital LLC AI Planning

ULTIMATE GOAL: Make an all-encompassing AI agent for Sernia Capital LLC that can handle all of our needs. 

## Technical Structure

Put most of the new code in the `api/src/ai_sernia` folder. There might be some imports from other folders as well to resuse logic from other existing services. Note that some of the pre-existing services might not be as high quality as we'd like (many were build in a rush). We might need to either do some cleanup/refactoring as we go too. 


## Capabilties

### Core Tools
* Quo (fka as OpenPhone). For sending SMS, reading SMS and voice transcriptions. Note: I'd like to avoide using the backend table where we store openphone_messages. I think it will be cleaner to retrieve the messages from the Quo API directly. Actually, Quo even released an MCP, which might be even better than our hand-rolled wrapper functions around their APIs. See here: https://support.quo.com/core-concepts/integrations/mcp . It's possible their MCP is trash though, but we should check it out first. 
* Google - Search drive, email, calendar, docs, etc. Send emails, create docs, etc. Google has no MCP, so we need to build out some functionality. We already have a service account in this repo we can use. 
* Clickup - For managing our tasks and projects. 

### Other capabilities we need
* Memory - We should give the agent a workspace sandbox where it can store long term memories in markdown files (use https://github.com/zby/pydantic-ai-filesystem-sandbox to safely give the AI a sandbox to work in). On localhost, the sandbox should be in a gitignored `.workspace` folder. The agent should have tools to store and retrieve memories. The memory structure should be 3 tiered in this structure:
    * MEMORY.md - Tacit memory of general patterns, rules, and principles. This should be used judiciously since it will injected into every conversation as context.
    * daily_notes/<YYYY-MM-DD>.md - A folder with markdown files for daily notes of the agent's activities, business events, etc.
    * areas/<area_name_ai_decided>/<file_name_ai_decided>.md - The `areas` folder is where the AI can organize its own memory into any category it wants, in any folder structure it wants.
    * skills/<skill_name_ai_decided>/SKILL.md - This should be used for SOP-like documents where the AI learns business processes and procedures. Use https://github.com/DougTrajano/pydantic-ai-skills to implement this.
* Automatic conversation compaction using history processors. See https://ai.pydantic.dev/message-history/#summarize-old-messages

### Sub agents

We should have a folder specifically for sub agents in `api/src/ai_sernia/sub_agents`. Some subagents that we know we'll need:
* history_compactor - This agent should be responsible for summarizing old messages in a conversation.
* summarization agent - This agent will be used to make sure data fetching tool calls don't blow up the main agent's context window. They might return data to the main agent verbatim, or they might return a summary or some truncated data. The main agent should always be aware if the subagent is returning a summary or not. If the summarization agent does not return raw data, it should have a semi-permanent structure to specificy if it's returning verbatim snippets, pararphased summaries, etc. Underneath its parent structure it could retain the structure of the original data so that raw results and summaries are comparable.



## Triggering the agent
* The agent should be triggered by every incoming SMS. It doesn't necessarily need to do anything each time. 
* The agent should read email on a scheduled basis (use APScheduler for this). Running on every new email event from pubsub is probably too noisy, so think APScheduler pattern is better.


## Self improving PLAN.md document

As we plan, we should update this document to reflect the updated plan.

