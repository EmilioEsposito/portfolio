"""A person's verdict on one output of the lead agent."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Label(BaseModel):
    """A person's verdict on one decision. Tied to the scenario, not to the
    case or the run, so it stays meaningful when operating data or the policy
    changes: the note says what a good response to this conversation needs."""

    scenario_id: str
    verdict: Literal["pass", "fail"] | None = None
    note: str = ""
    # Optional: the reply the reviewer would have sent. One good answer, not
    # the only one, so it is an example to learn from, never an exact target.
    corrected_reply: str = ""
    action: str = ""  # what the model did in the run that was reviewed
    # Which output this verdict judged. The model words the same decision
    # differently on every run, so a label applies to one output, and a later
    # run shows it as an earlier verdict to confirm, not as settled.
    output_hash: str = ""
    run: str = ""


def output_hash(decision: dict) -> str:
    """Identifies one output: the action and the words a reviewer judges."""
    judged = [decision["action"], decision.get("reply_body"), decision.get("question")]
    return hashlib.sha256(json.dumps(judged).encode()).hexdigest()[:12]


def read_labels(path: Path) -> list[Label]:
    """Labels from a file the review page downloaded, or a plain list of them."""
    content = json.loads(path.read_text())
    rows = content["labels"] if isinstance(content, dict) else content
    return [Label.model_validate(row) for row in rows]
