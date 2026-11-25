def format_assignees(task: dict) -> str:
    if task.get("assignees") is None:
        return "not reported"
    return (
        ", ".join(
            f"{user.get('username') or user.get('email') or '?'} (id: {user.get('id', '?')})"
            for user in task["assignees"]
        )
        or "unassigned"
    )
