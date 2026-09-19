"""Human-friendly issue keys (HAG-12) on top of the UUID primary keys.

Numbers are per workspace and assigned when an issue is inserted (see models.py). Any command
that takes an issue id also accepts its key, resolved in the active workspace.
"""

import re

from sqlalchemy import select

from hagent.models import Issue, Project, Workspace

KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{1,9})-(\d{1,9})$")


def derive_prefix(name: str) -> str:
    letters = "".join(ch for ch in (name or "").upper() if ch.isalnum())
    return letters[:3] if len(letters) >= 2 else "ISS"


def workspace_prefix(workspace: Workspace) -> str:
    return (workspace.issue_prefix or "").strip().upper() or derive_prefix(workspace.name)


def issue_key(session, issue: Issue) -> str:
    """The issue's key, or a short id fragment for issues that predate numbering."""
    if issue.number is None:
        return issue.id[:8]
    workspace = session.get(Workspace, issue.project.workspace_id)
    return f"{workspace_prefix(workspace)}-{issue.number}"


def resolve_issue_ref(session, ref):
    """Turn 'HAG-12' into the issue's UUID within the session's workspace; anything else is returned as is."""
    match = KEY_RE.match(ref) if isinstance(ref, str) else None
    workspace_id = session.info.get("workspace_id")
    if not match or not workspace_id:
        return ref
    workspace = session.get(Workspace, workspace_id)
    if workspace is None or match.group(1).upper() != workspace_prefix(workspace):
        return ref
    found = session.scalar(
        select(Issue.id).join(Project, Project.id == Issue.project_id).where(Project.workspace_id == workspace_id, Issue.number == int(match.group(2)))
    )
    return found or ref
