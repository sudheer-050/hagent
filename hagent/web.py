"""FastAPI + Jinja2 web dashboard: full kanban issue-tracker UI mirroring Multica's noun set."""

from pathlib import Path
from urllib.parse import quote, urlparse
from datetime import datetime, timezone
import asyncio
import json
import os
import shutil

from fastapi import FastAPI, Form, Request, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import httpx
import click
from hagent.tenancy import ScopeError
from hagent.db import request_workspace
from hagent.triggers import configure as configure_trigger
from hagent.engine import cancel_issue, execute_agent
from hagent.adapters import get_runtime_class
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
    McpServer,
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
    KnowledgeBase,
    KnowledgeFolder,
    KnowledgeDocument,
    KnowledgeChunk,
)
from hagent.scheduler import find_webhook_trigger, run_autopilot_once, start_scheduler, sync_scheduler_jobs
from hagent.runtime_catalog import PROVIDERS, catalog_for, provider_config, provider_options
from hagent.terminal import resolve_working_directory, spawn_terminal
from hagent.rag import MAX_DOCUMENT_BYTES, embed_texts, fetch_url, index_local_folder, ingest_document, local_folder_snapshot, search_knowledge
from hagent.local_watch import refresh_local_folder_watches, start_local_folder_watcher, stop_local_folder_watcher

get_or_create_default_workspace = get_active_workspace

app = FastAPI(title="Hagent")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
@app.get("/terminal")
def terminal_page(request: Request):
    return templates.TemplateResponse(
        request,
        "terminal.html",
        {"active": "terminal", "default_cwd": str(Path.home()), "user_home": str(Path.home()), "project_cwd": str(Path.cwd())},
    )


@app.websocket("/ws/terminal")
async def terminal_socket(websocket: WebSocket):
    """Bridge one browser tab to one native Windows ConPTY session."""
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    if origin and urlparse(origin).netloc.lower() != host.lower():
        await websocket.close(code=1008, reason="Cross-origin terminal access denied")
        return
    await websocket.accept()
    process = None
    reader = None
    try:
        cwd = websocket.query_params.get("cwd") or str(Path.cwd())
        rows = int(websocket.query_params.get("rows", "30"))
        cols = int(websocket.query_params.get("cols", "120"))
        process = await asyncio.to_thread(spawn_terminal, cwd, rows, cols)
        await websocket.send_json({"type": "ready", "cwd": resolve_working_directory(cwd)})

        async def stream_output():
            while process.isalive():
                try:
                    output = await asyncio.to_thread(process.read, 4096)
                except (EOFError, OSError):
                    break
                if output:
                    await websocket.send_json({"type": "output", "data": output})
            try:
                await websocket.send_json({"type": "exit"})
            except Exception:
                pass

        reader = asyncio.create_task(stream_output())
        while process.isalive():
            message = await websocket.receive_json()
            kind = message.get("type")
            if kind == "input":
                await asyncio.to_thread(process.write, str(message.get("data", "")))
            elif kind == "resize":
                rows = max(2, min(int(message.get("rows", 30)), 200))
                cols = max(2, min(int(message.get("cols", 120)), 400))
                await asyncio.to_thread(process.setwinsize, rows, cols)
    except WebSocketDisconnect:
        pass
    except (ValueError, RuntimeError, OSError) as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if process is not None and process.isalive():
            await asyncio.to_thread(process.close, True)
        if reader is not None:
            reader.cancel()

@app.get("/api/runtime-models")
def runtime_models(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown runtime provider")
    installed = None
    if provider == "ollama":
        try:
            response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=0.75)
            response.raise_for_status()
            installed = [item["name"] for item in response.json().get("models", []) if item.get("name")]
        except httpx.HTTPError:
            pass
    elif provider == "lmstudio":
        try:
            response = httpx.get("http://127.0.0.1:1234/v1/models", timeout=0.75)
            response.raise_for_status()
            installed = [item["id"] for item in response.json().get("data", []) if item.get("id")]
        except httpx.HTTPError:
            pass
    return catalog_for(provider, installed)


def quickcreate_data():
    """Lightweight lists (projects, agents) that the quick-create modals in base.html
    need regardless of which page they're rendered on. Kept intentionally small --
    just id/name pairs -- since this runs on every page render."""
    with get_session() as s:
        projects = s.scalars(select(Project)).all()
        agents = s.scalars(select(Agent)).all()
        runtimes = s.scalars(select(Runtime)).all()
        skills = s.scalars(select(Skill).order_by(Skill.name)).all()
        return {
            "projects": [{"id": p.id, "name": p.name} for p in projects],
            "agents": [{"id": a.id, "name": a.name} for a in agents],
            "runtimes": [{"id": r.id, "name": r.name, "type": r.type.value, "model": r.model} for r in runtimes],
            "skills": [{"id": skill.id, "name": skill.name, "description": skill.description} for skill in skills],
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
def create_chat(title: str = Form(""), body: str = Form(""), agent_id: str = Form(...)):
    body = body.strip()
    title = title.strip() or (body[:64] if body else "New conversation")
    with get_session() as s:
        agent = require(s, Agent, agent_id)
        thread = ChatThread(workspace_id=get_active_workspace(s).id, agent_id=agent.id, title=title)
        s.add(thread); s.flush()
        if body:
            s.add(ChatMessage(thread_id=thread.id, body=body))
            s.commit()
            _reply_in_chat(s, thread, agent)
        else:
            s.commit()
        thread_id = thread.id
    return RedirectResponse(f"/chat?thread_id={thread_id}", status_code=303)


def _reply_in_chat(session, thread, agent):
    prompt = "\n".join(f"{message.author}: {message.body}" for message in thread.messages)
    try:
        result = execute_agent(agent, prompt)
        answer = result.output or "The agent returned an empty response."
    except Exception as exc:
        answer = f"I couldn't complete that response: {exc}"
    session.add(ChatMessage(thread_id=thread.id, author=agent.name, body=answer))
    session.commit()


@app.post("/chat/{thread_id}/messages")
def send_chat(thread_id: str, body: str = Form(...), agent_id: str = Form("")):
    body = body.strip()
    if not body:
        raise HTTPException(400, "Message cannot be empty")
    with get_session() as s:
        thread = require(s, ChatThread, thread_id)
        selected_agent = require(s, Agent, agent_id) if agent_id else None
        if selected_agent:
            thread.agent_id = selected_agent.id
        elif thread.agent_id:
            selected_agent = require(s, Agent, thread.agent_id)
        s.add(ChatMessage(thread_id=thread.id, body=body))
        s.commit()
        if selected_agent:
            _reply_in_chat(s, thread, selected_agent)
        current_thread_id = thread.id
    return RedirectResponse(f"/chat?thread_id={current_thread_id}", status_code=303)


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
    start_local_folder_watcher()


@app.on_event("shutdown")
def _shutdown():
    stop_local_folder_watcher()


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
                results.append({"type": "agent", "title": a.name, "subtitle": "Agent", "url": f"/agents/{a.id}"})
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
def chat_page(request: Request, thread_id: str = ""):
    with get_session() as s:
        agents = s.scalars(select(Agent).where(Agent.archived.is_(False)).order_by(Agent.name)).all()
        threads = s.scalars(select(ChatThread).order_by(ChatThread.created_at.desc())).all()
        active_thread = require(s, ChatThread, thread_id) if thread_id else (threads[0] if threads else None)
        return templates.TemplateResponse(
            request,
            "chat.html",
            {"threads": threads, "agents": agents, "agent_names": {a.id: a.name for a in agents}, "active_thread": active_thread, "active": "chat"},
        )


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
        runtimes = s.scalars(select(Runtime).where(Runtime.archived.is_(False)).order_by(Runtime.name)).all()
        skills = s.scalars(select(Skill).order_by(Skill.name)).all()
        agents = s.scalars(select(Agent).where(Agent.archived.is_(False)).order_by(Agent.name)).all()
        agent_stats = {}
        for agent in agents:
            runs = s.scalars(select(Run).where(Run.agent_id == agent.id).order_by(Run.created_at.desc())).all()
            assigned_issues = s.scalars(select(Issue).where(Issue.assignee_agent_id == agent.id)).all()
            agent_stats[agent.id] = {
                "run_count": len(runs),
                "issue_count": len(assigned_issues),
                "last_run": runs[0] if runs else None,
            }
        return templates.TemplateResponse(
            request,
            "agents.html",
            {"runtimes": runtimes, "skills": skills, "agents": agents, "agent_stats": agent_stats, "active": "agents"},
        )


@app.get("/agents/{agent_id}")
def agent_detail(request: Request, agent_id: str):
    with get_session() as s:
        agent = require(s, Agent, agent_id)
        runtimes = s.scalars(select(Runtime).where(Runtime.archived.is_(False)).order_by(Runtime.name)).all()
        skills = s.scalars(select(Skill).order_by(Skill.name)).all()
        mcp_servers = s.scalars(select(McpServer).order_by(McpServer.name)).all()
        runs = s.scalars(
            select(Run).where(Run.agent_id == agent.id).order_by(Run.created_at.desc()).limit(10)
        ).all()
        assigned_issues = s.scalars(
            select(Issue)
            .where(Issue.assignee_agent_id == agent.id)
            .order_by(Issue.updated_at.desc())
            .limit(10)
        ).all()
        memberships = s.scalars(select(SquadMember).where(SquadMember.agent_id == agent.id)).all()
        completed_runs = sum(1 for run in runs if run.status == RunStatus.COMPLETED)
        backup_runtime = s.get(Runtime, agent.backup_runtime_id) if agent.backup_runtime_id else None
        return templates.TemplateResponse(
            request,
            "agent_detail.html",
            {
                "agent": agent,
                "runtimes": runtimes,
                "skills": skills,
                "mcp_servers": mcp_servers,
                "agent_environment": json.dumps(json.loads(agent.env_json or "{}"), indent=2, sort_keys=True),
                "runs": runs,
                "assigned_issues": assigned_issues,
                "memberships": memberships,
                "completed_runs": completed_runs,
                "backup_runtime": backup_runtime,
                "active": "agents",
            },
        )


@app.post("/agents/{agent_id}")
def update_agent(
    agent_id: str,
    name: str = Form(...),
    description: str = Form(""),
    runtime_id: str = Form(...),
    backup_runtime_id: str = Form(""),
    instructions: str = Form(""),
    skill_ids: list[str] = Form(default=[]),
    mcp_server_ids: list[str] = Form(default=[]),
    environment_json: str = Form("{}"),
    terminal_enabled: str | None = Form(None),
    terminal_working_directory: str = Form(""),
):
    with get_session() as s:
        agent = require(s, Agent, agent_id)
        runtime = require(s, Runtime, runtime_id)
        backup_runtime = require(s, Runtime, backup_runtime_id) if backup_runtime_id else None
        if backup_runtime and backup_runtime.id == runtime.id:
            raise HTTPException(400, "Backup runtime must differ from primary runtime")
        selected_skills = [require(s, Skill, skill_id) for skill_id in skill_ids]
        selected_servers = [require(s, McpServer, server_id) for server_id in mcp_server_ids]
        try:
            environment = json.loads(environment_json or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "Environment must be valid JSON") from exc
        if not isinstance(environment, dict) or not all(isinstance(key, str) for key in environment):
            raise HTTPException(400, "Environment must be a JSON object")
        agent.name = name.strip()
        agent.description = description.strip()
        if not agent.name:
            raise ValueError("Agent name cannot be empty")
        agent.runtime = runtime
        agent.backup_runtime_id = backup_runtime.id if backup_runtime else None
        agent.instructions = instructions.strip()
        agent.skills = selected_skills
        agent.mcp_servers = selected_servers
        agent.env_json = json.dumps(environment, sort_keys=True)
        agent.terminal_enabled = terminal_enabled is not None
        agent.terminal_working_directory = (
            resolve_working_directory(terminal_working_directory)
            if agent.terminal_enabled else None
        )
        s.commit()
    return RedirectResponse(url=f"/agents/{agent_id}", status_code=303)


@app.get("/runtimes")
def runtimes_page(request: Request):
    with get_session() as s:
        runtimes = s.scalars(select(Runtime).where(Runtime.archived.is_(False)).order_by(Runtime.name)).all()
        connection_status = {}
        for runtime in runtimes:
            config = json.loads(runtime.config_json or "{}")
            provider_id = config.get("provider_id", runtime.type.value)
            profile = provider_config(provider_id)
            environment_name = profile.get("env")
            kind = profile.get("kind")
            status = {"provider_name": profile["name"], "provider_id": provider_id}
            if kind in {"local", "local_openai"}:
                base_url = config.get("base_url") or profile.get("base_url", "")
                check_url = (
                    f"{base_url.rstrip('/')}/api/tags"
                    if provider_id == "ollama"
                    else f"{base_url.rstrip('/')}/models"
                )
                try:
                    response = httpx.get(check_url, timeout=0.5)
                    response.raise_for_status()
                    status.update(label="Local service online", ready=True)
                except httpx.HTTPError:
                    status.update(label="Local service offline", ready=False)
            elif kind == "cli":
                command = config.get("command") or profile.get("command", "")
                available = bool(shutil.which(command) or Path(command).expanduser().is_file())
                if provider_id == "codex_cli" and not available:
                    local_app_data = os.environ.get("LOCALAPPDATA", "")
                    available = bool(local_app_data) and (
                        Path(local_app_data) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
                    ).is_file()
                status.update(
                    label="CLI installed" if available else "CLI not found",
                    ready=available,
                )
            elif config.get("api_key"):
                status.update(label="API key saved", ready=True)
            elif environment_name and os.environ.get(environment_name):
                status.update(label="Environment key", ready=True)
            elif profile.get("allow_no_key"):
                status.update(label="No key required", ready=True)
            else:
                status.update(label="API key needed", ready=False)
            connection_status[runtime.id] = status
        return templates.TemplateResponse(
            request,
            "runtimes.html",
            {"runtimes": runtimes, "connection_status": connection_status, "providers": provider_options(), "active": "runtimes"},
        )


@app.get("/runtimes/{runtime_id}")
def runtime_detail(request: Request, runtime_id: str):
    with get_session() as s:
        runtime = require(s, Runtime, runtime_id)
        config = json.loads(runtime.config_json or "{}")
        provider_id = config.get("provider_id", runtime.type.value)
        backup_agents = s.scalars(select(Agent).where(Agent.backup_runtime_id == runtime.id)).all()
        return templates.TemplateResponse(
            request,
            "runtime_detail.html",
            {
                "runtime": runtime,
                "runtime_config": config,
                "provider_id": provider_id,
                "provider": provider_config(provider_id),
                "providers": provider_options(),
                                "backup_agents": backup_agents,
                "active": "runtimes",
            },
        )


@app.post("/runtimes/{runtime_id}")
def update_runtime(
    runtime_id: str,
    name: str = Form(...),
    type: str = Form(...),
    model: str = Form(...),
    api_key: str = Form(""),
    base_url: str = Form(""),
    custom_model: str = Form(""),
    command: str = Form(""),
    working_directory: str = Form(""),
):
    if type not in PROVIDERS:
        raise HTTPException(400, "Unknown runtime provider")
    selected_model = custom_model.strip() or ("" if model == "__custom__" else model.strip())
    if not name.strip() or not selected_model:
        raise ValueError("Runtime name and model are required")
    profile = provider_config(type)
    with get_session() as s:
        runtime = require(s, Runtime, runtime_id)
        old_config = json.loads(runtime.config_json or "{}")
        same_provider = old_config.get("provider_id", runtime.type.value) == type
        config = {"provider_id": type}
        if api_key.strip():
            config["api_key"] = api_key.strip()
        elif same_provider and old_config.get("api_key"):
            config["api_key"] = old_config["api_key"]
        if profile.get("env"):
            config["api_key_env"] = profile["env"]
        resolved_base_url = base_url.strip().rstrip("/") or profile.get("base_url", "")
        if resolved_base_url:
            config["base_url"] = resolved_base_url
        resolved_command = command.strip() or profile.get("command", "")
        if resolved_command:
            config["command"] = resolved_command
        if working_directory.strip():
            config["working_directory"] = working_directory.strip()
        if profile.get("allow_no_key"):
            config["allow_no_key"] = True
        runtime.name = name.strip()
        runtime.type = profile["runtime_type"]
        runtime.model = selected_model
        runtime.config_json = json.dumps(config)
        s.commit()
    return RedirectResponse(url=f"/runtimes/{runtime_id}", status_code=303)


@app.post("/runtimes/{runtime_id}/archive")
def archive_runtime(runtime_id: str):
    with get_session() as s:
        runtime = require(s, Runtime, runtime_id)
        backup_agent = s.scalar(select(Agent).where(Agent.backup_runtime_id == runtime.id))
        if runtime.agents or backup_agent:
            raise ValueError("Reassign this runtime's primary and backup agents before archiving it")
        runtime.archived = True
        s.commit()
    return RedirectResponse(url="/runtimes", status_code=303)

@app.post("/runtimes")
def create_runtime(
    name: str = Form(...),
    type: str = Form(...),
    model: str = Form(...),
    api_key: str = Form(""),
    base_url: str = Form(""),
    custom_model: str = Form(""),
    command: str = Form(""),
    working_directory: str = Form(""),
):
    if type not in PROVIDERS:
        raise HTTPException(400, "Unknown runtime provider")
    profile = provider_config(type)
    config = {"provider_id": type}
    if api_key.strip():
        config["api_key"] = api_key.strip()
    if profile.get("env"):
        config["api_key_env"] = profile["env"]
    resolved_base_url = base_url.strip().rstrip("/") or profile.get("base_url", "")
    if resolved_base_url:
        config["base_url"] = resolved_base_url
    resolved_command = command.strip() or profile.get("command", "")
    if resolved_command:
        config["command"] = resolved_command
    if working_directory.strip():
        config["working_directory"] = working_directory.strip()
    if profile.get("allow_no_key"):
        config["allow_no_key"] = True
    selected_model = custom_model.strip() or ("" if model == "__custom__" else model.strip())
    if not selected_model:
        raise ValueError("Choose or enter a model")
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(
            Runtime(
                workspace_id=ws.id,
                name=name.strip(),
                type=profile["runtime_type"],
                model=selected_model,
                config_json=json.dumps(config),
            )
        )
        s.commit()
    return RedirectResponse(url="/runtimes", status_code=303)



@app.post("/api/agents/draft")
async def draft_agent_instructions(request: Request):
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(400, "Expected a JSON object")
    runtime_id = str(payload.get("runtime_id", "")).strip()
    purpose = str(payload.get("purpose", "")).strip()
    if not runtime_id or not purpose:
        raise HTTPException(400, "Choose a runtime and describe the agent's goal")
    if len(purpose) > 5000:
        raise HTTPException(400, "Agent goal must be 5,000 characters or fewer")
    with get_session() as s:
        runtime = require(s, Runtime, runtime_id)
        runtime_class = get_runtime_class(runtime.type)
        config = json.loads(runtime.config_json or "{}")
        model = runtime.model
    adapter = runtime_class(model=model, config=config)
    prompt = (
        "Draft concise, practical standing instructions for an AI teammate. "
        "Define its role, responsibilities, boundaries, first checks, and how it should report completion. "
        "Do not execute the task or claim access it may not have. Return only the instruction draft.\n\n"
        f"Agent goal: {purpose}"
    )
    try:
        result = await asyncio.to_thread(
            adapter.run,
            prompt,
            context="You are helping configure an AI agent. Produce editable instructions, not a task result.",
        )
    except Exception as exc:
        raise HTTPException(502, f"Instruction draft failed: {exc}") from exc
    instructions = (result.output or "").strip()
    if not instructions:
        raise HTTPException(502, "The selected runtime returned an empty instruction draft")
    return JSONResponse({"instructions": instructions})
@app.post("/agents")
def create_agent(
    name: str = Form(...),
    runtime_id: str = Form(...),
    backup_runtime_id: str = Form(""),
    description: str = Form(""),
    instructions: str = Form(""),
    skill_ids: list[str] = Form(default=[]),
    terminal_enabled: str | None = Form(None),
    terminal_working_directory: str = Form(""),
):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        runtime = require(s, Runtime, runtime_id)
        backup_runtime = require(s, Runtime, backup_runtime_id) if backup_runtime_id else None
        if backup_runtime and backup_runtime.id == runtime.id:
            raise HTTPException(400, "Backup runtime must differ from primary runtime")
        selected_skills = [require(s, Skill, skill_id) for skill_id in skill_ids]
        agent = Agent(
            workspace_id=ws.id,
            runtime_id=runtime.id,
            backup_runtime_id=backup_runtime.id if backup_runtime else None,
            name=name.strip(),
            description=description.strip(),
            instructions=instructions.strip(),
            terminal_enabled=terminal_enabled is not None,
            terminal_working_directory=(
                resolve_working_directory(terminal_working_directory)
                if terminal_enabled is not None else None
            ),
        )
        if not agent.name:
            raise ValueError("Agent name cannot be empty")
        agent.skills = selected_skills
        s.add(agent)
        s.flush()
        agent_id = agent.id
        s.commit()
    return RedirectResponse(url=f"/agents/{agent_id}", status_code=303)


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
        skills = s.scalars(select(Skill).order_by(Skill.name)).all()
        agents = s.scalars(select(Agent).where(Agent.archived.is_(False)).order_by(Agent.name)).all()
        return templates.TemplateResponse(request, "skills.html", {"skills": skills, "agents": agents, "active": "skills"})


@app.post("/skills")
def create_skill(name: str = Form(...), description: str = Form(""), content: str = Form("")):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        s.add(Skill(workspace_id=ws.id, name=name, description=description, content=content))
        s.commit()
    return RedirectResponse(url="/skills", status_code=303)


@app.post("/skills/{skill_id}/agents")
def update_skill_access(skill_id: str, agent_ids: list[str] = Form(default=[])):
    with get_session() as s:
        skill = require(s, Skill, skill_id)
        selected_agents = [require(s, Agent, agent_id) for agent_id in dict.fromkeys(agent_ids)]
        skill.agents = selected_agents
        s.commit()
    return RedirectResponse(url="/skills", status_code=303)


# --- retrieval-augmented knowledge ---

@app.get("/knowledge")
def knowledge_page(request: Request):
    with get_session() as s:
        bases = s.scalars(select(KnowledgeBase).order_by(KnowledgeBase.name)).all()
        return templates.TemplateResponse(request, "knowledge.html", {"bases": bases, "active": "knowledge"})


@app.post("/knowledge")
def create_knowledge_base(
    name: str = Form(...), description: str = Form(""),
    embedding_provider: str = Form("ollama"), embedding_model: str = Form(""),
):
    if embedding_provider not in {"ollama", "openai", "openai_compatible", "gemini"}:
        raise HTTPException(400, "Unsupported embedding provider")
    if not name.strip():
        raise HTTPException(400, "Knowledge base name is required")
    default_models = {"ollama": "embeddinggemma", "openai": "text-embedding-3-small",
        "gemini": "gemini-embedding-001", "openai_compatible": "text-embedding-3-small"}
    embedding_model = embedding_model.strip() or default_models[embedding_provider]
    with get_session() as s:
        workspace = get_active_workspace(s)
        base = KnowledgeBase(workspace_id=workspace.id, name=name.strip(), description=description.strip(),
            embedding_provider=embedding_provider, embedding_model=embedding_model.strip())
        if embedding_provider == "openai_compatible":
            base.embedding_base_url = ""
        if embedding_provider == "openai":
            base.embedding_base_url = "https://api.openai.com/v1"
        if embedding_provider == "gemini":
            base.embedding_model = embedding_model.strip() or "gemini-embedding-001"
        s.add(base)
        s.commit()
        base_id = base.id
    return RedirectResponse(f"/knowledge/{base_id}", status_code=303)


@app.get("/knowledge/{knowledge_base_id}")
def knowledge_detail(
    request: Request, knowledge_base_id: str, q: str = "", folder_done: bool = False,
    folder_indexed: int = 0, folder_updated: int = 0, folder_unchanged: int = 0,
    folder_removed: int = 0, folder_skipped: int = 0, folder_failed: int = 0, folder_error: str = "",
):
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        documents = s.scalars(select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == base.id).order_by(KnowledgeDocument.created_at.desc())).all()
        local_folders = s.scalars(select(KnowledgeFolder).where(
            KnowledgeFolder.knowledge_base_id == base.id).order_by(KnowledgeFolder.path)).all()
        agents = s.scalars(select(Agent).where(Agent.archived.is_(False)).order_by(Agent.name)).all()
        results = search_knowledge(s, [base.id], q, top_k=8) if q.strip() else []
        return templates.TemplateResponse(request, "knowledge_detail.html", {
            "base": base, "documents": documents, "local_folders": local_folders, "agents": agents,
            "assigned_agent_ids": {agent.id for agent in base.agents},
            "results": results, "q": q, "active": "knowledge",
            "folder_done": folder_done, "folder_indexed": folder_indexed,
            "folder_updated": folder_updated, "folder_unchanged": folder_unchanged,
            "folder_removed": folder_removed, "folder_skipped": folder_skipped, "folder_failed": folder_failed,
            "folder_error": folder_error,
        })


@app.post("/knowledge/{knowledge_base_id}/settings")
def update_knowledge_settings(
    knowledge_base_id: str, name: str = Form(...), description: str = Form(""),
    embedding_provider: str = Form(...), embedding_model: str = Form(...),
    embedding_base_url: str = Form(""), embedding_api_key: str = Form(""),
):
    if embedding_provider not in {"ollama", "openai", "openai_compatible", "gemini"}:
        raise HTTPException(400, "Unsupported embedding provider")
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        base.name = name.strip() or base.name
        base.description = description.strip()
        base.embedding_provider = embedding_provider
        base.embedding_model = embedding_model.strip()
        if embedding_base_url.strip():
            base.embedding_base_url = embedding_base_url.strip().rstrip("/")
        elif embedding_provider == "ollama":
            base.embedding_base_url = "http://localhost:11434"
        elif embedding_provider == "openai":
            base.embedding_base_url = "https://api.openai.com/v1"
        elif embedding_provider in {"gemini", "openai_compatible"}:
            base.embedding_base_url = ""
        if embedding_api_key.strip():
            base.embedding_api_key = embedding_api_key.strip()
        s.commit()
    return RedirectResponse(f"/knowledge/{knowledge_base_id}", status_code=303)


@app.post("/knowledge/{knowledge_base_id}/agents")
def set_knowledge_agents(knowledge_base_id: str, agent_ids: list[str] = Form(default=[])):
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        agents = [require(s, Agent, agent_id) for agent_id in dict.fromkeys(agent_ids)]
        if any(agent.workspace_id != base.workspace_id for agent in agents):
            raise HTTPException(400, "Agents and knowledge base must share a workspace")
        base.agents = agents
        s.commit()
    return RedirectResponse(f"/knowledge/{knowledge_base_id}", status_code=303)


@app.post("/knowledge/{knowledge_base_id}/documents")
async def add_knowledge_document(knowledge_base_id: str, file: UploadFile = File(...)):
    data = await file.read(MAX_DOCUMENT_BYTES + 1)
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        try:
            ingest_document(s, base, file.filename or "upload.txt", data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        s.commit()
    return RedirectResponse(f"/knowledge/{knowledge_base_id}", status_code=303)


@app.post("/knowledge/{knowledge_base_id}/folder")
def add_local_knowledge_folder(knowledge_base_id: str, folder: str = Form(...)):
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        try:
            normalized_path = str(Path(folder.strip()).expanduser().resolve(strict=True))
            scan_snapshot = local_folder_snapshot(normalized_path)
            counts = index_local_folder(s, base, normalized_path)
            source = s.scalar(select(KnowledgeFolder).where(
                KnowledgeFolder.knowledge_base_id == base.id,
                KnowledgeFolder.path == normalized_path,
            ))
            if source is None:
                source = KnowledgeFolder(knowledge_base_id=base.id, path=normalized_path)
                s.add(source)
            source.snapshot_json = json.dumps(scan_snapshot, separators=(",", ":"))
            source.last_scanned_at = datetime.now(timezone.utc)
            source.error = f"{counts['failed']} file(s) failed during this scan." if counts["failed"] else ""
        except ValueError as exc:
            return RedirectResponse(
                f"/knowledge/{knowledge_base_id}?folder_error={quote(str(exc))}", status_code=303,
            )
        s.commit()
    refresh_local_folder_watches()
    summary = "&".join(f"folder_{key}={counts[key]}" for key in ("indexed", "updated", "unchanged", "removed", "skipped", "failed"))
    return RedirectResponse(f"/knowledge/{knowledge_base_id}?folder_done=1&{summary}", status_code=303)


@app.post("/knowledge/{knowledge_base_id}/folders/{folder_id}/stop")
def stop_watching_knowledge_folder(knowledge_base_id: str, folder_id: str):
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        source = require(s, KnowledgeFolder, folder_id)
        if source.knowledge_base_id != base.id:
            raise HTTPException(404, "Folder source not found")
        s.delete(source)
        s.commit()
    refresh_local_folder_watches()
    return RedirectResponse(f"/knowledge/{knowledge_base_id}", status_code=303)


@app.post("/knowledge/{knowledge_base_id}/url")
def add_knowledge_url(knowledge_base_id: str, url: str = Form(...)):
    try:
        name, data, content_type = fetch_url(url.strip())
        if not name or "." not in name:
            name = (name or "web-page") + (".html" if "html" in content_type else ".txt")
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(400, f"Could not read URL: {exc}") from exc
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        try:
            ingest_document(s, base, name, data, source_type="url", source_uri=url.strip())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        s.commit()
    return RedirectResponse(f"/knowledge/{knowledge_base_id}", status_code=303)

@app.post("/knowledge/{knowledge_base_id}/documents/{document_id}/delete")
def delete_knowledge_document(knowledge_base_id: str, document_id: str):
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        document = require(s, KnowledgeDocument, document_id)
        if document.knowledge_base_id != base.id:
            raise HTTPException(404, "Document not found")
        s.delete(document)
        s.commit()
    return RedirectResponse(f"/knowledge/{knowledge_base_id}", status_code=303)


@app.post("/knowledge/{knowledge_base_id}/reindex")
def reindex_knowledge(knowledge_base_id: str):
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        documents = s.scalars(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == base.id)).all()
        for document in documents:
            chunks = list(document.chunks)
            try:
                vectors = []
                for start in range(0, len(chunks), 24):
                    vectors.extend(embed_texts(base, [chunk.content for chunk in chunks[start:start + 24]]))
                if len(vectors) != len(chunks):
                    raise ValueError("Embedding service returned a mismatched vector count")
                for chunk, vector in zip(chunks, vectors):
                    chunk.embedding_json = json.dumps(vector)
                document.status, document.error = "ready", ""
            except Exception as exc:
                document.status = "keyword_only"
                document.error = f"Re-embedding failed; keyword retrieval remains active. {str(exc)[:800]}"
        s.commit()
    return RedirectResponse(f"/knowledge/{knowledge_base_id}", status_code=303)


@app.post("/knowledge/{knowledge_base_id}/delete")
def delete_knowledge_base(knowledge_base_id: str):
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        s.delete(base)
        s.commit()
    return RedirectResponse("/knowledge", status_code=303)


@app.get("/api/knowledge/{knowledge_base_id}/search")
def api_knowledge_search(knowledge_base_id: str, q: str = "", top_k: int = 5):
    with get_session() as s:
        base = require(s, KnowledgeBase, knowledge_base_id)
        results = search_knowledge(s, [base.id], q, top_k=top_k)
        return {"results": results}


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
