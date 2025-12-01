# API vs API_SRC Directory

The `/api` directory hosts the FastAPI server.

See [.vscode/launch.json](../.vscode/launch.json) for how to run the FastAPI backend in debug mode on Emilio's local laptop.
See [Dockerfile](./Dockerfile) for how it is ran in prod on Railway. 
See [railway_fastapi.json](./railway_fastapi.json) for how Railway deployment configuration.
Also see project root [README.md](../README.md) for more context.

Direct run command (I think):

```bash
# Activate the virtual environment from project root "portfolio" directory
source .venv/bin/activate
python -m hypercorn api.index:app --bind 0.0.0.0:8000 --reload
```

