"""FastAPI + Jinja2 web dashboard: full kanban issue-tracker UI mirroring Multica's noun set."""

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from hagent.db import get_or_create_default_workspace, get_session, init_db
from hagent.engine import run_issue as engine_run_issue
from hagent.models import (
    Agent,
    Autopilot,
    AutopilotTrigger,
    Comment,
    ISSUE_STATUS_ORDER,
    Issue,
    IssueStatus,
    Label,
    Project,
    Repo,
    Runtime,
    Skill,
    Squad,
    SquadMember,
)
from hagent.scheduler import run_autopilot_once, start_scheduler, sync_scheduler_jobs

app = FastAPI(title="Hagent")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
def _startup():
    init_db()
    start_scheduler()


@app.get("/")
def root():
    return RedirectResponse(url="/projects")


# --- projects ---

@app.get("/projects")
def projects_list(request: Request):
    with get_session() as s:
        projects = s.scalars(select(Project)).all()
        return templates.TemplateResponse(request, "projects_list.html", {"projects": projects, "active": "projects"})


@app.post("/projects")
def create_project(name: str = Form(...), description: str = Form("")):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(Project(workspace_id=ws.id, name=name, description=description))
        s.commit()
    return RedirectResponse(url="/projects", status_code=303)


@app.get("/projects/{project_id}")
def project_board(request: Request, project_id: str):
    with get_session() as s:
        project = s.get(Project, project_id)
        agents = s.scalars(select(Agent)).all()
        issues_by_status = {status: [] for status in ISSUE_STATUS_ORDER}
        for i in project.issues:
            issues_by_status[i.status].append(i)
        return templates.TemplateResponse(
            request,
            "project_board.html",
            {
                "project": project,
                "agents": agents,
                "statuses": ISSUE_STATUS_ORDER,
                "issues_by_status": issues_by_status,
                "active": "projects",
            },
        )


# --- issues ---

@app.post("/issues")
def create_issue(project_id: str = Form(...), title: str = Form(...), description: str = Form(""), assignee_agent_id: str = Form("")):
    with get_session() as s:
        i = Issue(
            project_id=project_id,
            title=title,
            description=description,
            assignee_agent_id=assignee_agent_id or None,
        )
        s.add(i)
        s.commit()
        return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@app.get("/issues/{issue_id}")
def issue_detail(request: Request, issue_id: str):
    with get_session() as s:
        issue = s.get(Issue, issue_id)
        agents = s.scalars(select(Agent)).all()
        all_labels = s.scalars(select(Label)).all()
        return templates.TemplateResponse(
            request,
            "issue_detail.html",
            {
                "issue": issue,
                "agents": agents,
                "all_labels": all_labels,
                "statuses": ISSUE_STATUS_ORDER,
                "active": "projects",
            },
        )


@app.post("/issues/{issue_id}/status")
def issue_set_status(issue_id: str, status: str = Form(...)):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        i.status = IssueStatus(status)
        s.commit()
        project_id = i.project_id
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@app.post("/issues/{issue_id}/assign")
def issue_assign(issue_id: str, agent_id: str = Form("")):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        i.assignee_agent_id = agent_id or None
        s.commit()
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/labels")
def issue_add_label(issue_id: str, label_id: str = Form(...)):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        l = s.get(Label, label_id)
        if l not in i.labels:
            i.labels.append(l)
            s.commit()
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/comments")
def issue_add_comment(issue_id: str, body: str = Form(...)):
    with get_session() as s:
        s.add(Comment(issue_id=issue_id, body=body))
        s.commit()
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/run")
def issue_run(issue_id: str):
    with get_session() as s:
        i = s.get(Issue, issue_id)
        if i.assignee_agent_id:
            a = s.get(Agent, i.assignee_agent_id)
            engine_run_issue(s, i, a)
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


# --- agents & runtimes ---

@app.get("/agents")
def agents_page(request: Request):
    with get_session() as s:
        runtimes = s.scalars(select(Runtime)).all()
        agents = s.scalars(select(Agent)).all()
        return templates.TemplateResponse(
            request, "agents.html", {"runtimes": runtimes, "agents": agents, "active": "agents"}
        )


@app.post("/runtimes")
def create_runtime(name: str = Form(...), type: str = Form(...), model: str = Form(...)):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(Runtime(workspace_id=ws.id, name=name, type=type, model=model, config_json="{}"))
        s.commit()
    return RedirectResponse(url="/agents", status_code=303)


@app.post("/agents")
def create_agent(name: str = Form(...), runtime_id: str = Form(...), instructions: str = Form("")):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(Agent(workspace_id=ws.id, runtime_id=runtime_id, name=name, instructions=instructions))
        s.commit()
    return RedirectResponse(url="/agents", status_code=303)


# --- squads ---

@app.get("/squads")
def squads_page(request: Request):
    with get_session() as s:
        squads = s.scalars(select(Squad)).all()
        agents = s.scalars(select(Agent)).all()
        return templates.TemplateResponse(request, "squads.html", {"squads": squads, "agents": agents, "active": "squads"})


@app.post("/squads")
def create_squad(name: str = Form(...), description: str = Form("")):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(Squad(workspace_id=ws.id, name=name, description=description))
        s.commit()
    return RedirectResponse(url="/squads", status_code=303)


@app.post("/squads/{squad_id}/members")
def add_squad_member(squad_id: str, agent_id: str = Form(...), role: str = Form("member")):
    with get_session() as s:
        s.add(SquadMember(squad_id=squad_id, agent_id=agent_id, role=role))
        s.commit()
    return RedirectResponse(url="/squads", status_code=303)


# --- skills ---

@app.get("/skills")
def skills_page(request: Request):
    with get_session() as s:
        skills = s.scalars(select(Skill)).all()
        return templates.TemplateResponse(request, "skills.html", {"skills": skills, "active": "skills"})


@app.post("/skills")
def create_skill(name: str = Form(...), description: str = Form(""), content: str = Form("")):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(Skill(workspace_id=ws.id, name=name, description=description, content=content))
        s.commit()
    return RedirectResponse(url="/skills", status_code=303)


# --- autopilots ---

@app.get("/autopilots")
def autopilots_page(request: Request):
    with get_session() as s:
        autopilots = s.scalars(select(Autopilot)).all()
        agents = s.scalars(select(Agent)).all()
        projects = s.scalars(select(Project)).all()
        return templates.TemplateResponse(
            request,
            "autopilots.html",
            {
                "autopilots": autopilots,
                "agents": agents,
                "projects": projects,
                "statuses": ISSUE_STATUS_ORDER,
                "active": "autopilots",
            },
        )


@app.post("/autopilots")
def create_autopilot(
    name: str = Form(...),
    agent_id: str = Form(...),
    project_id: str = Form(""),
    filter_status: str = Form(""),
):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(
            Autopilot(
                workspace_id=ws.id,
                name=name,
                agent_id=agent_id,
                project_id=project_id or None,
                filter_status=IssueStatus(filter_status) if filter_status else None,
            )
        )
        s.commit()
    return RedirectResponse(url="/autopilots", status_code=303)


@app.post("/autopilots/{autopilot_id}/triggers")
def add_autopilot_trigger(autopilot_id: str, cron: str = Form(...)):
    with get_session() as s:
        s.add(AutopilotTrigger(autopilot_id=autopilot_id, cron_expression=cron))
        s.commit()
    scheduler = start_scheduler()
    sync_scheduler_jobs(scheduler)
    return RedirectResponse(url="/autopilots", status_code=303)


@app.post("/autopilots/{autopilot_id}/trigger-now")
def trigger_autopilot_now(autopilot_id: str):
    run_autopilot_once(autopilot_id)
    return RedirectResponse(url="/autopilots", status_code=303)


# --- repos ---

@app.get("/repos")
def repos_page(request: Request):
    with get_session() as s:
        repos = s.scalars(select(Repo)).all()
        return templates.TemplateResponse(request, "repos.html", {"repos": repos, "active": "repos"})


@app.post("/repos")
def create_repo(name: str = Form(...), url: str = Form(...)):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(Repo(workspace_id=ws.id, name=name, url=url))
        s.commit()
    return RedirectResponse(url="/repos", status_code=303)
