# Cleanup after code changes

When making code changes, make sure non-code path things are also kept up to date:

1. Natural language changes. These are very important, since they impact the AI Agent's behavior at runtime.
    * @instructions.py text 
    * Tool descriptions docstrings, pydantic field descriptions, etc
2. Documentation updates. These are important so that humans and AI can understand the codebase and its intent during development and maintenance.
    * @README.md text 
    * @triggers/README.md text 
    * @tools/README.md text 
