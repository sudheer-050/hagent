"""click-based CLI mirroring Multica's noun/verb structure: hagent <noun> <verb> ..."""

import json
import re
import contextlib
import os
import time
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime, timezone

import click
from sqlalchemy import select

from hagent.db import get_active_workspace, get_or_create_default_workspace, get_session, init_db, set_active_workspace
from hagent.engine import run_issue as engine_run_issue
from hagent.triggers import configure as configure_trigger
from hagent.models import (
    IssuePullRequest,
    ProjectResource,
    Agent,
    Autopilot,
    AutopilotTrigger,
    Comment,
    Issue,
    IssueStatus,
    Label,
    Project,
    ProjectStatus,
    Property,
    PropertyType,
    PropertyValue,
    Repo,
    Runtime,
    RuntimeType,
    Run,
    RunStatus,
    Skill,
    Squad,
    SquadMember,
    Attachment,
    IssueMetadata,
    IssueSubscriber,
    TimelineEvent,
    SkillFile,
    SquadActivity,
    TriggerType,
    UserProfile,
    Workspace,
    WorkspaceMember,
    McpServer,
    RoutingDecision,
)
from hagent.memory import MemoryScope, MemoryService
from hagent.router import ModelRouter

# All workspace-scoped CLI writes follow the locally selected workspace.
get_or_create_default_workspace = get_active_workspace


@click.group()
def cli():
    """Hagent: self-hosted multi-agent orchestration."""
    init_db()


# --- runtime ---

@cli.group()
def runtime():
    """Work with runtimes."""


@runtime.command("list")
def runtime_list():
    with get_session() as s:
        for r in s.scalars(select(Runtime)).all():
            click.echo(f"{r.id}  {r.name:<20} {r.type.value:<8} {r.model}")


@runtime.command("create")
@click.option("--name", required=True)
@click.option("--type", "runtime_type", required=True, type=click.Choice([t.value for t in RuntimeType]))
@click.option("--model", required=True)
@click.option("--config", default="{}")
def runtime_create(name, runtime_type, model, config):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        r = Runtime(workspace_id=ws.id, name=name, type=RuntimeType(runtime_type), model=model, config_json=config)
        s.add(r)
        s.commit()
        click.echo(f"Created runtime {r.id}")


# --- agent ---

@cli.group()
def agent():
    """Work with agents."""


@agent.command("list")
def agent_list():
    with get_session() as s:
        for a in s.scalars(select(Agent)).all():
            click.echo(f"{a.id}  {a.name:<20} runtime={a.runtime_id}")


@agent.command("get")
@click.argument("agent_id")
def agent_get(agent_id):
    with get_session() as s:
        a = s.get(Agent, agent_id)
        if not a:
            raise click.ClickException(f"Agent {agent_id} not found")
        skills = ", ".join(sk.name for sk in a.skills) or "(none)"
        click.echo(f"id: {a.id}\nname: {a.name}\nruntime_id: {a.runtime_id}\ninstructions: {a.instructions}\nskills: {skills}")


@agent.command("create")
@click.option("--name", required=True)
@click.option("--runtime", "runtime_id", required=True)
@click.option("--instructions", default="")
def agent_create(name, runtime_id, instructions):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        rt = s.get(Runtime, runtime_id)
        if not rt:
            raise click.ClickException(f"Runtime {runtime_id} not found")
        a = Agent(workspace_id=ws.id, runtime_id=rt.id, name=name, instructions=instructions)
        s.add(a)
        s.flush()
        s.commit()
        click.echo(f"Created agent {a.id}")


@agent.group()
def skills():
    """Manage an agent's skill assignments."""


@skills.command("add")
@click.argument("agent_id")
@click.option("--skill", "skill_id", required=True)
def agent_skills_add(agent_id, skill_id):
    with get_session() as s:
        a = s.get(Agent, agent_id)
        sk = s.get(Skill, skill_id)
        if not a or not sk:
            raise click.ClickException("Agent or skill not found")
        if sk not in a.skills:
            a.skills.append(sk)
            s.commit()
        click.echo(f"Attached skill {sk.name} to agent {a.name}")


# --- project ---

@cli.group()
def project():
    """Work with projects."""


@project.command("create")
@click.option("--name", required=True)
@click.option("--description", default="")
@click.option("--repo", "repo_urls", multiple=True, help="Attach a GitHub repo URL as a resource (repeatable).")
def project_create(name, description, repo_urls):
    repo_urls = [_validated_resource("github_repo", url) for url in repo_urls]
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        p = Project(workspace_id=ws.id, name=name, description=description)
        s.add(p)
        for position, url in enumerate(repo_urls):
            p.resources.append(ProjectResource(type="github_repo", ref=url, position=position))
        s.commit()
        click.echo(f"Created project {p.id}")


@project.command("list")
def project_list():
    with get_session() as s:
        for p in s.scalars(select(Project)).all():
            click.echo(f"{p.id}  {p.status.value:<10} {p.name}")


@project.command("get")
@click.argument("project_id")
def project_get(project_id):
    with get_session() as s:
        p = s.get(Project, project_id)
        if not p:
            raise click.ClickException(f"Project {project_id} not found")
        click.echo(f"id: {p.id}\nname: {p.name}\nstatus: {p.status.value}\ndescription: {p.description}\nissues: {len(p.issues)}")


@project.command("status")
@click.argument("project_id")
@click.argument("status", type=click.Choice([s.value for s in ProjectStatus]))
def project_status(project_id, status):
    with get_session() as s:
        p = s.get(Project, project_id)
        if not p:
            raise click.ClickException(f"Project {project_id} not found")
        p.status = ProjectStatus(status)
        s.commit()
        click.echo(f"Project {p.id} -> {status}")


# --- label ---

@cli.group()
def label():
    """Work with issue labels."""


@label.command("create")
@click.option("--name", required=True)
@click.option("--color", default="#888888")
def label_create(name, color):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        l = Label(workspace_id=ws.id, name=name, color=color)
        s.add(l)
        s.commit()
        click.echo(f"Created label {l.id}")


@label.command("list")
def label_list():
    with get_session() as s:
        for l in s.scalars(select(Label)).all():
            click.echo(f"{l.id}  {l.color:<10} {l.name}")


# --- property ---

@cli.group()
def property():
    """Manage workspace custom issue properties."""


@property.command("create")
@click.option("--name", required=True)
@click.option("--type", "prop_type", required=True, type=click.Choice([t.value for t in PropertyType]))
@click.option("--options", default="[]", help="JSON array, for type=select")
def property_create(name, prop_type, options):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        p = Property(workspace_id=ws.id, name=name, type=PropertyType(prop_type), options_json=options)
        s.add(p)
        s.commit()
        click.echo(f"Created property {p.id}")


@property.command("list")
def property_list():
    with get_session() as s:
        for p in s.scalars(select(Property)).all():
            click.echo(f"{p.id}  {p.type.value:<8} {p.name}")


# --- issue ---

@cli.group()
def issue():
    """Work with issues."""


PRIORITIES = ["none", "low", "medium", "high", "urgent"]
_DATE_FORMAT = "%Y-%m-%d"


def _valid_date(value):
    if value in (None, ""):
        return value
    try:
        datetime.strptime(value, _DATE_FORMAT)
    except ValueError:
        raise click.ClickException(f"Dates must look like YYYY-MM-DD, got '{value}'") from None
    return value


def _find_agent(s, ref):
    """An agent by id, exact name, or unique name prefix (case-insensitive)."""
    agent_row = s.get(Agent, ref)
    if agent_row:
        return agent_row
    matches = [a for a in s.scalars(select(Agent).where(Agent.archived.is_(False))).all() if a.name.lower() == ref.lower()]
    if not matches:
        matches = [a for a in s.scalars(select(Agent).where(Agent.archived.is_(False))).all() if a.name.lower().startswith(ref.lower())]
    if len(matches) == 1:
        return matches[0]
    raise click.ClickException(f"Agent '{ref}' not found" if not matches else f"'{ref}' matches several agents: " + ", ".join(a.name for a in matches))


def _issue_id(s, ref):
    """The UUID for an issue id or key, or ref unchanged if there is no such issue."""
    found = s.get(Issue, ref)
    return found.id if found else ref


def _issue_key(s, item):
    from hagent.keys import issue_key

    return issue_key(s, item)


def _report_started(run):
    if run is not None:
        click.echo(f"Queued run {run.id} ({run.status.value}). It starts when Hagent's server or daemon is running.")


@issue.command("create")
@click.option("--project", "project_id", required=True)
@click.option("--title", required=True)
@click.option("--description", default="")
@click.option("--assignee", "assignee_ref", default=None, help="Agent id or name. With --status todo/in_progress this starts the agent.")
@click.option("--parent", "parent_issue_id", default=None)
@click.option("--stage", type=click.IntRange(min=1), default=None, help="Barrier group among the parent's sub-issues; the parent's agent is woken when a whole stage finishes.")
@click.option("--priority", type=click.Choice(PRIORITIES), default="none", show_default=True)
@click.option("--start-date", default=None, help="YYYY-MM-DD")
@click.option("--due-date", default=None, help="YYYY-MM-DD")
@click.option("--status", default=IssueStatus.BACKLOG.value, type=click.Choice([s.value for s in IssueStatus]))
@click.option("--no-start", is_flag=True, help="Create without starting the assigned agent.")
def issue_create(project_id, title, description, assignee_ref, parent_issue_id, stage, priority, start_date, due_date, status, no_start):
    from hagent.orchestration import AUTO_START_STATUSES, start_agent_run

    if stage is not None and not parent_issue_id:
        raise click.ClickException("--stage needs --parent: stages group a parent's sub-issues")
    with get_session() as s:
        p = s.get(Project, project_id)
        if not p:
            raise click.ClickException(f"Project {project_id} not found")
        if parent_issue_id and not s.get(Issue, parent_issue_id):
            raise click.ClickException(f"Parent issue {parent_issue_id} not found")
        agent_row = _find_agent(s, assignee_ref) if assignee_ref else None
        i = Issue(
            project_id=p.id,
            title=title,
            description=description,
            assignee_agent_id=agent_row.id if agent_row else None,
            parent_issue_id=_issue_id(s, parent_issue_id) if parent_issue_id else None,
            stage=stage,
            priority=priority,
            start_date=_valid_date(start_date),
            due_date=_valid_date(due_date),
            status=IssueStatus(status),
        )
        s.add(i)
        s.commit()
        click.echo(f"Created issue {i.id}")
        click.echo(f"key: {_issue_key(s, i)}")
        if agent_row and not no_start and i.status in AUTO_START_STATUSES:
            _report_started(start_agent_run(s, i))


_SORT_COLUMNS = {
    "created": lambda: Issue.created_at, "updated": lambda: Issue.updated_at, "number": lambda: Issue.number,
    "due": lambda: Issue.due_date, "title": lambda: Issue.title, "position": lambda: Issue.position,
}


@issue.command("list")
@click.option("--project", "project_id", default=None)
@click.option("--status", "statuses", multiple=True, type=click.Choice([s.value for s in IssueStatus]), help="Repeatable.")
@click.option("--priority", "priorities", multiple=True, type=click.Choice(PRIORITIES), help="Repeatable.")
@click.option("--assignee", "assignee_ref", default=None, help="Agent id or name.")
@click.option("--sort", type=click.Choice([*_SORT_COLUMNS, "priority"]), default="created", show_default=True)
@click.option("--direction", type=click.Choice(["asc", "desc"]), default="asc", show_default=True)
@click.option("--limit", type=click.IntRange(min=1), default=None)
@click.option("--offset", type=click.IntRange(min=0), default=0)
def issue_list(project_id, statuses, priorities, assignee_ref, sort, direction, limit, offset):
    from hagent.orchestration import PRIORITY_RANK

    with get_session() as s:
        stmt = select(Issue)
        if project_id:
            stmt = stmt.where(Issue.project_id == project_id)
        if statuses:
            stmt = stmt.where(Issue.status.in_([IssueStatus(v) for v in statuses]))
        if priorities:
            stmt = stmt.where(Issue.priority.in_(priorities))
        if assignee_ref:
            stmt = stmt.where(Issue.assignee_agent_id == _find_agent(s, assignee_ref).id)
        if sort == "priority":
            rows = s.scalars(stmt.order_by(Issue.created_at.asc())).all()
            rows.sort(key=lambda r: PRIORITY_RANK.get(r.priority or "none", 4))
        else:
            rows = s.scalars(stmt.order_by(_SORT_COLUMNS[sort]().asc())).all()
        if direction == "desc":
            rows.reverse()
        rows = rows[offset:offset + limit] if limit else rows[offset:]
        for i in rows:
            click.echo(f"{i.id}  {_issue_key(s, i):<9} {i.status.value:<12} {(i.priority or 'none'):<7} {i.title}")


@issue.command("get")
@click.argument("issue_id")
def issue_get(issue_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        labels = ", ".join(l.name for l in i.labels) or "(none)"
        click.echo(
            f"id: {i.id}\nkey: {_issue_key(s, i)}\ntitle: {i.title}\nstatus: {i.status.value}\npriority: {i.priority or 'none'}\n"
            f"project_id: {i.project_id}\nparent_issue_id: {i.parent_issue_id}\nstage: {i.stage}\n"
            f"start_date: {i.start_date}\ndue_date: {i.due_date}\n"
            f"assignee_agent_id: {i.assignee_agent_id}\nlabels: {labels}\ndescription: {i.description}\n"
            f"runs: {len(i.runs)}\ncomments: {len(i.comments)}"
        )


@issue.command("status")
@click.argument("issue_id")
@click.argument("status", type=click.Choice([s.value for s in IssueStatus]))
@click.option("--no-start", is_flag=True, help="Change status without starting the assigned agent.")
def issue_status(issue_id, status, no_start):
    from hagent.orchestration import apply_status_change

    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        previous = i.status
        i.status = IssueStatus(status)
        s.add(TimelineEvent(issue_id=i.id, event_type="status_changed", detail=status))
        s.commit()
        click.echo(f"Issue {i.id} -> {status}")
        _report_started(apply_status_change(s, i, previous, start=not no_start))


@issue.command("assign")
@click.argument("issue_id")
@click.option("--agent", "--to", "agent_ref", default=None, help="Agent id or name.")
@click.option("--unassign", is_flag=True, help="Remove the current assignee.")
@click.option("--no-start", is_flag=True, help="Assign without starting the agent.")
def issue_assign(issue_id, agent_ref, unassign, no_start):
    from hagent.orchestration import start_agent_run

    if bool(agent_ref) == bool(unassign):
        raise click.ClickException("Give either --agent/--to or --unassign")
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException("Issue not found")
        if unassign:
            i.assignee_agent_id = None
            s.add(TimelineEvent(issue_id=i.id, event_type="assigned", detail="unassigned"))
            s.commit()
            click.echo(f"Issue {i.id} unassigned")
            return
        a = _find_agent(s, agent_ref)
        i.assignee_agent_id = a.id
        s.add(TimelineEvent(issue_id=i.id, event_type="assigned", detail=a.name))
        s.commit()
        click.echo(f"Issue {i.id} assigned to {a.name}")
        if not no_start:
            _report_started(start_agent_run(s, i))


@issue.group()
def label_cmd():
    """Manage labels on an issue."""


cli.commands["issue"].add_command(label_cmd, name="label")


@label_cmd.command("add")
@click.argument("issue_id")
@click.option("--label", "label_id", required=True)
def issue_label_add(issue_id, label_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        l = s.get(Label, label_id)
        if not i or not l:
            raise click.ClickException("Issue or label not found")
        if l not in i.labels:
            i.labels.append(l)
            s.commit()
        click.echo(f"Added label {l.name} to issue {i.id}")


@issue.group()
def property_cmd():
    """Manage custom property values on an issue."""


cli.commands["issue"].add_command(property_cmd, name="property")


@property_cmd.command("set")
@click.argument("issue_id")
@click.option("--property", "property_id", required=True)
@click.option("--value", required=True)
def issue_property_set(issue_id, property_id, value):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        p = s.get(Property, property_id)
        if not i or not p:
            raise click.ClickException("Issue or property not found")
        existing = next((pv for pv in i.property_values if pv.property_id == p.id), None)
        if existing:
            existing.value = value
        else:
            s.add(PropertyValue(issue_id=i.id, property_id=p.id, value=value))
        s.commit()
        click.echo(f"Set {p.name}={value} on issue {i.id}")


@issue.group()
def comment():
    """Work with issue comments."""


def _comment_or_fail(s, issue_id, comment_id):
    i = s.get(Issue, issue_id)
    if not i:
        raise click.ClickException(f"Issue {issue_id} not found")
    matches = [c for c in i.comments if c.id == comment_id or c.id.startswith(comment_id)]
    if len(matches) != 1:
        raise click.ClickException(f"Comment {comment_id} not found on this issue" if not matches else f"'{comment_id}' matches several comments; use more characters")
    return i, matches[0]


@comment.command("add")
@click.argument("issue_id")
@click.option("--body", required=True)
@click.option("--parent", "parent_comment_id", default=None, help="Reply to this comment (id or unique prefix).")
def issue_comment_add(issue_id, body, parent_comment_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        parent_id = _comment_or_fail(s, issue_id, parent_comment_id)[1].id if parent_comment_id else None
        c = Comment(issue_id=i.id, body=body, parent_comment_id=parent_id)
        s.add(c)
        s.commit()
        click.echo(f"Added comment {c.id}")


@comment.command("list")
@click.argument("issue_id")
@click.option("--roots-only", is_flag=True, help="Hide replies.")
@click.option("--tail", type=click.IntRange(min=1), default=None, help="Only the last N comments.")
@click.option("--unresolved", is_flag=True, help="Hide resolved threads.")
def issue_comment_list(issue_id, roots_only, tail, unresolved):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        rows = [c for c in i.comments if not (roots_only and c.parent_comment_id) and not (unresolved and c.resolved)]
        for c in rows[-tail:] if tail else rows:
            indent = "    " if c.parent_comment_id else ""
            mark = " (resolved)" if c.resolved else ""
            click.echo(f"{indent}{c.id[:8]}  [{c.created_at}] {c.author}: {c.body}{mark}")


@comment.command("update")
@click.argument("issue_id")
@click.argument("comment_id")
@click.option("--body", required=True)
def issue_comment_update(issue_id, comment_id, body):
    with get_session() as s:
        _, c = _comment_or_fail(s, issue_id, comment_id)
        c.body = body
        s.commit()
        click.echo(f"Updated comment {c.id}")


@comment.command("delete")
@click.argument("issue_id")
@click.argument("comment_id")
def issue_comment_delete(issue_id, comment_id):
    """Delete a comment and any replies to it."""
    with get_session() as s:
        i, c = _comment_or_fail(s, issue_id, comment_id)
        doomed, frontier = {c.id}, [c.id]
        while frontier:
            children = [x.id for x in i.comments if x.parent_comment_id in frontier and x.id not in doomed]
            doomed.update(children)
            frontier = children
        for attachment in s.scalars(select(Attachment).where(Attachment.comment_id.in_(doomed))).all():
            attachment.comment_id = None  # the file stays on the issue
        for x in [x for x in i.comments if x.id in doomed]:
            s.delete(x)
        s.commit()
        click.echo(f"Deleted {len(doomed)} comment(s)")


@comment.command("resolve")
@click.argument("issue_id")
@click.argument("comment_id")
def issue_comment_resolve(issue_id, comment_id):
    with get_session() as s:
        _, c = _comment_or_fail(s, issue_id, comment_id)
        c.resolved = True
        s.commit()
        click.echo(f"Resolved comment {c.id}")


@comment.command("unresolve")
@click.argument("issue_id")
@click.argument("comment_id")
def issue_comment_unresolve(issue_id, comment_id):
    with get_session() as s:
        _, c = _comment_or_fail(s, issue_id, comment_id)
        c.resolved = False
        s.commit()
        click.echo(f"Reopened comment {c.id}")


@issue.command("rerun")
@click.argument("issue_id")
@click.option("--prompt", default=None)
def issue_rerun(issue_id, prompt):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        if not i.assignee_agent_id:
            raise click.ClickException("Issue has no assignee; run `hagent issue assign` first")
        a = s.get(Agent, i.assignee_agent_id)
        run = engine_run_issue(s, i, a, prompt=prompt)
        click.echo(f"status: {run.status.value}\noutput: {run.output}\nerror: {run.error}")


# --- squad ---

@cli.group()
def squad():
    """Work with squads."""


@squad.command("create")
@click.option("--name", required=True)
@click.option("--description", default="")
def squad_create(name, description):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        sq = Squad(workspace_id=ws.id, name=name, description=description)
        s.add(sq)
        s.commit()
        click.echo(f"Created squad {sq.id}")


@squad.command("list")
def squad_list():
    with get_session() as s:
        for sq in s.scalars(select(Squad)).all():
            click.echo(f"{sq.id}  {sq.name} ({len(sq.members)} members)")


@squad.group()
def member():
    """Work with squad members."""


@member.command("add")
@click.argument("squad_id")
@click.option("--agent", "agent_id", required=True)
@click.option("--role", default="member")
def squad_member_add(squad_id, agent_id, role):
    with get_session() as s:
        sq = s.get(Squad, squad_id)
        a = s.get(Agent, agent_id)
        if not sq or not a:
            raise click.ClickException("Squad or agent not found")
        s.add(SquadMember(squad_id=sq.id, agent_id=a.id, role=role))
        s.commit()
        click.echo(f"Added {a.name} to squad {sq.name}")


# --- skill ---

@cli.group()
def skill():
    """Work with skills."""


@skill.command("create")
@click.option("--name", required=True)
@click.option("--description", default="")
@click.option("--content", default="")
@click.option("--file", "content_file", type=click.Path(exists=True), default=None)
def skill_create(name, description, content, content_file):
    if content_file:
        content = open(content_file, encoding="utf-8").read()
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        sk = Skill(workspace_id=ws.id, name=name, description=description, content=content)
        s.add(sk)
        s.commit()
        click.echo(f"Created skill {sk.id}")


@skill.command("list")
def skill_list():
    with get_session() as s:
        for sk in s.scalars(select(Skill)).all():
            click.echo(f"{sk.id}  {sk.name}")


@skill.command("get")
@click.argument("skill_id")
def skill_get(skill_id):
    with get_session() as s:
        sk = s.get(Skill, skill_id)
        if not sk:
            raise click.ClickException(f"Skill {skill_id} not found")
        files = "\n".join(f"- {item.filename}" for item in sk.files)
        click.echo(f"id: {sk.id}\nname: {sk.name}\ndescription: {sk.description}\nsource_url: {sk.source_url or ''}\nfiles:\n{files or '(none)'}\ncontent:\n{sk.content}")


# --- autopilot ---

@cli.group()
def autopilot():
    """Manage autopilots (scheduled agent automations)."""


AUTOPILOT_MODES = ["filter", "create_issue"]


def _check_autopilot_config(s, mode, project_id, description, template):
    from hagent.triggers import validate_title_template

    validate_title_template(template)
    if project_id and not s.get(Project, project_id):
        raise click.ClickException(f"Project {project_id} not found")
    if mode == "create_issue" and not project_id:
        raise click.ClickException("create_issue mode needs --project: that is where each new issue is created")
    if mode == "create_issue" and not (description or "").strip():
        raise click.ClickException("create_issue mode needs --description: it is the task given to the agent each time")


@autopilot.command("create")
@click.option("--name", "--title", "name", required=True)
@click.option("--agent", "agent_ref", required=True, help="Agent id or name.")
@click.option("--project", "project_id", default=None)
@click.option("--filter-status", default=None, type=click.Choice([s.value for s in IssueStatus]))
@click.option("--mode", type=click.Choice(AUTOPILOT_MODES), default="filter", show_default=True,
              help="filter: run the agent on existing matching issues. create_issue: create a new issue every time and start the agent on it.")
@click.option("--description", default="", help="create_issue mode: the task for each new issue.")
@click.option("--issue-title-template", default="", help="create_issue mode: e.g. 'Nightly report {{date}}'. Only {{date}} is available.")
def autopilot_create(name, agent_ref, project_id, filter_status, mode, description, issue_title_template):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        a = _find_agent(s, agent_ref)
        _check_autopilot_config(s, mode, project_id, description, issue_title_template)
        ap = Autopilot(
            workspace_id=ws.id,
            name=name,
            agent_id=a.id,
            project_id=project_id,
            filter_status=IssueStatus(filter_status) if filter_status else None,
            mode=mode,
            description=description,
            issue_title_template=issue_title_template,
        )
        s.add(ap)
        s.commit()
        click.echo(f"Created autopilot {ap.id}")


@autopilot.command("list")
def autopilot_list():
    with get_session() as s:
        for ap in s.scalars(select(Autopilot)).all():
            click.echo(f"{ap.id}  enabled={ap.enabled}  {ap.name}")


@autopilot.command("trigger-add")
@click.argument("autopilot_id")
@click.option("--cron", default=None, help="Standard 5-field cron expression, e.g. '*/5 * * * *'")
@click.option("--webhook", is_flag=True, help="Create an HTTP webhook trigger")
@click.option("--timezone", "timezone_name", default=None, help="IANA zone the cron fires in, e.g. Asia/Kolkata (default: this machine's zone).")
@click.option("--label", default="", help="A name for this trigger.")
def autopilot_trigger_add(autopilot_id, cron, webhook, timezone_name, label):
    with get_session() as s:
        ap = s.get(Autopilot, autopilot_id)
        if not ap:
            raise click.ClickException(f"Autopilot {autopilot_id} not found")
        if bool(cron) == webhook:
            raise click.UsageError("Pass exactly one of --cron or --webhook")
        t = AutopilotTrigger(
            autopilot_id=ap.id,
            cron_expression=cron,
            type=TriggerType.WEBHOOK if webhook else TriggerType.CRON,
            webhook_token=secrets.token_urlsafe(32) if webhook else None,
        )
        configure_trigger(t, cron=cron, webhook=webhook, timezone_name=timezone_name, label=label)
        s.add(t)
        s.commit()
        click.echo(f"Added trigger {t.id} ({t.webhook_token or cron}) to autopilot {ap.name}")


@autopilot.command("trigger")
@click.argument("autopilot_id")
def autopilot_trigger_now(autopilot_id):
    """Manually trigger an autopilot to run once."""
    from hagent.scheduler import run_autopilot_once

    with get_session() as s:
        if not s.get(Autopilot, autopilot_id): raise click.ClickException("Autopilot not found")
    result = run_autopilot_once(autopilot_id)
    if result is None:
        raise click.ClickException("Autopilot not found or disabled")
    click.echo(f"autopilot_run {result.id}: {result.status} — {result.summary}")


@autopilot.command("runs")
@click.argument("autopilot_id")
def autopilot_runs(autopilot_id):
    from hagent.models import AutopilotRun

    with get_session() as s:
        runs = s.scalars(select(AutopilotRun).where(AutopilotRun.autopilot_id == autopilot_id)).all()
        for r in runs:
            click.echo(f"{r.id}  {r.status:<10} {r.summary}")


# --- repo ---

@cli.group()
def repo():
    """Work with repositories."""


@repo.command("add")
@click.option("--name", required=True)
@click.option("--url", default=None, help="Remote URL. Required unless --local-path points at an existing checkout.")
@click.option("--local-path", "local_path", default=None, type=click.Path(exists=True, file_okay=False), help="Register an already-existing local git checkout directly, without cloning.")
def repo_add(name, url, local_path):
    if not url and not local_path:
        raise click.ClickException("Provide --url, or --local-path for an existing checkout")
    resolved_local_path = None
    if local_path:
        target = Path(local_path).resolve()
        if not (target / ".git").exists():
            raise click.ClickException(f"{target} is not a git repository (no .git found)")
        resolved_local_path = str(target)
        if not url:
            origin = subprocess.run(["git", "-C", resolved_local_path, "remote", "get-url", "origin"], capture_output=True, text=True)
            url = origin.stdout.strip() if origin.returncode == 0 and origin.stdout.strip() else resolved_local_path
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        r = Repo(workspace_id=ws.id, name=name, url=url, local_path=resolved_local_path)
        s.add(r)
        s.commit()
        click.echo(f"Added repo {r.id}")


@repo.command("list")
def repo_list():
    with get_session() as s:
        for r in s.scalars(select(Repo)).all():
            click.echo(f"{r.id}  {r.name:<20} {r.url}")


# --- daemon ---

@cli.group()
def daemon():
    """Control the local autopilot scheduler daemon."""


@daemon.command("start")
def daemon_start():
    """Run the autopilot scheduler in the foreground (Ctrl+C to stop)."""
    from hagent.scheduler import start_scheduler

    sched = start_scheduler()
    click.echo(f"Scheduler started with {len(sched.get_jobs())} job(s). Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sched.shutdown()
        click.echo("Scheduler stopped.")


# --- Phase 3: workspace, user, and issue extras ---

@cli.group()
def workspace():
    """Manage local workspaces and their members."""


@workspace.command("create")
@click.option("--name", required=True)
def workspace_create(name):
    with get_session() as s:
        ws = Workspace(name=name)
        s.add(ws)
        s.commit()
        click.echo(ws.id)


@workspace.command("list")
def workspace_list():
    with get_session() as s:
        active = get_active_workspace(s).id
        for ws in s.scalars(select(Workspace)).all():
            click.echo(f"{ws.id}  {'*' if ws.id == active else ' '} {ws.name}")


@workspace.command("get")
@click.argument("workspace_id")
def workspace_get(workspace_id):
    with get_session() as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise click.ClickException("Workspace not found")
        click.echo(f"id: {ws.id}\nname: {ws.name}\ncreated_at: {ws.created_at}")


@workspace.command("update")
@click.argument("workspace_id")
@click.option("--name", required=True)
def workspace_update(workspace_id, name):
    with get_session() as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise click.ClickException("Workspace not found")
        ws.name = name
        s.commit()
        click.echo(ws.id)


@workspace.command("switch")
@click.argument("workspace_id")
def workspace_switch(workspace_id):
    with get_session() as s:
        if not s.get(Workspace, workspace_id):
            raise click.ClickException("Workspace not found")
    set_active_workspace(workspace_id)
    click.echo(f"Active workspace: {workspace_id}")


@workspace.group("member")
def workspace_member():
    """Manage workspace members."""


@workspace_member.command("add")
@click.argument("workspace_id")
@click.option("--name", required=True)
@click.option("--role", default="member")
def workspace_member_add(workspace_id, name, role):
    with get_session() as s:
        if not s.get(Workspace, workspace_id):
            raise click.ClickException("Workspace not found")
        member = WorkspaceMember(workspace_id=workspace_id, name=name, role=role)
        s.add(member)
        s.commit()
        click.echo(member.id)


@workspace_member.command("list")
@click.argument("workspace_id")
def workspace_member_list(workspace_id):
    with get_session() as s:
        for m in s.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)).all():
            click.echo(f"{m.id}  {m.role:<10} {m.name}")


@cli.group()
def user():
    """Manage the local user profile."""


@user.group("profile")
def user_profile():
    pass


@user_profile.command("get")
def user_profile_get():
    with get_session() as s:
        profile = s.scalar(select(UserProfile).order_by(UserProfile.updated_at.desc()))
        if not profile:
            click.echo("No profile configured")
            return
        click.echo(f"id: {profile.id}\nname: {profile.name}\nemail: {profile.email}\nbio: {profile.bio}")


@user_profile.command("update")
@click.option("--name", default=None)
@click.option("--email", default=None)
@click.option("--bio", default=None)
def user_profile_update(name, email, bio):
    with get_session() as s:
        profile = s.scalar(select(UserProfile).order_by(UserProfile.updated_at.desc()))
        if not profile:
            profile = UserProfile()
            s.add(profile)
        if name is not None: profile.name = name
        if email is not None: profile.email = email
        if bio is not None: profile.bio = bio
        s.commit()
        click.echo(profile.id)


@issue.command("children")
@click.argument("issue_id")
def issue_children(issue_id):
    with get_session() as s:
        children = s.scalars(select(Issue).where(Issue.parent_issue_id == _issue_id(s, issue_id)).order_by(Issue.position, Issue.created_at)).all()
        staged = sorted({c.stage for c in children if c.stage is not None})
        for stage in [*staged, None]:
            group = [c for c in children if c.stage == stage]
            if not group:
                continue
            if staged:
                click.echo(f"stage {stage}:" if stage is not None else "unstaged:")
            for child in group:
                click.echo(f"{'  ' if staged else ''}{child.status.value}: {child.id} {child.title}")


@issue.command("search")
@click.argument("query")
def issue_search(query):
    needle = query.lower()
    with get_session() as s:
        for i in s.scalars(select(Issue)).all():
            if needle in (" ".join([i.title, i.description] + [c.body for c in i.comments])).lower():
                click.echo(f"{i.id}  {i.status.value:<12} {i.title}")


@issue.command("usage")
@click.argument("issue_id")
def issue_usage(issue_id):
    with get_session() as s:
        runs = s.scalars(select(Run).where(Run.issue_id == issue_id)).all()
        click.echo(f"estimated_tokens: {sum(r.token_estimate or 0 for r in runs)}\nruns: {len(runs)}")


@issue.command("cancel-task")
@click.argument("issue_id")
def issue_cancel_task(issue_id):
    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item: raise click.ClickException("Issue not found")
        from hagent.engine import cancel_issue
        count = cancel_issue(s, item)
        click.echo(f"Cancelled {count} running task(s)")


@issue.command("timeline")
@click.argument("issue_id")
def issue_timeline(issue_id):
    with get_session() as s:
        for event in s.scalars(select(TimelineEvent).where(TimelineEvent.issue_id == issue_id).order_by(TimelineEvent.created_at)).all():
            click.echo(f"[{event.created_at}] {event.event_type}: {event.detail}")


@issue.command("continue")
@click.argument("issue_id")
@click.option("--prompt", required=True, help="Follow-up message to send into the resumed conversation.")
def issue_continue(issue_id, prompt):
    """Resume the actual prior conversation for this issue's most recent run - either a
    completed one (send a follow-up) or a failed one that recovered a checkpoint (pick
    up where it left off) - instead of starting a fresh session with no memory."""
    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item:
            raise click.ClickException("Issue not found")
        last_run = s.scalar(
            select(Run)
            .where(Run.issue_id == issue_id, Run.status.in_([RunStatus.COMPLETED, RunStatus.FAILED]), Run.session_id.is_not(None))
            .order_by(Run.created_at.desc())
        )
        if not last_run:
            raise click.ClickException("No completed or checkpointed run with a resumable session exists for this issue yet")
        agent = s.get(Agent, last_run.agent_id)
        if not agent:
            raise click.ClickException("The agent that ran this issue no longer exists")
        from hagent.engine import run_issue as engine_run_issue
        run = engine_run_issue(s, item, agent, prompt=prompt, resume_session_id=last_run.session_id)
        s.refresh(run)
        click.echo(f"status: {run.status.value}")
        if run.output:
            click.echo(f"output: {run.output}")
        if run.error:
            click.echo(f"error: {run.error}")


@issue.command("diff")
@click.argument("issue_id")
def issue_diff(issue_id):
    """Show the diff between this issue's isolated worktree branch and the repo's base branch."""
    from hagent.worktrees import base_branch, branch_name_for_issue, worktree_path_for_issue
    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item:
            raise click.ClickException("Issue not found")
        project = s.get(Project, item.project_id)
        if not project or not project.repo_id:
            raise click.ClickException("This issue's project isn't linked to a repo (see 'project update --repo')")
        repo = s.get(Repo, project.repo_id)
        if not repo or not repo.local_path:
            raise click.ClickException("Repo has no local checkout (see 'repo add --local-path' or 'repo checkout')")
        branch = branch_name_for_issue(issue_id)
        base = base_branch(repo.local_path)
        if not base:
            raise click.ClickException("Could not determine the repo's base branch (detached HEAD?)")
        worktree_path = worktree_path_for_issue(repo.local_path, issue_id)
        if not Path(worktree_path).is_dir():
            raise click.ClickException(f"No worktree exists yet for this issue - it hasn't run against '{repo.name}' yet")
        result = subprocess.run(
            ["git", "diff", f"{base}...{branch}"],
            cwd=worktree_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise click.ClickException(result.stderr.strip() or "git diff failed")
        click.echo(result.stdout or "(no changes)")


@issue.command("pr")
@click.argument("issue_id")
@click.option("--title", default=None, help="PR title. Defaults to the issue title.")
def issue_pr(issue_id, title):
    """Push this issue's worktree branch and open a real PR (requires a GitHub remote and the gh CLI)."""
    from hagent.worktrees import base_branch, branch_name_for_issue, worktree_path_for_issue
    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item:
            raise click.ClickException("Issue not found")
        project = s.get(Project, item.project_id)
        if not project or not project.repo_id:
            raise click.ClickException("This issue's project isn't linked to a repo")
        repo = s.get(Repo, project.repo_id)
        if not repo or not repo.local_path:
            raise click.ClickException("Repo has no local checkout")
        branch = branch_name_for_issue(issue_id)
        base = base_branch(repo.local_path)
        worktree_path = worktree_path_for_issue(repo.local_path, issue_id)
        if not Path(worktree_path).is_dir():
            raise click.ClickException("No worktree exists yet for this issue")

        remote = subprocess.run(["git", "-C", worktree_path, "remote", "get-url", "origin"], capture_output=True, text=True)
        if remote.returncode != 0 or not remote.stdout.strip():
            raise click.ClickException("This repo has no 'origin' remote configured - nothing to push to. Use 'issue diff' to review locally instead.")
        origin_url = remote.stdout.strip()
        if "github.com" not in origin_url:
            raise click.ClickException(f"Origin ({origin_url}) isn't a GitHub remote - 'gh pr create' won't work here. Use 'issue diff' instead.")

        push = subprocess.run(["git", "-C", worktree_path, "push", "-u", "origin", branch], capture_output=True, text=True)
        if push.returncode != 0:
            raise click.ClickException(f"Push failed: {push.stderr.strip()}")

        gh = shutil.which("gh")
        if not gh:
            click.echo(f"Branch '{branch}' pushed to origin. Install the 'gh' CLI to auto-create a PR, or open one manually.")
            return

        pr_title = title or item.title
        pr = subprocess.run(
            [gh, "pr", "create", "--repo", origin_url, "--head", branch, "--base", base or "main",
             "--title", pr_title, "--body", f"Automated PR for Hagent issue {issue_id}.\n\n{item.description or ''}"],
            capture_output=True, text=True,
        )
        if pr.returncode != 0:
            raise click.ClickException(f"Branch pushed, but 'gh pr create' failed: {pr.stderr.strip()}")
        pr_url = pr.stdout.strip()
        _record_pull_request(s, issue_id, pr_url, pr_title)
        s.add(Comment(issue_id=issue_id, author="system", body=f"Opened PR: {pr_url}"))
        s.commit()
        click.echo(pr_url)


@issue.group("subscriber")
def issue_subscriber():
    pass


@issue_subscriber.command("add")
@click.argument("issue_id")
@click.option("--name", required=True)
def issue_subscriber_add(issue_id, name):
    with get_session() as s:
        if not s.get(Issue, issue_id):
            raise click.ClickException("Issue not found")
        item = IssueSubscriber(issue_id=_issue_id(s, issue_id), name=name)
        s.add(item); s.commit(); click.echo(item.id)


@issue_subscriber.command("remove")
@click.argument("issue_id")
@click.option("--name", required=True)
def issue_subscriber_remove(issue_id, name):
    with get_session() as s:
        gone = s.scalars(select(IssueSubscriber).where(IssueSubscriber.issue_id == _issue_id(s, issue_id), IssueSubscriber.name == name)).all()
        if not gone:
            raise click.ClickException(f"'{name}' is not subscribed to this issue")
        for item in gone:
            s.delete(item)
        s.commit()
        click.echo(f"Unsubscribed {name}")


@issue_subscriber.command("list")
@click.argument("issue_id")
def issue_subscriber_list(issue_id):
    with get_session() as s:
        for item in s.scalars(select(IssueSubscriber).where(IssueSubscriber.issue_id == _issue_id(s, issue_id))).all():
            click.echo(f"{item.id}  {item.name}")


@issue.group("metadata")
def issue_metadata():
    pass


@issue_metadata.command("set")
@click.argument("issue_id")
@click.option("--key", required=True)
@click.option("--value", required=True)
def issue_metadata_set(issue_id, key, value):
    with get_session() as s:
        if not s.get(Issue, issue_id):
            raise click.ClickException("Issue not found")
        issue_id = _issue_id(s, issue_id)
        item = s.scalar(select(IssueMetadata).where(IssueMetadata.issue_id == issue_id, IssueMetadata.key == key))
        if not item:
            item = IssueMetadata(issue_id=issue_id, key=key); s.add(item)
        item.value = value; s.commit(); click.echo(f"{key}={value}")


@issue_metadata.command("get")
@click.argument("issue_id")
@click.option("--key", default=None, help="Print only this key's value.")
def issue_metadata_get(issue_id, key):
    with get_session() as s:
        query = select(IssueMetadata).where(IssueMetadata.issue_id == _issue_id(s, issue_id))
        if key is not None:
            item = s.scalar(query.where(IssueMetadata.key == key))
            if not item:
                raise click.ClickException(f"No metadata key '{key}'")
            click.echo(item.value)
            return
        for item in s.scalars(query).all():
            click.echo(f"{item.key}={item.value}")


@issue_metadata.command("list")
@click.argument("issue_id")
def issue_metadata_list(issue_id):
    with get_session() as s:
        for item in s.scalars(select(IssueMetadata).where(IssueMetadata.issue_id == _issue_id(s, issue_id)).order_by(IssueMetadata.key)).all():
            click.echo(f"{item.key}={item.value}")


@issue_metadata.command("delete")
@click.argument("issue_id")
@click.option("--key", required=True)
def issue_metadata_delete(issue_id, key):
    with get_session() as s:
        item = s.scalar(select(IssueMetadata).where(IssueMetadata.issue_id == _issue_id(s, issue_id), IssueMetadata.key == key))
        if not item:
            raise click.ClickException(f"No metadata key '{key}'")
        s.delete(item); s.commit(); click.echo(f"Deleted {key}")


@issue.command("reorder")
@click.argument("issue_id")
@click.option("--position", required=True, type=int)
def issue_reorder(issue_id, position):
    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item: raise click.ClickException("Issue not found")
        item.position = position; s.commit(); click.echo(f"{item.id}: position={position}")


from hagent.skills import read_source as _read_skill_source


@skill.command("import")
@click.argument("source")
@click.option("--name", default=None)
@click.option("--description", default="")
def skill_import(source, name, description):
    files, cleanup = _read_skill_source(source)
    try:
        with get_session() as s:
            ws = get_active_workspace(s)
            sk = Skill(workspace_id=ws.id, name=name or Path(source).stem, description=description, source_url=str(Path(source).resolve()), content="\n".join(content for filename, content in files if filename.lower().endswith("skill.md")))
            s.add(sk); s.flush()
            for filename, content in files: s.add(SkillFile(skill_id=sk.id, filename=filename, content=content))
            s.commit(); click.echo(sk.id)
    finally:
        if cleanup: shutil.rmtree(cleanup, ignore_errors=True)


@skill.command("refresh")
@click.argument("skill_id")
def skill_refresh(skill_id):
    with get_session() as s:
        sk = s.get(Skill, skill_id)
        if not sk or not sk.source_url: raise click.ClickException("Skill has no import source")
        files, cleanup = _read_skill_source(sk.source_url)
        try:
            sk.content = "\n".join(content for filename, content in files if filename.lower().endswith("skill.md"))
            sk.files.clear()
            for filename, content in files: sk.files.append(SkillFile(filename=filename, content=content))
            s.commit(); click.echo(f"Refreshed {sk.id}")
        finally:
            if cleanup: shutil.rmtree(cleanup, ignore_errors=True)


@skill.command("search")
@click.argument("query")
def skill_search(query):
    with get_session() as s:
        q = query.lower()
        for sk in s.scalars(select(Skill)).all():
            if q in (f"{sk.name} {sk.description} {sk.content} " + " ".join(f.content for f in sk.files)).lower(): click.echo(f"{sk.id}  {sk.name}")


@squad.group("activity")
def squad_activity():
    pass


@squad_activity.command("add")
@click.argument("squad_id")
@click.option("--evaluation", required=True)
@click.option("--issue", "issue_id", default=None)
def squad_activity_add(squad_id, evaluation, issue_id):
    with get_session() as s:
        item = SquadActivity(squad_id=squad_id, issue_id=issue_id, evaluation=evaluation)
        s.add(item); s.commit(); click.echo(item.id)


@squad_activity.command("list")
@click.argument("squad_id")
def squad_activity_list(squad_id):
    with get_session() as s:
        for item in s.scalars(select(SquadActivity).where(SquadActivity.squad_id == squad_id).order_by(SquadActivity.created_at)).all():
            click.echo(f"[{item.created_at}] {item.evaluation}")


@autopilot.command("trigger-list")
@click.argument("autopilot_id")
def autopilot_trigger_list(autopilot_id):
    with get_session() as s:
        for t in s.scalars(select(AutopilotTrigger).where(AutopilotTrigger.autopilot_id == autopilot_id)).all():
            secret = f" /webhooks/{t.webhook_token}" if t.webhook_token else ""
            click.echo(f"{t.id}  {t.type.value}  {t.cron_expression or ''}{secret}")


@autopilot.command("trigger-delete")
@click.argument("trigger_id")
def autopilot_trigger_delete(trigger_id):
    with get_session() as s:
        t = s.get(AutopilotTrigger, trigger_id)
        if not t: raise click.ClickException("Trigger not found")
        s.delete(t); s.commit(); click.echo("deleted")


@autopilot.command("trigger-update")
@click.argument("trigger_id")
@click.option("--cron", default=None)
@click.option("--enabled", type=bool, default=None)
@click.option("--webhook", is_flag=True)
@click.option("--timezone", "timezone_name", default=None, help="IANA zone; '' clears it.")
@click.option("--label", default=None)
def autopilot_trigger_update(trigger_id, cron, enabled, webhook, timezone_name, label):
    with get_session() as s:
        t = s.get(AutopilotTrigger, trigger_id)
        if not t: raise click.ClickException("Trigger not found")
        configure_trigger(t, cron=cron, webhook=webhook, enabled=enabled, timezone_name=timezone_name, label=label)
        if cron is None and timezone_name is not None and t.cron_expression:
            configure_trigger(t, cron=t.cron_expression)  # re-check the expression under the new zone
        s.commit(); click.echo(t.id)


@autopilot.command("trigger-rotate-url")
@click.argument("trigger_id")
def autopilot_trigger_rotate_url(trigger_id):
    with get_session() as s:
        t = s.get(AutopilotTrigger, trigger_id)
        if not t: raise click.ClickException("Trigger not found")
        if t.type != TriggerType.WEBHOOK: raise click.ClickException("Only webhook triggers have URLs")
        configure_trigger(t, webhook=True)
        s.commit(); click.echo(f"/webhooks/{t.webhook_token}")


@repo.command("checkout")
@click.argument("repo_id")
@click.option("--path", required=True, type=click.Path(file_okay=False))
def repo_checkout(repo_id, path):
    with get_session() as s:
        r = s.get(Repo, repo_id)
        if not r: raise click.ClickException("Repo not found")
        target = Path(path).resolve()
        if target.exists() and any(target.iterdir()):
            origin = subprocess.run(["git", "-C", str(target), "remote", "get-url", "origin"], capture_output=True, text=True)
            if origin.returncode or origin.stdout.strip() != r.url:
                raise click.ClickException("Target is not a checkout of this repository")
            command = ["git", "-C", str(target), "pull", "--ff-only"]
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            command = ["git", "clone", "--", r.url, str(target)]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode: raise click.ClickException(completed.stderr.strip())
        r.local_path = str(target); s.commit(); click.echo(str(target))


@repo.command("remove")
@click.argument("repo_id")
def repo_remove(repo_id):
    with get_session() as s:
        r = s.get(Repo, repo_id)
        if not r: raise click.ClickException("Repo not found")
        s.delete(r); s.commit(); click.echo("removed")


@cli.group()
def attachment():
    """Upload and download local issue attachments."""


@attachment.command("upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--issue", "issue_id", default=None)
@click.option("--comment", "comment_id", default=None)
def attachment_upload(file, issue_id, comment_id):
    source = Path(file).resolve()
    if not issue_id and not comment_id: raise click.UsageError("Pass --issue or --comment")
    with get_session() as s:
        if comment_id:
            comment = s.get(Comment, comment_id)
            if not comment: raise click.ClickException("Comment not found")
            if issue_id and comment.issue_id != issue_id: raise click.ClickException("Comment belongs to another issue")
            issue_id = comment.issue_id
        if not s.get(Issue, issue_id): raise click.ClickException("Issue not found")
        target_dir = Path("attachments") / get_active_workspace(s).id
        target_dir.mkdir(parents=True, exist_ok=True)
        stored = target_dir / f"{secrets.token_hex(8)}-{source.name}"
        shutil.copy2(source, stored)
        item = Attachment(comment_id=comment_id, issue_id=issue_id, filename=source.name, path=str(stored.resolve()))
        s.add(item); s.commit(); click.echo(item.id)


@attachment.command("download")
@click.argument("attachment_id")
@click.option("--out", required=True, type=click.Path(dir_okay=False))
def attachment_download(attachment_id, out):
    with get_session() as s:
        item = s.get(Attachment, attachment_id)
        if not item: raise click.ClickException("Attachment not found")
        shutil.copy2(item.path, out); click.echo(out)


@workspace.group("mcp")
def workspace_mcp():
    """Register MCP protocol servers."""


@workspace_mcp.command("add")
@click.option("--name", required=True)
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default="stdio")
@click.option("--command", default=None)
@click.option("--args", "args_json", default="[]")
@click.option("--url", default=None)
@click.option("--config", default="{}")
def workspace_mcp_add(name, transport, command, args_json, url, config):
    if transport == "stdio" and not command: raise click.UsageError("stdio requires --command")
    if transport == "sse" and not url: raise click.UsageError("sse requires --url")
    with get_session() as s:
        ws = get_active_workspace(s)
        item = McpServer(workspace_id=ws.id, name=name, transport=transport, command=command, args_json=args_json, url=url, config_json=config)
        s.add(item); s.commit(); click.echo(item.id)


@workspace_mcp.command("list")
def workspace_mcp_list():
    with get_session() as s:
        for item in s.scalars(select(McpServer)).all(): click.echo(f"{item.id}  {item.transport}  {item.name}")


@workspace_mcp.command("remove")
@click.argument("server_id")
def workspace_mcp_remove(server_id):
    with get_session() as s:
        item = s.get(McpServer, server_id)
        if not item: raise click.ClickException("MCP server not found")
        s.delete(item); s.commit(); click.echo("removed")


@agent.group("mcp")
def agent_mcp():
    """Attach MCP servers to agents."""


@agent_mcp.command("add")
@click.argument("agent_id")
@click.option("--server", "server_id", required=True)
def agent_mcp_add(agent_id, server_id):
    with get_session() as s:
        a, server = s.get(Agent, agent_id), s.get(McpServer, server_id)
        if not a or not server: raise click.ClickException("Agent or MCP server not found")
        if server not in a.mcp_servers: a.mcp_servers.append(server); s.commit()
        click.echo("attached")


@agent_mcp.command("remove")
@click.argument("agent_id")
@click.option("--server", "server_id", required=True)
def agent_mcp_remove(agent_id, server_id):
    with get_session() as s:
        a, server = s.get(Agent, agent_id), s.get(McpServer, server_id)
        if not a or not server: raise click.ClickException("Agent or MCP server not found")
        if server in a.mcp_servers: a.mcp_servers.remove(server); s.commit()
        click.echo("detached")



# --- Multica-compatible lifecycle and history commands ---

@agent.command("archive")
@click.argument("agent_id")
def agent_archive(agent_id):
    with get_session() as s:
        item = s.get(Agent, agent_id)
        if not item:
            raise click.ClickException("Agent not found")
        item.archived = True
        s.commit()
        click.echo(f"Archived agent {item.id}")


@agent.command("restore")
@click.argument("agent_id")
def agent_restore(agent_id):
    with get_session() as s:
        item = s.get(Agent, agent_id)
        if not item:
            raise click.ClickException("Agent not found")
        item.archived = False
        s.commit()
        click.echo(f"Restored agent {item.id}")


@agent.command("update")
@click.argument("agent_id")
@click.option("--name", default=None)
@click.option("--runtime", "runtime_id", default=None)
@click.option("--backup-runtime", "backup_runtime_id", default=None, help="Runtime ID to fail over to on a primary-runtime error. Pass an empty string to clear it.")
@click.option("--verifier", "verifier_agent_id", default=None, help="Agent ID that automatically reviews this agent's completed work (PASS/FAIL). Pass an empty string to clear it.")
@click.option("--sandbox-image", default=None, help="Docker image to run this agent's terminal commands in when working on a git-worktree-isolated issue (e.g. mcr.microsoft.com/powershell). Only activates when a worktree exists; pass an empty string to clear it.")
@click.option("--instructions", default=None)
def agent_update(agent_id, name, runtime_id, backup_runtime_id, verifier_agent_id, sandbox_image, instructions):
    with get_session() as s:
        item = s.get(Agent, agent_id)
        if not item:
            raise click.ClickException("Agent not found")
        if name is not None:
            item.name = name
        if runtime_id is not None:
            if not s.get(Runtime, runtime_id):
                raise click.ClickException("Runtime not found")
            item.runtime_id = runtime_id
        if backup_runtime_id is not None:
            if backup_runtime_id == "":
                item.backup_runtime_id = None
            else:
                if not s.get(Runtime, backup_runtime_id):
                    raise click.ClickException("Backup runtime not found")
                item.backup_runtime_id = backup_runtime_id
        if verifier_agent_id is not None:
            if verifier_agent_id == "":
                item.verifier_agent_id = None
            else:
                if verifier_agent_id == agent_id:
                    raise click.ClickException("An agent cannot verify its own work")
                if not s.get(Agent, verifier_agent_id):
                    raise click.ClickException("Verifier agent not found")
                item.verifier_agent_id = verifier_agent_id
        if sandbox_image is not None:
            item.sandbox_image = sandbox_image or None
        if instructions is not None:
            item.instructions = instructions
        s.commit()
        click.echo(item.id)


@agent.command("copy")
@click.argument("agent_id")
@click.option("--name", required=True)
@click.option("--runtime", "runtime_id", default=None)
def agent_copy(agent_id, name, runtime_id):
    with get_session() as s:
        source = s.get(Agent, agent_id)
        if not source:
            raise click.ClickException("Agent not found")
        target = Agent(
            workspace_id=source.workspace_id,
            runtime_id=runtime_id or source.runtime_id,
            name=name,
            instructions=source.instructions,
            env_json=source.env_json,
        )
        if not s.get(Runtime, target.runtime_id):
            raise click.ClickException("Runtime not found")
        s.add(target)
        s.flush()
        target.skills.extend(source.skills)
        target.mcp_servers.extend(source.mcp_servers)
        s.commit()
        click.echo(target.id)


@agent.command("tasks")
@click.argument("agent_id")
def agent_tasks(agent_id):
    with get_session() as s:
        if not s.get(Agent, agent_id):
            raise click.ClickException("Agent not found")
        for run in s.scalars(select(Run).where(Run.agent_id == agent_id).order_by(Run.created_at.desc())).all():
            click.echo(f"{run.id}  {run.created_at.isoformat()}  {run.status.value:<10} {run.issue_id}  {run.prompt}")


@agent.group("env")
def agent_env():
    """Read and update an agent's custom environment."""


@agent_env.command("get")
@click.argument("agent_id")
def agent_env_get(agent_id):
    with get_session() as s:
        item = s.get(Agent, agent_id)
        if not item:
            raise click.ClickException("Agent not found")
        click.echo(item.env_json or "{}")


@agent_env.command("set")
@click.argument("agent_id")
@click.option("--key", required=True)
@click.option("--value", required=True)
def agent_env_set(agent_id, key, value):
    with get_session() as s:
        item = s.get(Agent, agent_id)
        if not item:
            raise click.ClickException("Agent not found")
        values = json.loads(item.env_json or "{}")
        values[key] = value
        item.env_json = json.dumps(values, sort_keys=True)
        s.commit()
        click.echo(item.env_json)


@agent.command("avatar")
@click.argument("agent_id")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
def agent_avatar(agent_id, file):
    with get_session() as s:
        item = s.get(Agent, agent_id)
        if not item:
            raise click.ClickException("Agent not found")
        target_dir = Path(".hagent") / "avatars"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{item.id}-{Path(file).name}"
        shutil.copy2(file, target)
        item.avatar_path = str(target)
        s.commit()
        click.echo(str(target))


@runtime.command("rename")
@click.argument("runtime_id")
@click.option("--name", required=True)
def runtime_rename(runtime_id, name):
    with get_session() as s:
        item = s.get(Runtime, runtime_id)
        if not item:
            raise click.ClickException("Runtime not found")
        item.name = name
        s.commit()
        click.echo(item.id)


@runtime.command("update")
@click.argument("runtime_id")
@click.option("--model", default=None)
@click.option("--config", default=None)
def runtime_update(runtime_id, model, config):
    with get_session() as s:
        item = s.get(Runtime, runtime_id)
        if not item:
            raise click.ClickException("Runtime not found")
        if model is not None:
            item.model = model
        if config is not None:
            json.loads(config)
            item.config_json = config
        s.commit()
        click.echo(item.id)


@runtime.command("delete")
@click.argument("runtime_id")
def runtime_delete(runtime_id):
    with get_session() as s:
        item = s.get(Runtime, runtime_id)
        if not item:
            raise click.ClickException("Runtime not found")
        if item.agents:
            raise click.ClickException("Runtime is still used by an agent")
        s.delete(item)
        s.commit()
        click.echo("deleted")


@runtime.command("usage")
@click.argument("runtime_id")
def runtime_usage(runtime_id):
    with get_session() as s:
        item = s.get(Runtime, runtime_id)
        if not item:
            raise click.ClickException("Runtime not found")
        runs = s.scalars(select(Run).join(Agent).where(Agent.runtime_id == runtime_id)).all()
        click.echo(f"estimated_tokens: {sum(r.token_estimate or 0 for r in runs)}\nruns: {len(runs)}")


@runtime.command("activity")
@click.argument("runtime_id")
def runtime_activity(runtime_id):
    with get_session() as s:
        if not s.get(Runtime, runtime_id):
            raise click.ClickException("Runtime not found")
        runs = s.scalars(select(Run).join(Agent).where(Agent.runtime_id == runtime_id).order_by(Run.created_at.desc())).all()
        for run in runs:
            click.echo(f"{run.created_at}  {run.status.value:<10} {run.id}")


@runtime.group("profile")
def runtime_profile():
    """Manage named local runtime profiles."""


@runtime_profile.command("list")
@click.argument("runtime_id")
def runtime_profile_list(runtime_id):
    with get_session() as s:
        item = s.get(Runtime, runtime_id)
        if not item:
            raise click.ClickException("Runtime not found")
        profiles = json.loads(item.config_json or "{}").get("profiles", {})
        for name in profiles:
            click.echo(name)


@runtime_profile.command("set")
@click.argument("runtime_id")
@click.argument("profile_name")
@click.option("--config", required=True)
def runtime_profile_set(runtime_id, profile_name, config):
    with get_session() as s:
        item = s.get(Runtime, runtime_id)
        if not item:
            raise click.ClickException("Runtime not found")
        profile = json.loads(config)
        values = json.loads(item.config_json or "{}")
        values.setdefault("profiles", {})[profile_name] = profile
        item.config_json = json.dumps(values, sort_keys=True)
        s.commit()
        click.echo(profile_name)


@skill.command("delete")
@click.argument("skill_id")
def skill_delete(skill_id):
    with get_session() as s:
        item = s.get(Skill, skill_id)
        if not item:
            raise click.ClickException("Skill not found")
        s.delete(item)
        s.commit()
        click.echo("deleted")


@skill.command("update")
@click.argument("skill_id")
@click.option("--name", default=None)
@click.option("--description", default=None)
@click.option("--content", default=None)
def skill_update(skill_id, name, description, content):
    with get_session() as s:
        item = s.get(Skill, skill_id)
        if not item:
            raise click.ClickException("Skill not found")
        if name is not None:
            item.name = name
        if description is not None:
            item.description = description
        if content is not None:
            item.content = content
        s.commit()
        click.echo(item.id)


@skill.group("files")
def skill_files():
    """Work with the files in a skill bundle."""


@skill_files.command("list")
@click.argument("skill_id")
def skill_files_list(skill_id):
    with get_session() as s:
        item = s.get(Skill, skill_id)
        if not item:
            raise click.ClickException("Skill not found")
        for file in item.files:
            click.echo(f"{file.id}  {file.filename}")


@skill_files.command("get")
@click.argument("skill_id")
@click.argument("filename")
def skill_files_get(skill_id, filename):
    with get_session() as s:
        item = s.get(Skill, skill_id)
        if not item:
            raise click.ClickException("Skill not found")
        file = next((f for f in item.files if f.filename == filename), None)
        if not file:
            raise click.ClickException("Skill file not found")
        click.echo(file.content)


@squad.command("get")
@click.argument("squad_id")
def squad_get(squad_id):
    with get_session() as s:
        item = s.get(Squad, squad_id)
        if not item:
            raise click.ClickException("Squad not found")
        click.echo(f"id: {item.id}\nname: {item.name}\ndescription: {item.description}\narchived: {item.archived}")
        for member in item.members:
            click.echo(f"member: {member.agent_id}  {member.role}")


@squad.command("update")
@click.argument("squad_id")
@click.option("--name", default=None)
@click.option("--description", default=None)
def squad_update(squad_id, name, description):
    with get_session() as s:
        item = s.get(Squad, squad_id)
        if not item:
            raise click.ClickException("Squad not found")
        if name is not None:
            item.name = name
        if description is not None:
            item.description = description
        s.commit()
        click.echo(item.id)


@squad.command("delete")
@click.argument("squad_id")
def squad_delete(squad_id):
    with get_session() as s:
        item = s.get(Squad, squad_id)
        if not item:
            raise click.ClickException("Squad not found")
        item.archived = True
        s.commit()
        click.echo(f"Archived squad {item.id}")


@autopilot.command("get")
@click.argument("autopilot_id")
def autopilot_get(autopilot_id):
    with get_session() as s:
        item = s.get(Autopilot, autopilot_id)
        if not item:
            raise click.ClickException("Autopilot not found")
        click.echo(f"id: {item.id}\nname: {item.name}\nmode: {item.mode}\nagent_id: {item.agent_id}\nproject_id: {item.project_id}\nenabled: {item.enabled}")
        if item.mode == "create_issue":
            click.echo(f"issue_title_template: {item.issue_title_template or '(autopilot name)'}\ndescription: {item.description}")
        for trigger in item.triggers:
            value = trigger.cron_expression if trigger.type.value == "cron" else "<redacted>"
            zone = f" [{trigger.timezone}]" if trigger.timezone else ""
            click.echo(f"trigger: {trigger.id} {trigger.type.value} {value}{zone}" + (f"  {trigger.label}" if trigger.label else ""))


@autopilot.command("update")
@click.argument("autopilot_id")
@click.option("--name", default=None)
@click.option("--enabled/--disabled", default=None)
@click.option("--filter-status", default=None, type=click.Choice([s.value for s in IssueStatus]))
@click.option("--agent", "agent_ref", default=None, help="Agent id or name.")
@click.option("--project", "project_id", default=None)
@click.option("--mode", type=click.Choice(AUTOPILOT_MODES), default=None)
@click.option("--description", default=None)
@click.option("--issue-title-template", default=None)
def autopilot_update(autopilot_id, name, enabled, filter_status, agent_ref, project_id, mode, description, issue_title_template):
    with get_session() as s:
        item = s.get(Autopilot, autopilot_id)
        if not item:
            raise click.ClickException("Autopilot not found")
        if name is not None:
            item.name = name
        if enabled is not None:
            item.enabled = enabled
        if filter_status is not None:
            item.filter_status = IssueStatus(filter_status)
        if agent_ref is not None:
            item.agent_id = _find_agent(s, agent_ref).id
        if project_id is not None:
            item.project_id = project_id or None
        if mode is not None:
            item.mode = mode
        if description is not None:
            item.description = description
        if issue_title_template is not None:
            item.issue_title_template = issue_title_template
        _check_autopilot_config(s, item.mode, item.project_id, item.description, item.issue_title_template)
        s.commit()
        click.echo(item.id)


@autopilot.command("delete")
@click.argument("autopilot_id")
def autopilot_delete(autopilot_id):
    with get_session() as s:
        item = s.get(Autopilot, autopilot_id)
        if not item:
            raise click.ClickException("Autopilot not found")
        item.enabled = False
        s.commit()
        click.echo(f"Disabled autopilot {item.id}")


@issue.command("update")
@click.argument("issue_id")
@click.option("--title", default=None)
@click.option("--description", default=None)
@click.option("--status", default=None, type=click.Choice([s.value for s in IssueStatus]))
@click.option("--assignee", "assignee_agent_id", default=None)
@click.option("--parent", "parent_issue_id", default=None)
@click.option("--position", default=None, type=int)
@click.option("--priority", type=click.Choice(PRIORITIES), default=None)
@click.option("--start-date", default=None, help="YYYY-MM-DD, or '' to clear.")
@click.option("--due-date", default=None, help="YYYY-MM-DD, or '' to clear.")
@click.option("--stage", type=click.IntRange(min=0), default=None, help="Stage among the parent's sub-issues; 0 clears it.")
@click.option("--no-start", is_flag=True, help="Apply the update without starting the assigned agent.")
def issue_update(issue_id, title, description, status, assignee_agent_id, parent_issue_id, position, priority, start_date, due_date, stage, no_start):
    from hagent.orchestration import apply_status_change, start_agent_run

    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item:
            raise click.ClickException("Issue not found")
        previous_status, reassigned = item.status, False
        if title is not None:
            item.title = title
        if description is not None:
            item.description = description
        if status is not None:
            item.status = IssueStatus(status)
            s.add(TimelineEvent(issue_id=item.id, event_type="status_changed", detail=status))
        if assignee_agent_id is not None:
            agent_row = _find_agent(s, assignee_agent_id) if assignee_agent_id else None
            item.assignee_agent_id = agent_row.id if agent_row else None
            reassigned = agent_row is not None
            s.add(TimelineEvent(issue_id=item.id, event_type="assigned", detail=agent_row.name if agent_row else "unassigned"))
        if parent_issue_id is not None:
            item.parent_issue_id = _issue_id(s, parent_issue_id) if parent_issue_id else None
        if position is not None:
            item.position = position
        if priority is not None:
            item.priority = priority
        if start_date is not None:
            item.start_date = _valid_date(start_date) or None
        if due_date is not None:
            item.due_date = _valid_date(due_date) or None
        if stage is not None:
            if stage and not item.parent_issue_id:
                raise click.ClickException("--stage needs a parent issue")
            item.stage = stage or None
        s.commit()
        click.echo(item.id)
        run = apply_status_change(s, item, previous_status, start=not no_start)
        if run is None and reassigned and not no_start:
            run = start_agent_run(s, item)
        _report_started(run)


@issue.command("runs")
@click.argument("issue_id")
def issue_runs(issue_id):
    with get_session() as s:
        if not s.get(Issue, issue_id):
            raise click.ClickException("Issue not found")
        for run in s.scalars(select(Run).where(Run.issue_id == issue_id).order_by(Run.created_at.desc())).all():
            click.echo(f"{run.id}  {run.status.value:<10} {run.created_at}  {run.agent_id}")


@issue.command("run-messages")
@click.argument("run_id")
def issue_run_messages(run_id):
    with get_session() as s:
        run = s.get(Run, run_id)
        if not run:
            raise click.ClickException("Run not found")
        transcript = json.loads(run.transcript_json or "{}")
        if transcript:
            click.echo(json.dumps(transcript, indent=2))
        if run.output:
            click.echo(run.output)


@project.command("update")
@click.argument("project_id")
@click.option("--name", default=None)
@click.option("--description", default=None)
@click.option("--repo", "repo_id", default=None, help="Repo ID to link for per-issue git worktree isolation. Pass an empty string to unlink.")
def project_update(project_id, name, description, repo_id):
    with get_session() as s:
        item = s.get(Project, project_id)
        if not item:
            raise click.ClickException("Project not found")
        if name is not None:
            item.name = name
        if description is not None:
            item.description = description
        if repo_id is not None:
            if repo_id == "":
                item.repo_id = None
            else:
                if not s.get(Repo, repo_id):
                    raise click.ClickException("Repo not found")
                item.repo_id = repo_id
        s.commit()
        click.echo(item.id)


@project.command("delete")
@click.argument("project_id")
def project_delete(project_id):
    with get_session() as s:
        item = s.get(Project, project_id)
        if not item:
            raise click.ClickException("Project not found")
        item.status = ProjectStatus.COMPLETED
        s.commit()
        click.echo(f"Archived project {item.id}")


@label.command("get")
@click.argument("label_id")
def label_get(label_id):
    with get_session() as s:
        item = s.get(Label, label_id)
        if not item:
            raise click.ClickException("Label not found")
        click.echo(f"id: {item.id}\nname: {item.name}\ncolor: {item.color}")


@label.command("update")
@click.argument("label_id")
@click.option("--name", default=None)
@click.option("--color", default=None)
def label_update(label_id, name, color):
    with get_session() as s:
        item = s.get(Label, label_id)
        if not item:
            raise click.ClickException("Label not found")
        if name is not None:
            item.name = name
        if color is not None:
            item.color = color
        s.commit()
        click.echo(item.id)


@label.command("delete")
@click.argument("label_id")
def label_delete(label_id):
    with get_session() as s:
        item = s.get(Label, label_id)
        if not item:
            raise click.ClickException("Label not found")
        s.delete(item)
        s.commit()
        click.echo("deleted")


@property.command("get")
@click.argument("property_id")
def property_get(property_id):
    with get_session() as s:
        item = s.get(Property, property_id)
        if not item:
            raise click.ClickException("Property not found")
        click.echo(f"id: {item.id}\nname: {item.name}\ntype: {item.type.value}\narchived: {item.archived}\noptions: {item.options_json}")


@property.command("update")
@click.argument("property_id")
@click.option("--name", default=None)
@click.option("--options", default=None)
def property_update(property_id, name, options):
    with get_session() as s:
        item = s.get(Property, property_id)
        if not item:
            raise click.ClickException("Property not found")
        if name is not None:
            item.name = name
        if options is not None:
            json.loads(options)
            item.options_json = options
        s.commit()
        click.echo(item.id)


@property.command("archive")
@click.argument("property_id")
def property_archive(property_id):
    with get_session() as s:
        item = s.get(Property, property_id)
        if not item:
            raise click.ClickException("Property not found")
        item.archived = True
        s.commit()
        click.echo(item.id)


@property.command("unarchive")
@click.argument("property_id")
def property_unarchive(property_id):
    with get_session() as s:
        item = s.get(Property, property_id)
        if not item:
            raise click.ClickException("Property not found")
        item.archived = False
        s.commit()
        click.echo(item.id)


@daemon.command("status")
def daemon_status():
    from hagent.scheduler import get_scheduler
    scheduler = get_scheduler()
    if scheduler is None or not scheduler.running:
        click.echo("stopped")
        return
    click.echo(f"running jobs={len(scheduler.get_jobs())}")


@daemon.command("stop")
def daemon_stop():
    from hagent.scheduler import get_scheduler
    scheduler = get_scheduler()
    if scheduler is None or not scheduler.running:
        click.echo("stopped")
        return
    scheduler.shutdown()
    click.echo("stopped")


@daemon.command("restart")
def daemon_restart():
    from hagent.scheduler import start_scheduler
    from hagent.scheduler import get_scheduler
    scheduler = get_scheduler()
    if scheduler is not None and scheduler.running:
        scheduler.shutdown()
    start_scheduler()
    click.echo("running")


@daemon.command("disk-usage")
def daemon_disk_usage():
    root = Path(".")
    total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)
    click.echo(f"workspace_bytes: {total}")


@daemon.command("logs")
def daemon_logs():
    click.echo("Hagent scheduler logs are emitted by the foreground process.")


def _memory_owner(session):
    profile = session.scalar(select(UserProfile))
    return profile.id if profile else "local-user"


@cli.group()
def memory():
    """Inspect and operate Hagent's canonical provider-neutral memory."""


@memory.command("serve")
@click.option("--workspace-id", default="")
@click.option("--user-id", default="")
@click.option("--project-id", default="")
@click.option("--provider", default="mcp")
def memory_serve(workspace_id, user_id, project_id, provider):
    with get_session() as session:
        workspace = get_active_workspace(session)
        workspace_id = workspace_id or workspace.id
        user_id = user_id or _memory_owner(session)
    os.environ["HAGENT_MEMORY_WORKSPACE_ID"] = workspace_id
    os.environ["HAGENT_MEMORY_USER_ID"] = user_id
    os.environ["HAGENT_MEMORY_PROVIDER"] = provider
    if project_id: os.environ["HAGENT_MEMORY_PROJECT_ID"] = project_id
    from hagent.memory_mcp import main
    main()


@memory.command("search")
@click.argument("query", default="")
@click.option("--project-id", default=None)
@click.option("--category", default=None)
@click.option("--provider", default=None)
@click.option("--limit", default=8, type=int)
def memory_search(query, project_id, category, provider, limit):
    with get_session() as session:
        workspace = get_active_workspace(session)
        service = MemoryService(session, MemoryScope(workspace.id, _memory_owner(session), project_id))
        click.echo(json.dumps(service.search(query, category=category, provider=provider, limit=limit), indent=2))


@memory.command("remember")
@click.argument("content")
@click.option("--project-id", default=None)
@click.option("--category", default="explicit")
@click.option("--origin", default="explicit_user")
@click.option("--verified", is_flag=True)
def memory_remember(content, project_id, category, origin, verified):
    with get_session() as session:
        workspace = get_active_workspace(session)
        service = MemoryService(session, MemoryScope(workspace.id, _memory_owner(session), project_id, provider="cli"))
        result = service.remember(content, category=category, origin_type=origin,
            verification_status="verified" if verified else "unverified", actor="user")
        click.echo(result["id"])


@memory.command("forget")
@click.argument("memory_id")
@click.option("--project-id", default=None)
def memory_forget(memory_id, project_id):
    with get_session() as session:
        workspace = get_active_workspace(session)
        result = MemoryService(session, MemoryScope(workspace.id, _memory_owner(session), project_id)).forget(memory_id)
        click.echo(json.dumps(result))


@memory.command("export")
@click.argument("path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--project-id", default=None)
def memory_export(path, project_id):
    with get_session() as session:
        workspace = get_active_workspace(session)
        payload = MemoryService(session, MemoryScope(workspace.id, _memory_owner(session), project_id)).export()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    click.echo(str(path))


@memory.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--project-id", default=None)
def memory_import(path, project_id):
    payload = json.loads(path.read_text(encoding="utf-8"))
    with get_session() as session:
        workspace = get_active_workspace(session)
        result = MemoryService(session, MemoryScope(workspace.id, _memory_owner(session), project_id)).import_data(payload)
    click.echo(json.dumps(result))


@memory.command("doctor")
def memory_doctor():
    checks = {}
    try:
        from mcp.server.mcpserver import MCPServer
        checks["mcp_sdk"] = "2.x"
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP
            checks["mcp_sdk"] = "1.x"
        except ImportError:
            checks["mcp_sdk"] = "missing"
    with get_session() as session:
        workspace = get_active_workspace(session)
        service = MemoryService(session, MemoryScope(workspace.id, _memory_owner(session)))
        checks["database"] = "ok"
        checks["settings"] = service.settings_dict(service.settings())
        checks["workspace_id"] = workspace.id
        checks["user_id"] = _memory_owner(session)
    click.echo(json.dumps(checks, indent=2))


@cli.group("route")
def route_group():
    """Preview and configure adaptive model routing."""


@route_group.command("preview")
@click.argument("prompt")
@click.option("--project-id", default=None)
@click.option("--runtime-id", default=None)
@click.option("--mode", type=click.Choice(["auto", "provider_fixed", "model_fixed", "manual"]), default=None)
@click.option("--model", default=None)
@click.option("--effort", type=click.Choice(["low", "medium", "high", "xhigh", "max"]), default=None)
def route_preview(prompt, project_id, runtime_id, mode, model, effort):
    with get_session() as session:
        workspace = get_active_workspace(session)
        runtime = session.get(Runtime, runtime_id) if runtime_id else None
        override = {k: v for k, v in {"runtime_id": runtime_id, "mode": mode,
                    "model": model, "effort": effort}.items() if v is not None}
        result = ModelRouter(session, workspace.id, project_id).route(
            prompt, default_runtime=runtime, override=override, persist=False)
        click.echo(json.dumps(result, indent=2))


@route_group.command("config")
@click.option("--project-id", default=None)
@click.option("--mode", type=click.Choice(["auto", "provider_fixed", "model_fixed", "manual"]), required=True)
@click.option("--provider", default="")
@click.option("--model", default="")
@click.option("--effort", default="")
@click.option("--max-effort", default="high")
@click.option("--max-cost-usd", type=float, default=None)
@click.option("--max-latency-ms", type=int, default=None)
def route_config(project_id, mode, provider, model, effort, max_effort, max_cost_usd, max_latency_ms):
    with get_session() as session:
        workspace = get_active_workspace(session)
        result = ModelRouter(session, workspace.id, project_id).set_policy(
            mode=mode, provider=provider, model=model, effort=effort,
            max_effort=max_effort, max_cost_usd=max_cost_usd, max_latency_ms=max_latency_ms)
        click.echo(json.dumps(result, indent=2))


@route_group.command("decisions")
@click.option("--project-id", default=None)
def route_decisions(project_id):
    with get_session() as session:
        query = select(RoutingDecision).order_by(RoutingDecision.created_at.desc()).limit(50)
        if project_id: query = query.where(RoutingDecision.project_id == project_id)
        click.echo(json.dumps([ModelRouter.serialize(row) for row in session.scalars(query)], indent=2))


@route_group.command("launch")
@click.argument("client", type=click.Choice(["codex", "claude"]))
@click.option("--runtime-id", required=True)
@click.option("--project-id", default=None)
@click.option("--prompt", default=None)
@click.option("--working-directory", type=click.Path(file_okay=False, path_type=Path), default=Path("."))
def route_launch(client, runtime_id, project_id, prompt, working_directory):
    """Launch a new supported CLI session with a routed model and Hagent MCP memory."""
    from hagent.client_wrappers import build_claude_args, build_codex_args, claude_mcp_config
    with get_session() as session:
        workspace = get_active_workspace(session)
        runtime = session.get(Runtime, runtime_id)
        if not runtime:
            raise click.ClickException("Runtime not found")
        route = ModelRouter(session, workspace.id, project_id).route(
            prompt or "Interactive coding session", default_runtime=runtime,
            override={"mode": "manual", "runtime_id": runtime.id})
        scope = {"HAGENT_MEMORY_WORKSPACE_ID": workspace.id,
                 "HAGENT_MEMORY_USER_ID": _memory_owner(session),
                 "HAGENT_MEMORY_PROJECT_ID": project_id or "",
                 "HAGENT_MEMORY_PROVIDER": client}
    config = json.loads(runtime.config_json or "{}")
    command = config.get("command", client)
    started_at = time.monotonic()
    exit_code = None
    try:
        if client == "codex":
            args = build_codex_args(command, route, scope, prompt)
            exit_code = subprocess.call(args, cwd=working_directory)
        else:
            handle = tempfile.NamedTemporaryFile(
                "w", suffix=".json", encoding="utf-8", delete=False
            )
            try:
                json.dump(claude_mcp_config(scope), handle)
                handle.close()
                args = build_claude_args(command, route, handle.name, prompt)
                exit_code = subprocess.call(args, cwd=working_directory)
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(handle.name)
    finally:
        with get_session() as session:
            workspace = get_active_workspace(session)
            ModelRouter(session, workspace.id, project_id).finalize(
                route["id"],
                latency_ms=int((time.monotonic() - started_at) * 1000),
                outcome="completed" if exit_code == 0 else "failed",
            )
    raise SystemExit(exit_code)


# --- project resources ---

_GITHUB_REPO_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+?(?:\.git)?/?$")


def _validated_resource(resource_type, ref):
    ref = ref.strip()
    if not ref:
        raise click.ClickException("Resource URL cannot be empty")
    if resource_type == "github_repo" and not _GITHUB_REPO_URL.match(ref):
        raise click.ClickException("A github_repo resource needs a URL like https://github.com/owner/repo")
    return ref


def _project_or_fail(s, project_id):
    project_row = s.get(Project, project_id)
    if not project_row:
        raise click.ClickException(f"Project {project_id} not found")
    return project_row


def _resource_or_fail(s, project_id, resource_id):
    _project_or_fail(s, project_id)
    resource = s.get(ProjectResource, resource_id)
    if not resource or resource.project_id != project_id:
        raise click.ClickException(f"Resource {resource_id} not found on project {project_id}")
    return resource


@project.group("resource")
def project_resource():
    """Manage resources (e.g. GitHub repos) attached to a project."""


@project_resource.command("add")
@click.argument("project_id")
@click.option("--type", "resource_type", default="github_repo", show_default=True)
@click.option("--url", required=True)
@click.option("--label", default="")
def project_resource_add(project_id, resource_type, url, label):
    url = _validated_resource(resource_type, url)
    with get_session() as s:
        project_row = _project_or_fail(s, project_id)
        if any(r.type == resource_type and r.ref == url for r in project_row.resources):
            raise click.ClickException("That resource is already attached to this project")
        position = max((r.position for r in project_row.resources), default=-1) + 1
        resource = ProjectResource(project_id=project_id, type=resource_type, ref=url, label=label, position=position)
        s.add(resource)
        s.commit()
        click.echo(resource.id)


@project_resource.command("list")
@click.argument("project_id")
def project_resource_list(project_id):
    with get_session() as s:
        for r in _project_or_fail(s, project_id).resources:
            click.echo(f"{r.id}  {r.type:<12} {r.ref}" + (f"  ({r.label})" if r.label else ""))


@project_resource.command("remove")
@click.argument("project_id")
@click.argument("resource_id")
def project_resource_remove(project_id, resource_id):
    with get_session() as s:
        s.delete(_resource_or_fail(s, project_id, resource_id))
        s.commit()
        click.echo(f"Removed resource {resource_id}")


@project_resource.command("update")
@click.argument("project_id")
@click.argument("resource_id")
@click.option("--url", default=None)
@click.option("--label", default=None)
@click.option("--position", type=int, default=None)
def project_resource_update(project_id, resource_id, url, label, position):
    if url is None and label is None and position is None:
        raise click.ClickException("Nothing to update: pass --url, --label or --position")
    with get_session() as s:
        resource = _resource_or_fail(s, project_id, resource_id)
        if url is not None:
            resource.ref = _validated_resource(resource.type, url)
        if label is not None:
            resource.label = label
        if position is not None:
            resource.position = position
        s.commit()
        click.echo(f"Updated resource {resource_id}")


# --- skill labels ---

@skill.group("label")
def skill_label():
    """Manage labels on a skill."""


def _skill_and_label(s, skill_id, label_id):
    sk = s.get(Skill, skill_id)
    lb = s.get(Label, label_id)
    if not sk or not lb:
        raise click.ClickException("Skill or label not found")
    return sk, lb


@skill_label.command("add")
@click.argument("skill_id")
@click.option("--label", "label_id", required=True)
def skill_label_add(skill_id, label_id):
    with get_session() as s:
        sk, lb = _skill_and_label(s, skill_id, label_id)
        if lb not in sk.labels:
            sk.labels.append(lb)
            s.commit()
        click.echo(f"Added label {lb.name} to skill {sk.id}")


@skill_label.command("list")
@click.argument("skill_id")
def skill_label_list(skill_id):
    with get_session() as s:
        sk = s.get(Skill, skill_id)
        if not sk:
            raise click.ClickException(f"Skill {skill_id} not found")
        for lb in sk.labels:
            click.echo(f"{lb.id}  {lb.name}")


@skill_label.command("remove")
@click.argument("skill_id")
@click.option("--label", "label_id", required=True)
def skill_label_remove(skill_id, label_id):
    with get_session() as s:
        sk, lb = _skill_and_label(s, skill_id, label_id)
        if lb in sk.labels:
            sk.labels.remove(lb)
            s.commit()
        click.echo(f"Removed label {lb.name} from skill {sk.id}")


# --- issue pull requests ---

def _record_pull_request(s, issue_id, url, title="", number=None, state="open"):
    """Insert or refresh the PR row for (issue, url)."""
    if number is None:
        match = re.search(r"/pull/(\d+)", url)
        number = int(match.group(1)) if match else None
    existing = s.scalar(select(IssuePullRequest).where(IssuePullRequest.issue_id == issue_id, IssuePullRequest.url == url))
    if existing:
        existing.state = state
        existing.title = title or existing.title
        existing.number = number if number is not None else existing.number
        return existing
    row = IssuePullRequest(issue_id=issue_id, url=url, number=number, title=title, state=state)
    s.add(row)
    return row


def _discover_pull_requests(s, item):
    """Ask GitHub (via gh) for PRs opened from this issue's branch; returns how many were found."""
    from hagent.worktrees import branch_name_for_issue, worktree_path_for_issue

    project_row = s.get(Project, item.project_id)
    repo = s.get(Repo, project_row.repo_id) if project_row and project_row.repo_id else None
    if not repo or not repo.local_path:
        raise click.ClickException("This issue's project isn't linked to a repo with a local checkout")
    gh = shutil.which("gh")
    if not gh:
        raise click.ClickException("The 'gh' CLI is required for --refresh")
    worktree = worktree_path_for_issue(repo.local_path, item.id)
    cwd = worktree if Path(worktree).is_dir() else repo.local_path
    listing = subprocess.run(
        [gh, "pr", "list", "--head", branch_name_for_issue(item.id), "--state", "all", "--json", "number,url,title,state"],
        cwd=cwd, capture_output=True, text=True,
    )
    if listing.returncode != 0:
        raise click.ClickException(f"gh pr list failed: {listing.stderr.strip()}")
    found = json.loads(listing.stdout or "[]")
    for pr_info in found:
        _record_pull_request(s, item.id, pr_info["url"], pr_info.get("title", ""), pr_info.get("number"), str(pr_info.get("state", "open")).lower())
    s.commit()
    return len(found)


@issue.command("pull-requests")
@click.argument("issue_id")
@click.option("--refresh", is_flag=True, help="Look up PRs opened from this issue's branch with the gh CLI first.")
def issue_pull_requests(issue_id, refresh):
    """List pull requests linked to an issue."""
    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item:
            raise click.ClickException("Issue not found")
        if refresh:
            _discover_pull_requests(s, item)
        for pr_row in item.pull_requests:
            number = f"#{pr_row.number}" if pr_row.number is not None else "-"
            click.echo(f"{number:<6} {pr_row.state:<7} {pr_row.url}" + (f"  {pr_row.title}" if pr_row.title else ""))



# --- accounts, tokens and serving to other devices ---

@cli.group("auth")
def auth_group():
    """Manage accounts and API tokens (run on the machine that hosts Hagent)."""


def _auth_call(fn, *args, **kwargs):
    from hagent.auth import AuthError

    try:
        return fn(*args, **kwargs)
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc


def _read_password(password_stdin, confirm=True):
    if password_stdin:
        return click.get_text_stream("stdin").readline().rstrip("\r\n")
    return click.prompt("Password", hide_input=True, confirmation_prompt=confirm)


@auth_group.command("user-create")
@click.argument("username")
@click.option("--role", type=click.Choice(["owner", "member"]), default="member", show_default=True, help="The first account is always the owner.")
@click.option("--password-stdin", is_flag=True, help="Read the password from stdin instead of prompting.")
def auth_user_create(username, role, password_stdin):
    from hagent import auth as auth_mod

    password = _read_password(password_stdin)
    with get_session(scoped=False) as s:
        was_empty = not auth_mod.auth_enabled()
        user = _auth_call(auth_mod.create_user, s, username, password, role)
        click.echo(f"Created {user.role} '{user.username}'")
    if was_empty:
        click.echo("Sign-in is now required to use the dashboard and API, including on this machine.")


@auth_group.command("user-list")
def auth_user_list():
    from hagent.models import User

    with get_session(scoped=False) as s:
        for user in s.scalars(select(User).order_by(User.created_at)):
            click.echo(f"{user.username:<24} {user.role}")


@auth_group.command("user-remove")
@click.argument("username")
def auth_user_remove(username):
    from hagent import auth as auth_mod

    with get_session(scoped=False) as s:
        _auth_call(auth_mod.remove_user, s, username)
    click.echo(f"Removed '{username}' and revoked all of their tokens")


@auth_group.command("passwd")
@click.argument("username")
@click.option("--password-stdin", is_flag=True)
def auth_passwd(username, password_stdin):
    from hagent import auth as auth_mod

    password = _read_password(password_stdin)
    with get_session(scoped=False) as s:
        _auth_call(auth_mod.set_password, s, username, password)
    click.echo("Password changed; browser sessions were signed out (API tokens still work).")


@auth_group.command("token-create")
@click.option("--user", "username", required=True)
@click.option("--name", default="", help="A label to recognise this token by, e.g. 'laptop'.")
@click.option("--kind", type=click.Choice(["api", "worker"]), default="api", show_default=True, help="'worker' tokens can only be used by `hagent worker`.")
@click.option("--days", type=int, default=None, help="Expire after this many days (default: never).")
def auth_token_create(username, name, kind, days):
    from hagent import auth as auth_mod
    from hagent.models import User
    from sqlalchemy import func

    with get_session(scoped=False) as s:
        user = s.scalar(select(User).where(func.lower(User.username) == username.lower()))
        if not user:
            raise click.ClickException(f"User '{username}' not found")
        secret, row = _auth_call(auth_mod.issue_token, s, user, kind, name, days)
        click.echo(f"Created {kind} token {row.id[:8]} for '{user.username}'. It is shown only once:", err=True)
        click.echo(secret)


@auth_group.command("token-list")
@click.option("--user", "username", default=None)
def auth_token_list(username):
    from hagent.models import AuthToken, User
    from sqlalchemy import func

    with get_session(scoped=False) as s:
        query = select(AuthToken, User).join(User, User.id == AuthToken.user_id).where(AuthToken.kind != "session").order_by(AuthToken.created_at)
        if username:
            query = query.where(func.lower(User.username) == username.lower())
        for token, user in s.execute(query):
            state = "revoked" if token.revoked_at else "active"
            click.echo(f"{token.id[:8]}  {token.kind:<7} {user.username:<16} {token.prefix}...  {state:<8} {token.name}")


@auth_group.command("token-revoke")
@click.argument("token_id")
def auth_token_revoke(token_id):
    from hagent import auth as auth_mod

    with get_session(scoped=False) as s:
        _auth_call(auth_mod.revoke_token, s, token_id)
    click.echo("Token revoked (servers notice within about 15 seconds).")


@auth_group.command("status")
def auth_status():
    from hagent import auth as auth_mod
    from hagent.models import User
    from sqlalchemy import func

    with get_session(scoped=False) as s:
        count = s.scalar(select(func.count()).select_from(User))
    click.echo(f"Accounts: {count}. Sign-in required: {'yes' if auth_mod.auth_enabled() else 'no (loopback-only until an account exists)'}")


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Use 0.0.0.0 (or a Tailscale/LAN address) to reach Hagent from other devices.")
@click.option("--port", type=int, default=8000, show_default=True)
@click.option("--ssl-certfile", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--ssl-keyfile", type=click.Path(exists=True, dir_okay=False), default=None)
def serve(host, port, ssl_certfile, ssl_keyfile):
    """Run the dashboard and API. Refuses to listen beyond this machine until an account exists."""
    from hagent import auth as auth_mod

    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise click.ClickException("--ssl-certfile and --ssl-keyfile must be given together")
    if not auth_mod.is_loopback_host(host):
        if not auth_mod.auth_enabled():
            raise click.ClickException(
                "Refusing to listen beyond this machine without accounts: anyone who could reach it would get "
                "the dashboard and agent terminals. Run `hagent auth user-create <name>` first."
            )
        if not ssl_certfile:
            click.echo(
                "Warning: serving plain HTTP. Passwords and tokens are readable on the network; use a private "
                "network (e.g. Tailscale), a TLS-terminating proxy, or --ssl-certfile/--ssl-keyfile.",
                err=True,
            )
    import uvicorn

    uvicorn.run("hagent.web:app", host=host, port=port, ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile)



# --- worker devices ---

@cli.group("worker")
def worker_group():
    """Run model calls for a Hagent server on this device, or list workers (on the server)."""


@worker_group.command("start")
@click.option("--name", required=True, help="This device's worker name; must match the name its worker token was created with.")
@click.option("--url", default=None, help="Server URL. Defaults to HAGENT_REMOTE_URL or the `remote login` server.")
@click.option("--token", default=None, help="Worker token. Defaults to HAGENT_WORKER_TOKEN, else you are prompted.")
@click.option("--workdir", type=click.Path(file_okay=False), default=None, help="Directory jobs run in (default: the current directory).")
@click.option("--allow-terminal", is_flag=True, help="Honour the server's request to let agents run commands here. Off by default.")
@click.option("--type", "types", multiple=True, help="Runtime types to offer (default: every supported assistant).")
@click.option("--allow-insecure", is_flag=True, help="Allow plain http to a non-local server (e.g. over Tailscale).")
def worker_start(name, url, token, workdir, allow_terminal, types, allow_insecure):
    import os
    import urllib.parse

    from hagent import remote, worker as worker_mod

    url = (url or os.environ.get("HAGENT_REMOTE_URL") or (remote.load_config() or {}).get("url") or "").rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise click.ClickException("Give the server with --url https://host:8000")
    if parsed.scheme == "http" and parsed.hostname not in remote._LOOPBACK and not allow_insecure:
        raise click.ClickException("Refusing to send a token over plain http; use https or pass --allow-insecure on a network you trust")
    unknown = [t for t in types if t not in worker_mod.ALLOWED_TYPES]
    if unknown:
        raise click.ClickException(f"Unsupported worker type(s): {', '.join(unknown)}. Allowed: {', '.join(worker_mod.ALLOWED_TYPES)}")
    token = token or os.environ.get("HAGENT_WORKER_TOKEN") or click.prompt("Worker token", hide_input=True)
    try:
        remote._request({"url": url, "token": token}, "GET", "/api/worker/ping")
    except remote.RemoteError as exc:
        raise click.ClickException(str(exc)) from exc
    if allow_terminal:
        click.echo("Terminal access ON: agents may run commands on this device.", err=True)
    try:
        worker_mod.run_worker({"url": url, "token": token}, name, workdir, allow_terminal, list(types) or None, log=click.echo)
    except KeyboardInterrupt:
        click.echo("Worker stopped.")


@worker_group.command("list")
def worker_list():
    """List devices that have connected to this server, and when each was last seen."""
    from hagent.models import Worker, WorkerJob
    from hagent.worker_api import is_online
    from sqlalchemy import func

    with get_session(scoped=False) as s:
        for w in s.scalars(select(Worker).order_by(Worker.name)):
            pending = s.scalar(select(func.count()).select_from(WorkerJob).where(WorkerJob.worker_name == w.name, WorkerJob.status == "pending"))
            seen = w.last_seen_at.strftime("%Y-%m-%d %H:%M:%S") if w.last_seen_at else "never"
            click.echo(f"{w.name:<20} {'online ' if is_online(s, w.name) else 'offline'} last seen {seen}  pending jobs: {pending}  offers: {w.capabilities}")



# --- edit/remove operations added for full lifecycle control ---

@label_cmd.command("list")
@click.argument("issue_id")
def issue_label_list(issue_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException("Issue not found")
        for lb in i.labels:
            click.echo(f"{lb.id}  {lb.name}")


@label_cmd.command("remove")
@click.argument("issue_id")
@click.option("--label", "label_id", required=True)
def issue_label_remove(issue_id, label_id):
    with get_session() as s:
        i, lb = s.get(Issue, issue_id), s.get(Label, label_id)
        if not i or not lb:
            raise click.ClickException("Issue or label not found")
        if lb in i.labels:
            i.labels.remove(lb)
            s.commit()
        click.echo(f"Removed label {lb.name} from issue {i.id}")


@property_cmd.command("list")
@click.argument("issue_id")
def issue_property_list(issue_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException("Issue not found")
        for pv in i.property_values:
            click.echo(f"{pv.property.name}={pv.value}  ({pv.property_id})")


@property_cmd.command("unset")
@click.argument("issue_id")
@click.option("--property", "property_id", required=True)
def issue_property_unset(issue_id, property_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException("Issue not found")
        found = next((pv for pv in i.property_values if pv.property_id == property_id), None)
        if not found:
            raise click.ClickException("That property has no value on this issue")
        s.delete(found); s.commit(); click.echo("Unset")


def _squad_member_or_fail(s, squad_id, agent_ref):
    sq = s.get(Squad, squad_id)
    if not sq:
        raise click.ClickException("Squad not found")
    agent_row = _find_agent(s, agent_ref)
    membership = next((m for m in sq.members if m.agent_id == agent_row.id), None)
    if not membership:
        raise click.ClickException(f"{agent_row.name} is not in squad {sq.name}")
    return sq, agent_row, membership


@member.command("list")
@click.argument("squad_id")
def squad_member_list(squad_id):
    with get_session() as s:
        sq = s.get(Squad, squad_id)
        if not sq:
            raise click.ClickException("Squad not found")
        for m in sq.members:
            click.echo(f"{m.agent_id}  {m.agent.name:<20} {m.role}")


@member.command("remove")
@click.argument("squad_id")
@click.option("--agent", "agent_ref", required=True, help="Agent id or name.")
def squad_member_remove(squad_id, agent_ref):
    with get_session() as s:
        sq, agent_row, membership = _squad_member_or_fail(s, squad_id, agent_ref)
        s.delete(membership); s.commit()
        click.echo(f"Removed {agent_row.name} from squad {sq.name}")


@member.command("set-role")
@click.argument("squad_id")
@click.option("--agent", "agent_ref", required=True, help="Agent id or name.")
@click.option("--role", required=True)
def squad_member_set_role(squad_id, agent_ref, role):
    with get_session() as s:
        sq, agent_row, membership = _squad_member_or_fail(s, squad_id, agent_ref)
        membership.role = role; s.commit()
        click.echo(f"{agent_row.name} is now {role} in squad {sq.name}")


@skill_files.command("upsert")
@click.argument("skill_id")
@click.argument("filename")
@click.option("--content", default=None, help="File content. Use --content-file for anything long.")
@click.option("--content-file", type=click.Path(exists=True, dir_okay=False), default=None)
def skill_files_upsert(skill_id, filename, content, content_file):
    if (content is None) == (content_file is None):
        raise click.ClickException("Give exactly one of --content or --content-file")
    if ".." in filename.replace("\\", "/").split("/") or filename.startswith(("/", "\\")):
        raise click.ClickException("Skill filenames must be relative and cannot contain '..'")
    text_value = content if content is not None else Path(content_file).read_text(encoding="utf-8")
    with get_session() as s:
        item = s.get(Skill, skill_id)
        if not item:
            raise click.ClickException("Skill not found")
        file = next((f for f in item.files if f.filename == filename), None)
        if file:
            file.content = text_value
        else:
            item.files.append(SkillFile(filename=filename, content=text_value))
        s.commit()
        click.echo(f"{'Updated' if file else 'Added'} {filename}")


@skill_files.command("delete")
@click.argument("skill_id")
@click.argument("filename")
def skill_files_delete(skill_id, filename):
    with get_session() as s:
        item = s.get(Skill, skill_id)
        if not item:
            raise click.ClickException("Skill not found")
        file = next((f for f in item.files if f.filename == filename), None)
        if not file:
            raise click.ClickException("Skill file not found")
        item.files.remove(file); s.commit()
        click.echo(f"Deleted {filename}")


@skills.command("list")
@click.argument("agent_id")
def agent_skills_list(agent_id):
    with get_session() as s:
        a = s.get(Agent, agent_id)
        if not a:
            raise click.ClickException("Agent not found")
        for sk in a.skills:
            click.echo(f"{sk.id}  {sk.name}")


@skills.command("set")
@click.argument("agent_id")
@click.option("--skill", "skill_ids", multiple=True, help="Repeat for each skill. With none, removes all skills.")
def agent_skills_set(agent_id, skill_ids):
    """Replace the agent's skills with exactly this set."""
    with get_session() as s:
        a = s.get(Agent, agent_id)
        if not a:
            raise click.ClickException("Agent not found")
        chosen = []
        for sid in dict.fromkeys(skill_ids):
            sk = s.get(Skill, sid)
            if not sk:
                raise click.ClickException(f"Skill {sid} not found")
            chosen.append(sk)
        a.skills = chosen
        s.commit()
        click.echo(f"{a.name} now has {len(chosen)} skill(s)")


@agent_mcp.command("list")
@click.argument("agent_id")
def agent_mcp_list(agent_id):
    with get_session() as s:
        a = s.get(Agent, agent_id)
        if not a:
            raise click.ClickException("Agent not found")
        for server in a.mcp_servers:
            click.echo(f"{server.id}  {server.transport}  {server.name}")


@workspace_mcp.command("update")
@click.argument("server_id")
@click.option("--name", default=None)
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default=None)
@click.option("--command", default=None)
@click.option("--args", "args_json", default=None)
@click.option("--url", default=None)
@click.option("--config", default=None)
def workspace_mcp_update(server_id, name, transport, command, args_json, url, config):
    if all(v is None for v in (name, transport, command, args_json, url, config)):
        raise click.ClickException("Nothing to update")
    with get_session() as s:
        item = s.get(McpServer, server_id)
        if not item:
            raise click.ClickException("MCP server not found")
        for field, value in (("name", name), ("transport", transport), ("command", command), ("args_json", args_json), ("url", url), ("config_json", config)):
            if value is not None:
                setattr(item, field, value)
        if item.transport == "stdio" and not item.command:
            raise click.UsageError("stdio requires --command")
        if item.transport == "sse" and not item.url:
            raise click.UsageError("sse requires --url")
        s.commit()
        click.echo(f"Updated {item.name}")


@cli.command("version")
def version_cmd():
    """Print the Hagent version."""
    from hagent import __version__

    click.echo(f"hagent {__version__}")



# --- queue visibility ---

def _duration(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds / 3600:.1f}h"


@cli.command("queue")
def queue_cmd():
    """Show what is running, what is queued, and exactly why each queued run is waiting."""
    from hagent.dispatcher import explain_queue

    with get_session() as s:
        snap = explain_queue(s)
    age = snap["heartbeat_age"]
    if age is None or age > 30:
        seen = "never" if age is None else f"{_duration(age)} ago"
        click.echo(f"dispatcher: NOT RUNNING (last seen {seen}) - queued runs will not start until you run `hagent serve` or `hagent daemon start`")
    else:
        click.echo(f"dispatcher: running (last seen {_duration(age)} ago)")
    click.echo(f"running ({len(snap['running'])}, at most {snap['global_cap']} at once):")
    for r in snap["running"]:
        note = "  <- its process is gone; it will be resumed on the next recovery pass" if r["orphaned"] else ""
        click.echo(f"  {r['key']:<9} {r['agent']:<18} {_duration(r['seconds']):>6}  {r['title']}{note}")
    click.echo(f"queued ({len(snap['queued'])}):")
    for q in snap["queued"]:
        aged = f" (aged from {q['priority']})" if q["effective_rank"] < PRIORITY_RANK_FOR_QUEUE.get(q["priority"], 4) else ""
        click.echo(f"  {q['position']:>2}. {q['key']:<9} {q['priority']:<7} {q['agent']:<18} waiting {_duration(q['waited']):>5}{aged} -> {q['reason']}")
    for a in snap["approvals"]:
        click.echo(f"  awaiting approval: {a['key']} ({a['agent']})")


PRIORITY_RANK_FOR_QUEUE = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}


if __name__ == "__main__":
    cli()
