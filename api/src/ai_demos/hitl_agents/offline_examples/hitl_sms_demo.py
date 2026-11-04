"""Manual persisted SMS-agent demo; can perform real inference and approved sends.

Run only with intentional development credentials and recipients:
python -m api.src.ai_demos.hitl_agents.offline_examples.hitl_sms_demo
"""

import asyncio
import uuid

from api.src.ai_demos.hitl_agents.hitl_sms_agent import (
    ApprovalDecision,
    HITLAgentContext,
    extract_pending_approvals,
    hitl_sms_agent,
    resume_with_approvals,
)


async def demo():
    print("=== HITL SMS Agent Demo ===\n")

    # Step 1: First run - agent proposes an action
    print("Step 1: Running agent with SMS request...")
    conversation_id = str(uuid.uuid4())

    # Use agent.run directly - persistence patch handles saving to DB
    result = await hitl_sms_agent.run(
        user_prompt="Send a friendly hello to Emilio",
        deps=HITLAgentContext(clerk_user_id="demo_user", conversation_id=conversation_id),
    )

    pending_list = extract_pending_approvals(result)
    if pending_list:
        print(f"✓ Agent returned DeferredToolRequests with {len(pending_list)} pending approval(s)")
        for p in pending_list:
            print(f"  - Tool: {p['tool_name']}, Args: {p['args']}")

        # Step 2: Simulate user approval with modified message
        print("Step 2: Approving all pending tool calls...")
        decisions = [
            ApprovalDecision(
                tool_call_id=p["tool_call_id"],
                approved=True,
                override_args={"body": f"[APPROVED] {p['args'].get('body', '')}"}
                if p["tool_name"] == "send_sms"
                else None,
            )
            for p in pending_list
        ]

        final = await resume_with_approvals(
            conversation_id=conversation_id,
            decisions=decisions,
            clerk_user_id="demo_user",
        )

        print("✓ Agent resumed and completed")
        print(f"  Final output: {final.output}\n")
    else:
        print(f"Result (no approval needed): {result.output}")


if __name__ == "__main__":
    asyncio.run(demo())
