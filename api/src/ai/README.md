# Agentic AI Assistant Service

This service is responsible for handling all AI related tasks.

## Goals

There should be some backend-only self-scheduling logic, but maybe the same agent is also used for interactive chat in the app? For interactive chat solution this potential solution for integrating PydanticAI with Vercel's AI SDK: https://pastebin.com/zGXT2Wp2 (also saved in scratch/aisdk_and_pydanticai.md)

## Known Requirements

* The agent should be able to call these functions:
* api.src.open_phone.service.send_message
* api.src.google.gmail.service.send_email
* api.src.push.service.send_push_to_user
* api.src.scheduler.service.schedule_sms
* api.src.scheduler.service.schedule_email
* api.src.scheduler.service.schedule_push

To be created:
* api.src.google.calendar.service.create_event




## Framework Options (not necessarily mutually exclusive)

* PydanticAI
* Vercel AI SDK
    * has good frontend support for chat
* OpenAI Native tooling
  * Responses API
  * Completions API
  * Agent SDK
* Langchain
    * Not preferred for anything. Has a bad reputation for confusing abstractions, poor documentation, breaking changes, etc.





## Questions

Could this agent also handle interactive chat in the app? Or should we have a separate agent (maybe on a different framework) for that? Would a separate agent be able to either call this agent or use same tools though (does this depend on the framework)?