"""Policy reconciliation only changes the automation flag on the owned API."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import railway_automation_policy as policy


@pytest.mark.parametrize(
    "name,ephemeral,expected",
    [
        ("production", False, "true"),
        ("development", False, "false"),
        ("portfolio-pr-123", True, "false"),
    ],
)
def test_hosted_defaults(name, ephemeral, expected):
    assert policy.desired_value({"name": name, "isEphemeral": ephemeral}) == expected


def test_reconcile_only_updates_owned_flag_and_is_idempotent(monkeypatch):
    values = {policy.KEY: "true", "UNRELATED": "preserve-me"}
    writes = []

    def query(document, variables):
        if "project(id:" in document:
            return {
                "project": {
                    "services": {"edges": [{"node": {"id": "api", "name": "fastapi"}}]},
                    "environments": {
                        "edges": [
                            {
                                "node": {
                                    "id": "preview",
                                    "name": "portfolio-pr-123",
                                    "isEphemeral": True,
                                }
                            }
                        ]
                    },
                }
            }
        if "variableCollectionUpsert" in document:
            update = variables["input"]
            assert update["serviceId"] == "api"
            assert update["environmentId"] == "preview"
            assert update["variables"] == {policy.KEY: "false"}
            writes.append(update)
            values.update(update["variables"])
            return {"variableCollectionUpsert": True}
        return {"variables": dict(values)}

    monkeypatch.setattr(policy, "query", query)
    policy.reconcile(apply=False)
    assert not writes
    policy.reconcile(apply=True)
    policy.reconcile(apply=True)
    assert len(writes) == 1
    assert values["UNRELATED"] == "preserve-me"
