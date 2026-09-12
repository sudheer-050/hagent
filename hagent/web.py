"""FastAPI + Jinja2 web dashboard: full kanban issue-tracker UI mirroring Multica's noun set."""

from pathlib import Path

from fastapi import FastAPI, Form, Request, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
import click
from hagent.tenancy import ScopeError
from hagent.db import request_workspace
from hagent.triggers import configure as configure_trigger
from hagent.engine import cancel_issue, execute_agent
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from hagent.db import get_active_workspace, get_or_create_default_workspace, get_session, init_db
from hagent.engine import run_issue as engine_run_issue
from hagent.models import (
    Attachment,
    IssueMetadata,
    IssueSubscriber,
    SquadActivity,
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
    Run,
    RunStatus,
    Runtime,
    Skill,
    Squad,
    SquadMember,
    TimelineEvent,
    ChatThread,
    ChatMessage,
    Workspace,
    UserProfile,
)
from hagent.scheduler import find_webhook_trigger, run_autopilot_once, start_scheduler, sync_scheduler_jobs

get_or_create_default_workspace = get_active_workspace

app = FastAPI(title="Hagent")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


def quickcreate_data():
    """Lightweight lists (projects, agents) that the quick-create modals in base.html
    need regardless of which page they're rendered on. Kept intentionally small --
    just id/name pairs -- since this runs on every page render."""
    with get_session() as s:
        projects = s.scalars(select(Project)).all()
        agents = s.scalars(select(Agent)).all()
        runtimes = s.scalars(select(Runtime)).all()
        return {
            "projects": [{"id": p.id, "name": p.name} for p in projects],
            "agents": [{"id": a.id, "name": a.name} for a in agents],
            "runtimes": [{"id": r.id, "name": r.name, "type": r.type.value} for r in runtimes],
        }


templates.env.globals["quickcreate_data"] = quickcreate_data


def require(session, model, identifier):
    item = session.get(model, identifier)
    if item is None:
        raise HTTPException(404, "Object not found in selected workspace")
    return item


@app.middleware("http")
async def workspace_context(request, call_next):
    token = request_workspace.set(request.cookies.get("workspace_id"))
    try:
        return await call_next(request)
    finally:
        request_workspace.reset(token)


@app.exception_handler(ScopeError)
async def scope_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=404)


@app.exception_handler(click.ClickException)
@app.exception_handler(ValueError)
async def input_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/workspaces")
def workspace_create(name: str = Form(...)):
    with get_session() as s:
        s.add(Workspace(name=name)); s.commit()
    return RedirectResponse("/workspaces", status_code=303)


@app.post("/workspaces/{workspace_id}/switch")
def workspace_switch(workspace_id: str):
    with get_session() as s:
        require(s, Workspace, workspace_id)
    response = RedirectResponse("/projects", status_code=303)
    response.set_cookie("workspace_id", workspace_id, httponly=True, samesite="strict")
    return response


@app.post("/profile")
def update_profile(name: str = Form(""), email: str = Form(""), bio: str = Form("")):
    with get_session() as s:
        profile = s.scalar(select(UserProfile))
        if profile is None:
            profile = UserProfile(); s.add(profile)
        profile.name, profile.email, profile.bio = name, email, bio
        s.commit()
    return RedirectResponse("/profile", status_code=303)


@app.post("/chat")
def create_chat(title: str = Form(...), body: str = Form("")):
    with get_session() as s:
        thread = ChatThread(workspace_id=get_active_workspace(s).id, title=title)
        s.add(thread); s.flush()
        if body: s.add(ChatMessage(thread_id=thread.id, body=body))
        s.commit()
    return RedirectResponse("/chat", status_code=303)


@app.post("/chat/{thread_id}/messages")
def send_chat(thread_id: str, body: str = Form(...), agent_id: str = Form("")):
    with get_session() as s:
        thread = require(s, ChatThread, thread_id)
        agent = require(s, Agent, agent_id) if agent_id else None
        s.add(ChatMessage(thread_id=thread.id, body=body)); s.commit()
        if agent:
            prompt = "\n".join(f"{message.author}: {message.body}" for message in thread.messages)
            result = execute_agent(agent, prompt)
            s.add(ChatMessage(thread_id=thread.id, author=agent.name, body=result.output)); s.commit()
    return RedirectResponse("/chat", status_code=303)


@app.post("/issues/{issue_id}/cancel")
def cancel_task(issue_id: str):
    with get_session() as s:
        cancel_issue(s, require(s, Issue, issue_id))
    return RedirectResponse(f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/metadata")
def set_metadata(issue_id: str, key: str = Form(...), value: str = Form(...)):
    with get_session() as s:
        issue = require(s, Issue, issue_id)
        item = next((m for m in issue.metadata_values if m.key == key), None)
        if item is None:
            item = IssueMetadata(issue_id=issue.id, key=key); s.add(item)
        item.value = value; s.commit()
    return RedirectResponse(f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/subscribers")
def add_subscriber(issue_id: str, name: str = Form(...)):
    with get_session() as s:
        issue = require(s, Issue, issue_id)
        if not any(item.name == name for item in issue.subscribers):
            s.add(IssueSubscriber(issue_id=issue.id, name=name)); s.commit()
    return RedirectResponse(f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/reorder")
def reorder_issue(issue_id: str, position: int = Form(...)):
    with get_session() as s:
        issue = require(s, Issue, issue_id)
        issue.position = position; s.commit()
        project_id = issue.project_id
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/issues/{issue_id}/attachments")
def upload_attachment(issue_id: str, file: UploadFile = File(...)):
    import secrets
    with get_session() as s:
        require(s, Issue, issue_id)
        folder = Path("attachments") / get_active_workspace(s).id
        folder.mkdir(parents=True, exist_ok=True)
        filename = Path((file.filename or "attachment").replace("\\", "/")).name
        data = file.file.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024: raise HTTPException(413, "Attachment exceeds 10 MiB")
        target = folder / secrets.token_hex(16)
        target.write_bytes(data)
        s.add(Attachment(issue_id=issue_id, filename=filename, path=str(target.resolve()))); s.commit()
    return RedirectResponse(f"/issues/{issue_id}", status_code=303)


@app.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: str):
    with get_session() as s:
        item = require(s, Attachment, attachment_id)
        if not Path(item.path).is_file(): raise HTTPException(404, "Attachment file missing")
        return FileResponse(item.path, filename=item.filename)


@app.on_event("startup")
def _startup():
    init_db()
    start_scheduler()


@app.get("/")
def dashboard(request: Request):
    with get_session() as s:
        projects = s.scalars(select(Project)).all()
        agents = s.scalars(select(Agent)).all()
        autopilots = s.scalars(select(Autopilot)).all()
        issues = s.scalars(select(Issue)).all()

        needs_attention = sorted(
            (
                i
                for i in issues
                if i.status == IssueStatus.IN_PROGRESS
                or any(r.status == RunStatus.FAILED for r in i.runs)
            ),
            key=lambda i: i.updated_at,
            reverse=True,
        )[:8]

        recent_issues = sorted(issues, key=lambda i: i.updated_at, reverse=True)[:6]

        recent_runs = sorted(
            (r for i in issues for r in i.runs),
            key=lambda r: r.created_at,
            reverse=True,
        )[:6]

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "project_count": len(projects),
                "agent_count": len(agents),
                "autopilot_count": len(autopilots),
                "issue_count": len(issues),
                "needs_attention": needs_attention,
                "recent_issues": recent_issues,
                "recent_runs": recent_runs,
                "active": "dashboard",
            },
        )


@app.get("/api/search")
def api_search(q: str = ""):
    needle = q.strip().casefold()
    if not needle:
        return {"results": []}
    results = []
    with get_session() as s:
        for p in s.scalars(select(Project)).all():
            if needle in p.name.casefold():
                results.append({"type": "project", "title": p.name, "subtitle": "Project", "url": f"/projects/{p.id}"})
        for a in s.scalars(select(Agent)).all():
            if needle in a.name.casefold():
                results.append({"type": "agent", "title": a.name, "subtitle": "Agent", "url": "/agents"})
        for i in s.scalars(select(Issue)).all():
            if needle in i.title.casefold():
                project_name = i.project.name if i.project else ""
                results.append({"type": "issue", "title": i.title, "subtitle": f"Issue · {project_name}", "url": f"/issues/{i.id}"})
    return {"results": results[:20]}


@app.post("/webhooks/{token}")
def autopilot_webhook(token: str):
    trigger = find_webhook_trigger(token)
    if not trigger:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    result = run_autopilot_once(trigger.autopilot_id)
    if result is None:
        return JSONResponse({"error": "autopilot disabled"}, status_code=409)
    return {"autopilot_run_id": result.id, "status": result.status, "summary": result.summary}


@app.get("/workspaces")
def workspaces_page(request: Request):
    with get_session() as s:
        rows = s.scalars(select(Workspace)).all()
        return templates.TemplateResponse(request, "workspaces.html", {"workspaces": rows, "active": "workspaces"})


@app.get("/profile")
def profile_page(request: Request):
    with get_session() as s:
        profile = s.scalar(select(UserProfile).order_by(UserProfile.updated_at.desc()))
        if not profile:
            return templates.TemplateResponse(request, "profile.html", {"profile": None, "active": "profile"})
        return templates.TemplateResponse(request, "profile.html", {"profile": profile, "active": "profile"})


@app.get("/chat")
def chat_page(request: Request):
    with get_session() as s:
        threads = s.scalars(select(ChatThread).order_by(ChatThread.created_at.desc())).all()
        return templates.TemplateResponse(request, "chat.html", {"threads": threads, "agents": s.scalars(select(Agent)).all(), "active": "chat"})


# --- projects ---

@app.get("/projects")
def projects_list(request: Request, q: str = ""):
    with get_session() as s:
        projects = s.scalars(select(Project)).all()
        return templates.TemplateResponse(request, "projects_list.html", {"projects": projects, "q": q, "matches": [i for i in s.scalars(select(Issue)).all() if q and q.casefold() in (i.title + " " + i.description + " " + " ".join(c.body for c in i.comments)).casefold()], "active": "projects"})


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
        project = require(s, Project, project_id)
        agents = s.scalars(select(Agent)).all()
        issues_by_status = {status: [] for status in ISSUE_STATUS_ORDER}
        for i in sorted(project.issues, key=lambda i: (i.position, i.created_at)):
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
        issue = require(s, Issue, issue_id)
        agents = s.scalars(select(Agent)).all()
        all_labels = s.scalars(select(Label)).all()
        return templates.TemplateResponse(
            request,
            "issue_detail.html",
            {
                "issue": issue,
                "children": s.scalars(select(Issue).where(Issue.parent_issue_id == issue.id).order_by(Issue.position)).all(),
                "agents": agents,
                "all_labels": all_labels,
                "statuses": ISSUE_STATUS_ORDER,
                "active": "projects",
            },
        )


@app.post("/issues/{issue_id}/status")
def issue_set_status(issue_id: str, status: str = Form(...)):
    with get_session() as s:
        i = require(s, Issue, issue_id)
        i.status = IssueStatus(status)
        s.add(TimelineEvent(issue_id=i.id, event_type="status_changed", detail=status))
        s.commit()
        project_id = i.project_id
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@app.post("/issues/{issue_id}/assign")
def issue_assign(issue_id: str, agent_id: str = Form("")):
    with get_session() as s:
        i = require(s, Issue, issue_id)
        i.assignee_agent_id = agent_id or None
        s.add(TimelineEvent(issue_id=i.id, event_type="assigned", detail=agent_id or "unassigned"))
        s.commit()
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/labels")
def issue_add_label(issue_id: str, label_id: str = Form(...)):
    with get_session() as s:
        i = require(s, Issue, issue_id)
        l = require(s, Label, label_id)
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
        i = require(s, Issue, issue_id)
        if i.assignee_agent_id:
            a = require(s, Agent, i.assignee_agent_id)
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
        s.add(configure_trigger(AutopilotTrigger(autopilot_id=autopilot_id), cron=cron))
        s.commit()
    scheduler = start_scheduler()
    sync_scheduler_jobs(scheduler)
    return RedirectResponse(url="/autopilots", status_code=303)


@app.post("/autopilots/{autopilot_id}/trigger-now")
def trigger_autopilot_now(autopilot_id: str):
    with get_session() as s:
        require(s, Autopilot, autopilot_id)
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
