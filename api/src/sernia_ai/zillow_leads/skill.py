"""The lead policy as a skill for the general agent.

``prompts/policy.md`` is the one place that says how Zillow leads are
answered. The lead agent gets it in its instructions (see ``agent.py``). The
general agent gets the same text here, so a person asking it about a lead hears the rules
the lead agent applies.

The skill is built in code, not kept in the workspace, for two reasons:

- It ships with the deploy. A change to the policy goes through a pull
  request, where the evals can run on it, and both agents get it together.
- Nothing written to the workspace can replace it: the general agent leaves
  a workspace skill of this name out of its catalog (see ``../general_agent.py``). The
  name below is the one the old workspace skill used, so this version takes
  its place as soon as it deploys.

It is a deferred capability, not a ``SKILL.md`` directory read by the
harness ``Skills`` capability, because its text is assembled from
``policy.md`` at import. The model sees it in the same catalog as the
workspace skills and loads it the same way, with ``load_capability``.
"""

from pydantic_ai.capabilities import AbstractCapability

from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.zillow_leads.agent import load_policy

SKILL_NAME = "zillow-auto-reply"

DESCRIPTION = (
    "How Sernia answers Zillow rental leads: when to reply, stay quiet or hand "
    "off to a person, how to qualify a lead, how to offer tours, and the "
    "standard answers about applying. A dedicated lead agent applies this "
    "policy to incoming Zillow emails. Load it to answer a question about how "
    "leads are handled, or to draft a reply to a lead when a person asks."
)

# Read by the general agent only. The lead agent never sees this text.
PREFACE = """\
This is the policy the dedicated Zillow lead agent follows. That agent handles
incoming Zillow emails, not you: code gives it one case (the listings with a
fact sheet for each unit, booking links, the tour times that may be offered,
tours already booked, and the email thread), it returns `reply`, `no_reply` or
`escalate`, and a person approves each reply before code sends it.

Use this policy when a person asks how leads are handled, or asks you to draft
a reply to a lead. Where it says "the case", it means the data listed above;
when you draft by hand, look up those facts yourself and do not state one you
could not find.

"""


def lead_policy_text() -> str:
    """What the general agent reads when it loads the skill.

    The heading matches the one the harness puts on a workspace skill, so the
    model can tell where this text starts when it loads several at once.
    """
    return f"# Skill: {SKILL_NAME}\n\n{PREFACE}{load_policy()}"


class LeadPolicySkill(AbstractCapability[SerniaDeps]):
    """The lead policy, offered to the model as a skill it can load.

    Instructions only, like a workspace skill: nothing here adds a tool.
    """

    def __init__(self) -> None:
        self.id = SKILL_NAME
        self.description = DESCRIPTION
        self.defer_loading = True
        self.instructions = lead_policy_text()

    def get_instructions(self) -> str:
        return self.instructions


def lead_policy_skill() -> LeadPolicySkill:
    return LeadPolicySkill()
