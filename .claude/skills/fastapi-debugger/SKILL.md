---
name: fastapi-debug-lifecycle
description: |
  Detect FastAPI readiness, identify debugger pauses, extract errors,
  and restart or replace a VS Code debug session using CLI-only signals.
---

# FastAPI Debug Lifecycle Skill

This Skill enables Claude Code to manage a FastAPI backend running under
VS Code + debugpy + Hypercorn with hot reload.

## Important Runtime Context

- **All Python file edits trigger a hot reload**
- **Typical cold boot / reload time is ~15 seconds**
- During reload:
  - the process may temporarily exit
  - the port may flap
  - the heartbeat file may disappear or reset
- Claude should wait for readiness before assuming failure

The application already includes a `_local_heartbeat()` helper wired into
the FastAPI lifespan. This heartbeat writes to disk and stops when the
Python interpreter is paused by a debugger.

---

## Capabilities

- Check application readiness (`/api/health`)
- Determine whether the process is alive vs paused
- Detect debugger pauses via the existing heartbeat
- Distinguish reload vs crash vs pause
- Extract recent tracebacks from logs
- Kill an existing VS Code debug session
- Start a fresh debug session under Claude control

---

## Quick Status Commands

### Is the FastAPI app up?

```bash
curl -sf --max-time 1 http://localhost:8000/api/health >/dev/null \
  && echo READY || echo NOT_READY
```

Retry loop (reload-aware, ~15s budget):

```bash
for i in {1..30}; do
  if curl -sf --max-time 1 http://localhost:8000/api/health >/dev/null; then
    echo READY
    exit 0
  fi
  sleep 0.5
done
echo NOT_READY
```

Claude should not assume failure until this loop completes.

---

## Is the server process alive?

```bash
lsof -iTCP:8000 -sTCP:LISTEN -n -P
```

```bash
pgrep -af hypercorn
```

Interpretation:
- No listener → crashed or currently reloading
- Listener present + health failing → paused, wedged, or still booting

---

## Detect a Debugger Pause (Heartbeat)

The application already runs `_local_heartbeat()` automatically
in all non-Railway environments.

Heartbeat file location:

```
/tmp/fastapi_heartbeat
```

Check heartbeat freshness via CLI:

```bash
python - <<'PY'
import os, time
path = "/tmp/fastapi_heartbeat"
try:
    age = time.time() - os.path.getmtime(path)
    print("HEARTBEAT_AGE_SECONDS", round(age, 2))
    print("PAUSED_OR_STUCK" if age > 2.0 else "RUNNING")
except FileNotFoundError:
    print("NO_HEARTBEAT_FILE")
PY
```

Interpretation:
- `RUNNING` → interpreter scheduling normally
- `PAUSED_OR_STUCK` → very likely VS Code debugger pause
- `NO_HEARTBEAT_FILE` → app not started, reloading, or heartbeat disabled

During hot reload, `NO_HEARTBEAT_FILE` is expected temporarily.

---

## Extract Recent Errors

Assuming logs are written to `/path/to/backend.log`.

```bash
tail -n 200 /path/to/backend.log
```

Extract the most recent traceback:

```bash
python - <<'PY'
import re, pathlib
p = pathlib.Path("/path/to/backend.log")
text = p.read_text(errors="ignore")
matches = list(re.finditer(r"Traceback \\(most recent call last\\):", text))
if not matches:
    print("NO_TRACEBACK_FOUND")
else:
    start = matches[-1].start()
    print(text[start:][-4000:])
PY
```

---

## Kill the Existing VS Code Debug Session

Kill the Hypercorn process launched by VS Code:

```bash
pkill -f "hypercorn api.index:app"
```

Or kill any debugpy-backed process:

```bash
pkill -f debugpy
```

Note: this terminates the VS Code debugger session.

---

## Start a Fresh Debug Session (Claude-Owned)

Run the backend directly under debugpy in a background process:

```bash
python -m debugpy \
  --listen 5678 \
  -m hypercorn api.index:app \
  --reload \
  -b 0.0.0.0:8000 \
  > backend.log 2>&1 &
```

This allows Claude Code to control lifecycle and restart cleanly.

---

## Recommended Claude Workflow

1. After any code change, expect a hot reload (~15s)
2. Run readiness check with retry loop
3. If NOT_READY after retries:
   - check listener
   - check heartbeat
4. If heartbeat is stale:
   - treat as debugger pause
   - extract traceback
5. Apply fix
6. Kill existing debug session if needed
7. Start fresh debug session
8. Re-check health

---

## Notes

- `_local_heartbeat()` is already implemented and wired via lifespan.
- Heartbeat is disabled automatically on Railway.
- Heartbeat staleness must be interpreted with reload timing in mind.
- Log file path must be stable.
- Ports and paths should match project config.