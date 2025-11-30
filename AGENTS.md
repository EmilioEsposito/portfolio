You are Codex Cloud Agent. 

This repo has a NextJS frontend and a FastAPI backend. It also has an Expo app but not really using it much, it is not a priority right now.

See .codex/setup.sh for how your development environment was setup in case you suspect something is wrong with your environment.

Your dev environment setup has some differences from Emilio's local laptop development.

Differences from Emilio's local laptop development: 
* The .env file is not used by you (Codex Cloud Agent), it is only used for Emilio's local laptop development. Just check for the existence of some of the environment variables in env.example file. The variables should have just been injected into the environment variables when you deployed.
* Postgres should already be running. You can ignore the Docker instructions for Postgres in README.md. 

# Running services
See [.vscode/launch.json](.vscode/launch.json) for how to run the different services are ran on Emilio's local laptop.

# FastAPI Backend
Note you always need to source the .venv/bin/activate file to activate the virtual environment.
See [api/README.md](api/README.md) for more directions on FastAPI backend.

# More General
See [README.md](README.md) for more context (remember not all of this is relevant to you, Codex Cloud Agent).
