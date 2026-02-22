#!/usr/bin/env python3
"""Approval server for Claude Code Slack-based permission approvals.

Deployed on Railway at:
    https://claude-approval-production.up.railway.app

To redeploy after changes:
    railway up --service claude-approval -d .claude/hooks
"""

import json
import os

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="Claude Code Approval Server")

# In-memory stores
pending: dict[str, dict] = {}
decisions: dict[str, str] = {}


@app.post("/register")
async def register_request(request: Request):
    """Register a pending permission request (called by the hook script)."""
    data = await request.json()
    rid = data["request_id"]
    pending[rid] = data
    return {"status": "registered", "request_id": rid}


@app.post("/slack/action")
async def slack_action(request: Request):
    """Receive Slack interactive component callback when a button is tapped."""
    form = await request.form()
    payload = json.loads(form["payload"])

    action = payload["actions"][0]
    rid = action["value"]
    action_id = action["action_id"]  # "approve" or "deny"
    user = payload.get("user", {}).get("name", "unknown")

    decision = "allow" if action_id == "approve" else "deny"
    decisions[rid] = decision
    pending.pop(rid, None)

    emoji = "Approved" if decision == "allow" else "Denied"
    return JSONResponse({
        "replace_original": True,
        "text": f"{emoji} by {user}  (request `{rid}`)",
    })


@app.get("/decision/{request_id}")
async def get_decision(request_id: str):
    """Polled by the hook script to check for a decision."""
    if request_id in decisions:
        return {"decision": decisions[request_id]}
    return Response(status_code=404)


@app.get("/health")
async def health():
    return {"status": "ok", "pending": len(pending), "decided": len(decisions)}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", os.environ.get("APPROVAL_SERVER_PORT", "9876")))
    print(f"Starting approval server on :{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
