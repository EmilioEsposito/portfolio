"""Reconcile only the FastAPI automation variable; never print provider variables."""

import argparse
import json
from pathlib import Path

from railway_pr_sleep import query

POLICY = json.loads(Path(__file__).with_suffix(".json").read_text())
KEY = "BACKGROUND_AUTOMATION_ENABLED"


def desired_value(environment: dict) -> str:
    if environment["isEphemeral"]:
        return POLICY["ephemeral"]
    name = environment["name"]
    if name not in POLICY["environments"]:
        raise ValueError(f"No automation policy for environment {name}")
    return POLICY["environments"][name]


def reconcile(environment_id: str | None = None, *, apply: bool = False) -> None:
    project = POLICY["projectId"]
    state = query(
        """query($id:String!) { project(id:$id) {
      environments { edges { node { id name isEphemeral } } }
      services { edges { node { id name } } }
    } }""",
        {"id": project},
    )["project"]
    services = [
        e["node"] for e in state["services"]["edges"] if e["node"]["name"] == POLICY["serviceName"]
    ]
    if len(services) != 1:
        raise RuntimeError("Expected exactly one FastAPI service")
    service = services[0]["id"]
    environments = [
        e["node"]
        for e in state["environments"]["edges"]
        if environment_id is None or e["node"]["id"] == environment_id
    ]
    if not environments:
        raise RuntimeError("Environment does not belong to the policy project")
    for environment in environments:
        value = desired_value(environment)
        variables = {"project": project, "environment": environment["id"], "service": service}
        read = """query($project:String!, $environment:String!, $service:String!) {
          variables(projectId:$project, environmentId:$environment, serviceId:$service)
        }"""
        current = query(read, variables)["variables"].get(KEY)
        if current == value:
            print(f"{environment['name']}: automation policy already applied")
            continue
        print(f"{environment['name']}: {KEY} -> {value}")
        if apply:
            query(
                """mutation($input:VariableCollectionUpsertInput!) {
              variableCollectionUpsert(input:$input)
            }""",
                {
                    "input": {
                        "projectId": project,
                        "environmentId": environment["id"],
                        "serviceId": service,
                        "variables": {KEY: value},
                        "skipDeploys": False,
                    }
                },
            )
            if query(read, variables)["variables"].get(KEY) != value:
                raise RuntimeError("Automation policy readback did not match")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    reconcile(args.environment_id, apply=args.apply)
