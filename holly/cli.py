"""click-based CLI: holly agent/runtime/task ..."""

import click
from sqlalchemy import select

from holly.db import get_or_create_default_workspace, get_session, init_db
from holly.engine import run_task as engine_run_task
from holly.models import Agent, Runtime, RuntimeType, Task


@click.group()
def cli():
    """Holly: self-hosted multi-agent orchestration."""
    init_db()


# --- runtime ---

@cli.group()
def runtime():
    """Work with runtimes."""


@runtime.command("list")
def runtime_list():
    with get_session() as s:
        runtimes = s.scalars(select(Runtime)).all()
        for r in runtimes:
            click.echo(f"{r.id}  {r.name:<20} {r.type.value:<8} {r.model}")


@runtime.command("create")
@click.option("--name", required=True)
@click.option("--type", "runtime_type", required=True, type=click.Choice([t.value for t in RuntimeType]))
@click.option("--model", required=True)
@click.option("--config", default="{}", help="JSON config, e.g. base_url/api_key overrides")
def runtime_create(name, runtime_type, model, config):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        r = Runtime(workspace_id=ws.id, name=name, type=RuntimeType(runtime_type), model=model, config_json=config)
        s.add(r)
        s.commit()
        s.refresh(r)
        click.echo(f"Created runtime {r.id}")


# --- agent ---

@cli.group()
def agent():
    """Work with agents."""


@agent.command("list")
def agent_list():
    with get_session() as s:
        agents = s.scalars(select(Agent)).all()
        for a in agents:
            click.echo(f"{a.id}  {a.name:<20} runtime={a.runtime_id}")


@agent.command("get")
@click.argument("agent_id")
def agent_get(agent_id):
    with get_session() as s:
        a = s.get(Agent, agent_id)
        if not a:
            raise click.ClickException(f"Agent {agent_id} not found")
        click.echo(f"id: {a.id}\nname: {a.name}\nruntime_id: {a.runtime_id}\ninstructions: {a.instructions}")


@agent.command("create")
@click.option("--name", required=True)
@click.option("--runtime", "runtime_id", required=True, help="Runtime ID to bind this agent to")
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
        s.refresh(a)
        click.echo(f"Created agent {a.id}")


# --- task ---

@cli.group()
def task():
    """Work with tasks."""


@task.command("list")
def task_list():
    with get_session() as s:
        tasks = s.scalars(select(Task)).all()
        for t in tasks:
            click.echo(f"{t.id}  {t.status.value:<10} agent={t.agent_id}")


@task.command("get")
@click.argument("task_id")
def task_get(task_id):
    with get_session() as s:
        t = s.get(Task, task_id)
        if not t:
            raise click.ClickException(f"Task {task_id} not found")
        click.echo(f"id: {t.id}\nstatus: {t.status.value}\nprompt: {t.prompt}\noutput: {t.output}\nerror: {t.error}")


@task.command("create")
@click.option("--agent", "agent_id", required=True)
@click.option("--prompt", required=True)
@click.option("--run/--no-run", default=False, help="Run the task immediately after creating it")
def task_create(agent_id, prompt, run):
    with get_session() as s:
        a = s.get(Agent, agent_id)
        if not a:
            raise click.ClickException(f"Agent {agent_id} not found")
        t = Task(agent_id=a.id, prompt=prompt)
        s.add(t)
        s.commit()
        s.refresh(t)
        click.echo(f"Created task {t.id}")
        if run:
            engine_run_task(s, t)
            click.echo(f"status: {t.status.value}\noutput: {t.output}\nerror: {t.error}")


@task.command("run")
@click.argument("task_id")
def task_run(task_id):
    with get_session() as s:
        t = s.get(Task, task_id)
        if not t:
            raise click.ClickException(f"Task {task_id} not found")
        engine_run_task(s, t)
        click.echo(f"status: {t.status.value}\noutput: {t.output}\nerror: {t.error}")


if __name__ == "__main__":
    cli()
