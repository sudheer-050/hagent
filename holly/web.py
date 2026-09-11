"""FastAPI + Jinja2 web dashboard: view agents/runtimes/tasks, view output, trigger a run."""

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from holly.db import get_or_create_default_workspace, get_session, init_db
from holly.engine import run_task as engine_run_task
from holly.models import Agent, Runtime, Task

app = FastAPI(title="Holly")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/")
def dashboard(request: Request):
    with get_session() as s:
        runtimes = s.scalars(select(Runtime)).all()
        agents = s.scalars(select(Agent)).all()
        tasks = s.scalars(select(Task).order_by(Task.created_at.desc())).all()
        return templates.TemplateResponse(
            request, "dashboard.html", {"runtimes": runtimes, "agents": agents, "tasks": tasks}
        )


@app.get("/tasks/{task_id}")
def task_detail(request: Request, task_id: str):
    with get_session() as s:
        t = s.get(Task, task_id)
        return templates.TemplateResponse(request, "task_detail.html", {"task": t})


@app.post("/tasks")
def create_task(agent_id: str = Form(...), prompt: str = Form(...), run_now: bool = Form(False)):
    with get_session() as s:
        t = Task(agent_id=agent_id, prompt=prompt)
        s.add(t)
        s.commit()
        s.refresh(t)
        if run_now:
            engine_run_task(s, t)
        return RedirectResponse(url="/", status_code=303)


@app.post("/tasks/{task_id}/run")
def run_task_route(task_id: str):
    with get_session() as s:
        t = s.get(Task, task_id)
        if t:
            engine_run_task(s, t)
        return RedirectResponse(url="/", status_code=303)


@app.post("/runtimes")
def create_runtime(name: str = Form(...), type: str = Form(...), model: str = Form(...)):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        r = Runtime(workspace_id=ws.id, name=name, type=type, model=model, config_json="{}")
        s.add(r)
        s.commit()
        return RedirectResponse(url="/", status_code=303)


@app.post("/agents")
def create_agent(name: str = Form(...), runtime_id: str = Form(...), instructions: str = Form("")):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        a = Agent(workspace_id=ws.id, runtime_id=runtime_id, name=name, instructions=instructions)
        s.add(a)
        s.commit()
        return RedirectResponse(url="/", status_code=303)
