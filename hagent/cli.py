"""click-based CLI mirroring Multica's noun/verb structure: hagent <noun> <verb> ..."""

import json
import time

import click
from sqlalchemy import select

from hagent.db import get_or_create_default_workspace, get_session, init_db
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
    Skill,
    Squad,
    SquadMember,
)


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
        click.echo(f"id: {sk.id}\nname: {sk.name}\ndescription: {sk.description}\ncontent:\n{sk.content}")


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
@click.option("--cron", required=True, help="Standard 5-field cron expression, e.g. '*/5 * * * *'")
def autopilot_trigger_add(autopilot_id, cron):
    with get_session() as s:
        ap = s.get(Autopilot, autopilot_id)
        if not ap:
            raise click.ClickException(f"Autopilot {autopilot_id} not found")
        t = AutopilotTrigger(autopilot_id=ap.id, cron_expression=cron)
        s.add(t)
        s.commit()
        click.echo(f"Added trigger {t.id} ({cron}) to autopilot {ap.name}")


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


if __name__ == "__main__":
    cli()
