"""Enable sleep on this project's ephemeral PR services without changing production."""

import json
import os
import urllib.request


def query(document: str, variables: dict) -> dict:
    request = urllib.request.Request(
        "https://backboard.railway.com/graphql/v2",
        data=json.dumps({"query": document, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['RAILWAY_API_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError("Railway rejected the PR sleep operation")
    return result["data"]


def main() -> None:
    environment_id = os.environ["RAILWAY_ENV_ID"]
    environment = query(
        """query($id: String!) { environment(id: $id) {
          projectId isEphemeral name serviceInstances { edges { node {
            serviceId serviceName cronSchedule sleepApplication
            latestDeployment { id }
          } } }
        } }""",
        {"id": environment_id},
    )["environment"]
    if (
        not environment["isEphemeral"]
        or environment["projectId"] != os.environ["RAILWAY_PROJECT_ID"]
        or environment["name"] == "production"
    ):
        raise RuntimeError("Refusing to configure a non-PR or unrelated environment")
    for edge in environment["serviceInstances"]["edges"]:
        service = edge["node"]
        if service["cronSchedule"] or service["sleepApplication"]:
            continue
        query(
            """mutation($service: String!, $environment: String!) {
              serviceInstanceUpdate(serviceId: $service, environmentId: $environment,
                input: {sleepApplication: true})
            }""",
            {"service": service["serviceId"], "environment": environment_id},
        )
        print(f"Sleep enabled: {service['serviceName']}")
        if service["latestDeployment"]:
            query(
                """mutation($service: String!, $environment: String!) {
                  serviceInstanceDeployV2(serviceId: $service, environmentId: $environment)
                }""",
                {"service": service["serviceId"], "environment": environment_id},
            )


if __name__ == "__main__":
    main()
