"""click-based CLI mirroring Multica's noun/verb structure: hagent <noun> <verb> ..."""

import json
import os
import time
import secrets
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from datetime import datetime, timezone

import click
from sqlalchemy import select

from hagent.db import get_active_workspace, get_or_create_default_workspace, get_session, init_db, set_active_workspace
from hagent.engine import run_issue as engine_run_issue
from hagent.models import (
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
    ChatMessage,
    ChatThread,
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
)

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
def project_create(name, description):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        p = Project(workspace_id=ws.id, name=name, description=description)
        s.add(p)
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


@issue.command("create")
@click.option("--project", "project_id", required=True)
@click.option("--title", required=True)
@click.option("--description", default="")
@click.option("--assignee", "assignee_agent_id", default=None)
@click.option("--parent", "parent_issue_id", default=None)
@click.option("--status", default=IssueStatus.BACKLOG.value, type=click.Choice([s.value for s in IssueStatus]))
def issue_create(project_id, title, description, assignee_agent_id, parent_issue_id, status):
    with get_session() as s:
        p = s.get(Project, project_id)
        if not p:
            raise click.ClickException(f"Project {project_id} not found")
        i = Issue(
            project_id=p.id,
            title=title,
            description=description,
            assignee_agent_id=assignee_agent_id,
            parent_issue_id=parent_issue_id,
            status=IssueStatus(status),
        )
        s.add(i)
        s.commit()
        click.echo(f"Created issue {i.id}")


@issue.command("list")
@click.option("--project", "project_id", default=None)
def issue_list(project_id):
    with get_session() as s:
        stmt = select(Issue)
        if project_id:
            stmt = stmt.where(Issue.project_id == project_id)
        for i in s.scalars(stmt).all():
            click.echo(f"{i.id}  {i.status.value:<12} {i.title}")


@issue.command("get")
@click.argument("issue_id")
def issue_get(issue_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        labels = ", ".join(l.name for l in i.labels) or "(none)"
        click.echo(
            f"id: {i.id}\ntitle: {i.title}\nstatus: {i.status.value}\nproject_id: {i.project_id}\n"
            f"assignee_agent_id: {i.assignee_agent_id}\nlabels: {labels}\ndescription: {i.description}\n"
            f"runs: {len(i.runs)}\ncomments: {len(i.comments)}"
        )


@issue.command("status")
@click.argument("issue_id")
@click.argument("status", type=click.Choice([s.value for s in IssueStatus]))
def issue_status(issue_id, status):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        i.status = IssueStatus(status)
        s.add(TimelineEvent(issue_id=i.id, event_type="status_changed", detail=status))
        s.commit()
        click.echo(f"Issue {i.id} -> {status}")


@issue.command("assign")
@click.argument("issue_id")
@click.option("--agent", "agent_id", required=True)
def issue_assign(issue_id, agent_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        a = s.get(Agent, agent_id)
        if not i or not a:
            raise click.ClickException("Issue or agent not found")
        i.assignee_agent_id = a.id
        s.add(TimelineEvent(issue_id=i.id, event_type="assigned", detail=a.name))
        s.commit()
        click.echo(f"Issue {i.id} assigned to {a.name}")


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


@comment.command("add")
@click.argument("issue_id")
@click.option("--body", required=True)
def issue_comment_add(issue_id, body):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        c = Comment(issue_id=i.id, body=body)
        s.add(c)
        s.commit()
        click.echo(f"Added comment {c.id}")


@comment.command("list")
@click.argument("issue_id")
def issue_comment_list(issue_id):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if not i:
            raise click.ClickException(f"Issue {issue_id} not found")
        for c in i.comments:
            click.echo(f"[{c.created_at}] {c.author}: {c.body}")


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


@autopilot.command("create")
@click.option("--name", required=True)
@click.option("--agent", "agent_id", required=True)
@click.option("--project", "project_id", default=None)
@click.option("--filter-status", default=None, type=click.Choice([s.value for s in IssueStatus]))
def autopilot_create(name, agent_id, project_id, filter_status):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        a = s.get(Agent, agent_id)
        if not a:
            raise click.ClickException(f"Agent {agent_id} not found")
        ap = Autopilot(
            workspace_id=ws.id,
            name=name,
            agent_id=a.id,
            project_id=project_id,
            filter_status=IssueStatus(filter_status) if filter_status else None,
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
def autopilot_trigger_add(autopilot_id, cron, webhook):
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
        s.add(t)
        s.commit()
        click.echo(f"Added trigger {t.id} ({t.webhook_token or cron}) to autopilot {ap.name}")


@autopilot.command("trigger")
@click.argument("autopilot_id")
def autopilot_trigger_now(autopilot_id):
    """Manually trigger an autopilot to run once."""
    from hagent.scheduler import run_autopilot_once

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
@click.option("--url", required=True)
def repo_add(name, url):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        r = Repo(workspace_id=ws.id, name=name, url=url)
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
        for child in s.scalars(select(Issue).where(Issue.parent_issue_id == issue_id).order_by(Issue.position, Issue.created_at)).all():
            click.echo(f"{child.status.value}: {child.id} {child.title}")


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
        runs = s.scalars(select(Run).where(Run.issue_id == issue_id, Run.status == RunStatus.RUNNING)).all()
        for run in runs:
            run.status, run.error, run.finished_at = RunStatus.FAILED, "cancelled by user", datetime.now(timezone.utc)
        s.commit()
        click.echo(f"Cancelled {len(runs)} running task(s)")


@issue.command("timeline")
@click.argument("issue_id")
def issue_timeline(issue_id):
    with get_session() as s:
        for event in s.scalars(select(TimelineEvent).where(TimelineEvent.issue_id == issue_id).order_by(TimelineEvent.created_at)).all():
            click.echo(f"[{event.created_at}] {event.event_type}: {event.detail}")


@issue.group("subscriber")
def issue_subscriber():
    pass


@issue_subscriber.command("add")
@click.argument("issue_id")
@click.option("--name", required=True)
def issue_subscriber_add(issue_id, name):
    with get_session() as s:
        item = IssueSubscriber(issue_id=issue_id, name=name)
        s.add(item); s.commit(); click.echo(item.id)


@issue_subscriber.command("list")
@click.argument("issue_id")
def issue_subscriber_list(issue_id):
    with get_session() as s:
        for item in s.scalars(select(IssueSubscriber).where(IssueSubscriber.issue_id == issue_id)).all():
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
        item = s.scalar(select(IssueMetadata).where(IssueMetadata.issue_id == issue_id, IssueMetadata.key == key))
        if not item:
            item = IssueMetadata(issue_id=issue_id, key=key); s.add(item)
        item.value = value; s.commit(); click.echo(f"{key}={value}")


@issue_metadata.command("get")
@click.argument("issue_id")
def issue_metadata_get(issue_id):
    with get_session() as s:
        for item in s.scalars(select(IssueMetadata).where(IssueMetadata.issue_id == issue_id)).all():
            click.echo(f"{item.key}={item.value}")


@issue.command("reorder")
@click.argument("issue_id")
@click.option("--position", required=True, type=int)
def issue_reorder(issue_id, position):
    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item: raise click.ClickException("Issue not found")
        item.position = position; s.commit(); click.echo(f"{item.id}: position={position}")


def _read_skill_source(source):
    cleanup = None
    if source.startswith("http://") or source.startswith("https://"):
        fd, archive = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        Path(archive).unlink(missing_ok=True)
        urllib.request.urlretrieve(source, archive)
        root = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(archive) as zf: zf.extractall(root)
        Path(archive).unlink(missing_ok=True); cleanup = root
    else:
        root = Path(source)
        if root.is_file() and root.suffix.lower() == ".zip":
            target = Path(tempfile.mkdtemp())
            with zipfile.ZipFile(root) as zf: zf.extractall(target)
            root, cleanup = target, target
    if not root.exists(): raise click.ClickException(f"Skill source not found: {source}")
    files = [(str(p.relative_to(root)), p.read_text(encoding="utf-8")) for p in root.rglob("*") if p.is_file()]
    return files, cleanup


@skill.command("import")
@click.argument("source")
@click.option("--name", default=None)
@click.option("--description", default="")
def skill_import(source, name, description):
    files, cleanup = _read_skill_source(source)
    try:
        with get_session() as s:
            ws = get_active_workspace(s)
            sk = Skill(workspace_id=ws.id, name=name or Path(source).stem, description=description, source_url=source)
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
            if q in f"{sk.name} {sk.description}".lower(): click.echo(f"{sk.id}  {sk.name}")


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
def autopilot_trigger_update(trigger_id, cron, enabled):
    with get_session() as s:
        t = s.get(AutopilotTrigger, trigger_id)
        if not t: raise click.ClickException("Trigger not found")
        if cron is not None: t.cron_expression = cron; t.type = TriggerType.CRON
        s.commit(); click.echo(t.id)


@autopilot.command("trigger-rotate-url")
@click.argument("trigger_id")
def autopilot_trigger_rotate_url(trigger_id):
    with get_session() as s:
        t = s.get(AutopilotTrigger, trigger_id)
        if not t: raise click.ClickException("Trigger not found")
        t.webhook_token = secrets.token_urlsafe(32); t.type = TriggerType.WEBHOOK
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
            command = ["git", "-C", str(target), "pull", "--ff-only"]
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            command = ["git", "clone", r.url, str(target)]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode: raise click.ClickException(completed.stderr.strip())
        r.local_path = str(target); s.commit(); click.echo(str(target))


@repo.command("remove")
@click.argument("repo_id")
def repo_remove(repo_id):
    with get_session() as s:
        r = s.get(Repo, repo_id)
        if not r: raise click.ClickException("Repo not found")
        if r.local_path and Path(r.local_path).exists(): shutil.rmtree(r.local_path)
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
    target_dir = Path("attachments"); target_dir.mkdir(exist_ok=True)
    stored = target_dir / f"{secrets.token_hex(8)}-{source.name}"
    shutil.copy2(source, stored)
    with get_session() as s:
        item = Attachment(comment_id=comment_id, issue_id=issue_id, filename=source.name, path=str(stored))
        s.add(item); s.commit(); click.echo(item.id)


@attachment.command("download")
@click.argument("attachment_id")
@click.option("--out", required=True, type=click.Path(dir_okay=False))
def attachment_download(attachment_id, out):
    with get_session() as s:
        item = s.get(Attachment, attachment_id)
        if not item: raise click.ClickException("Attachment not found")
        shutil.copy2(item.path, out); click.echo(out)


@cli.group()
def chat():
    """Manage standalone chat threads."""


@chat.command("create")
@click.option("--title", required=True)
@click.option("--body", default=None)
def chat_create(title, body):
    with get_session() as s:
        thread = ChatThread(workspace_id=get_active_workspace(s).id, title=title); s.add(thread); s.flush()
        if body: s.add(ChatMessage(thread_id=thread.id, body=body))
        s.commit(); click.echo(thread.id)


@chat.command("history")
def chat_history():
    with get_session() as s:
        for thread in s.scalars(select(ChatThread).order_by(ChatThread.created_at.desc())).all():
            latest = thread.messages[-1].body if thread.messages else ""
            click.echo(f"{thread.id}  {thread.title}: {latest}")


@chat.command("thread")
@click.argument("thread_id")
def chat_thread(thread_id):
    with get_session() as s:
        thread = s.get(ChatThread, thread_id)
        if not thread: raise click.ClickException("Chat thread not found")
        click.echo(thread.title)
        for message in thread.messages: click.echo(f"[{message.created_at}] {message.author}: {message.body}")


@chat.command("send")
@click.argument("thread_id")
@click.option("--body", required=True)
@click.option("--author", default="you")
def chat_send(thread_id, body, author):
    with get_session() as s:
        if not s.get(ChatThread, thread_id): raise click.ClickException("Chat thread not found")
        s.add(ChatMessage(thread_id=thread_id, author=author, body=body)); s.commit(); click.echo("sent")


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
@click.option("--instructions", default=None)
def agent_update(agent_id, name, runtime_id, instructions):
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
            click.echo(f"{run.id}  {run.status.value:<10} {run.issue_id}  {run.prompt}")


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
        click.echo(f"id: {item.id}\nname: {item.name}\nagent_id: {item.agent_id}\nproject_id: {item.project_id}\nenabled: {item.enabled}")
        for trigger in item.triggers:
            value = trigger.cron_expression if trigger.type.value == "cron" else "<redacted>"
            click.echo(f"trigger: {trigger.id} {trigger.type.value} {value}")


@autopilot.command("update")
@click.argument("autopilot_id")
@click.option("--name", default=None)
@click.option("--enabled/--disabled", default=None)
@click.option("--filter-status", default=None, type=click.Choice([s.value for s in IssueStatus]))
def autopilot_update(autopilot_id, name, enabled, filter_status):
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
def issue_update(issue_id, title, description, status, assignee_agent_id, parent_issue_id, position):
    with get_session() as s:
        item = s.get(Issue, issue_id)
        if not item:
            raise click.ClickException("Issue not found")
        if title is not None:
            item.title = title
        if description is not None:
            item.description = description
        if status is not None:
            item.status = IssueStatus(status)
            s.add(TimelineEvent(issue_id=item.id, event_type="status_changed", detail=status))
        if assignee_agent_id is not None:
            if assignee_agent_id and not s.get(Agent, assignee_agent_id):
                raise click.ClickException("Agent not found")
            item.assignee_agent_id = assignee_agent_id or None
            s.add(TimelineEvent(issue_id=item.id, event_type="assigned", detail=assignee_agent_id or "unassigned"))
        if parent_issue_id is not None:
            item.parent_issue_id = parent_issue_id or None
        if position is not None:
            item.position = position
        s.commit()
        click.echo(item.id)


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
def project_update(project_id, name, description):
    with get_session() as s:
        item = s.get(Project, project_id)
        if not item:
            raise click.ClickException("Project not found")
        if name is not None:
            item.name = name
        if description is not None:
            item.description = description
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
if __name__ == "__main__":
    cli()
