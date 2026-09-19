"""FastAPI + Jinja2 web dashboard: full kanban issue-tracker UI mirroring Multica's noun set."""

from pathlib import Path
from urllib.parse import quote, urlparse
from datetime import datetime, timezone
import asyncio
import json
import os
import re
import shutil

from fastapi import BackgroundTasks, FastAPI, Form, Request, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import httpx
import click
from hagent.tenancy import ScopeError
from hagent.db import request_workspace
from starlette.middleware.gzip import GZipMiddleware

from hagent.auth import AuthMiddleware, router as auth_router
from hagent.remote_api import router as remote_api_router
from hagent.worker_api import router as worker_api_router
from hagent.triggers import configure as configure_trigger
from hagent.engine import cancel_issue, execute_agent, mark_agent_skills_used
from hagent.agent_avatars import agent_avatar_url
from hagent.adapters import get_runtime_class
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload, selectinload

from hagent.db import get_active_workspace, get_or_create_default_workspace, get_session, init_db
from hagent.engine import process_queued_run_by_id, queue_issue_run
from hagent.models import (
    Attachment,
    IssueMetadata,
    IssueSubscriber,
    SquadActivity,
    Agent,
    Autopilot,
    AutopilotRun,
    AutopilotTrigger,
    ChatMessage,
    ChatThread,
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
    Workspace,
    UserProfile,
    AppSetting,
)
from hagent.scheduler import find_webhook_trigger, resume_pending_runs, run_autopilot_once, start_scheduler, sync_scheduler_jobs
from hagent.runtime_catalog import PROVIDERS, catalog_for, provider_config, provider_options
from hagent.skill_icons import choose_skill_emoji
from hagent.skill_badges import skill_badge_svg
from hagent.terminal import resolve_working_directory
from hagent.memory import MemoryScope, MemoryService
from hagent.router import ModelRouter
from hagent.models import RoutingDecision

get_or_create_default_workspace = get_active_workspace

app = FastAPI(title="Hagent")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["skill_badge_svg"] = skill_badge_svg
templates.env.globals["agent_avatar_url"] = agent_avatar_url


def chat_unread_count() -> int:
    with get_session() as s:
        return s.scalar(select(func.count()).select_from(ChatThread).where(ChatThread.unread.is_(True))) or 0


templates.env.globals["chat_unread_count"] = chat_unread_count
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
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


APP_SETTING_DEFAULTS = {
    "app_name": "Hagent",
    "tagline": "Self-hosted · local-first",
    "accent_color": "#e8a857",
    "app_icon_url": "/static/holly_icon.png",
    "density": "comfortable",
    "show_starfield": "true",
    "default_agent_delegation_limit": "8",
    "default_agent_require_approval": "false",
    "default_agent_terminal_enabled": "false",
    "default_agent_terminal_directory": str(Path.home()),
}


def get_app_settings() -> dict:
    values = dict(APP_SETTING_DEFAULTS)
    with get_session(scoped=False) as session:
        values.update({item.key: item.value for item in session.scalars(select(AppSetting)).all()})
    values["show_starfield"] = values["show_starfield"].lower() == "true"
    values["default_agent_require_approval"] = values["default_agent_require_approval"].lower() == "true"
    values["default_agent_terminal_enabled"] = values["default_agent_terminal_enabled"].lower() == "true"
    try:
        values["default_agent_delegation_limit"] = max(
            0, min(50, int(values["default_agent_delegation_limit"]))
        )
    except (TypeError, ValueError):
        values["default_agent_delegation_limit"] = 8
    return values


templates.env.globals["app_settings"] = get_app_settings


def _save_app_settings(values: dict[str, str]) -> None:
    with get_session(scoped=False) as session:
        for key, value in values.items():
            item = session.get(AppSetting, key)
            if item is None:
                session.add(AppSetting(key=key, value=value))
            else:
                item.value = value
        session.commit()


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


# Added after workspace_context so it is the outermost layer and runs first.
app.add_middleware(GZipMiddleware, minimum_size=1024)  # dashboard pages are large; matters most over a network
app.add_middleware(AuthMiddleware)
app.include_router(auth_router)
app.include_router(remote_api_router)
app.include_router(worker_api_router)


@app.exception_handler(ScopeError)
async def scope_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=404)


@app.exception_handler(click.ClickException)
@app.exception_handler(ValueError)
async def input_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(KeyError)
@app.exception_handler(PermissionError)
async def memory_scope_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=404)


def _memory_owner(session):
    profile = session.scalar(select(UserProfile))
    return profile.id if profile else "local-user"


def _memory_service(session, project_id=None, provider="web"):
    workspace = get_active_workspace(session)
    return MemoryService(session, MemoryScope(
        workspace_id=workspace.id, user_id=_memory_owner(session),
        project_id=project_id or None, provider=provider))


@app.get("/memory")
def memory_page(request: Request, q: str = "", project_id: str = "",
                provider: str = "", category: str = "", status: str = "active",
                sensitive_only: bool = False):
    with get_session() as session:
        service = _memory_service(session, project_id)
        memories = service.search(q, category=category or None,
            provider=provider or None, status=status, limit=50)
        if sensitive_only:
            memories = [item for item in memories if item["sensitivity"] in {"sensitive", "restricted"}]
        return templates.TemplateResponse(request, "memory.html", {
            "active": "memory", "memories": memories, "projects": session.scalars(select(Project)).all(),
            "filters": {"q": q, "project_id": project_id, "provider": provider,
                        "category": category, "status": status, "sensitive_only": sensitive_only},
            "settings": service.settings_dict(service.settings()),
        })


@app.get("/api/memory")
def memory_api(q: str = "", memory_id: str = "", project_id: str = "",
               provider: str = "", category: str = "", status: str = "active",
               origin_type: str = "", verification_status: str = "",
               sensitivity: str = "", source_session_id: str = "",
               limit: int = 20):
    with get_session() as session:
        return {"memories": _memory_service(session, project_id).search(
            q, memory_id=memory_id or None, provider=provider or None,
            category=category or None, status=status,
            origin_type=origin_type or None,
            verification_status=verification_status or None,
            sensitivity=sensitivity or None,
            source_session_id=source_session_id or None, limit=limit)}


@app.post("/memory")
def memory_create(content: str = Form(...), project_id: str = Form(""),
                  category: str = Form("explicit"), sensitivity: str = Form("normal"),
                  verified: str | None = Form(None)):
    with get_session() as session:
        _memory_service(session, project_id).remember(content, category=category,
            origin_type="explicit_user", sensitivity=sensitivity,
            verification_status="verified" if verified else "user_stated", actor="user")
    return RedirectResponse(f"/memory?project_id={quote(project_id)}", status_code=303)


@app.post("/memory/{memory_id}")
def memory_correct(memory_id: str, content: str = Form(...), project_id: str = Form(""),
                   category: str = Form("explicit"), verification_status: str = Form("verified"),
                   sensitivity: str = Form("normal"), supersede: str | None = Form(None)):
    with get_session() as session:
        _memory_service(session, project_id).update(memory_id, content=content,
            category=category, verification_status=verification_status,
            sensitivity=sensitivity, supersede=bool(supersede), actor="user")
    return RedirectResponse(f"/memory?project_id={quote(project_id)}", status_code=303)


@app.post("/memory/{memory_id}/forget")
def memory_delete(memory_id: str, project_id: str = Form("")):
    with get_session() as session:
        _memory_service(session, project_id).forget(memory_id)
    return RedirectResponse(f"/memory?project_id={quote(project_id)}", status_code=303)


@app.post("/memory/settings")
def memory_settings(project_id: str = Form(""), enabled: str | None = Form(None),
                    retain_raw_events: str | None = Form(None),
                    automatic_extraction: str | None = Form(None),
                    retrieval_limit: int = Form(8), token_budget: int = Form(1200),
                    retention_days: int = Form(365), strict_mode: str | None = Form(None),
                    embedding_provider: str = Form(""), embedding_model: str = Form(""),
                    embedding_base_url: str = Form("")):
    with get_session() as session:
        _memory_service(session, project_id).update_settings(
            enabled=bool(enabled), retain_raw_events=bool(retain_raw_events),
            automatic_extraction=bool(automatic_extraction),
            retrieval_limit=retrieval_limit, token_budget=token_budget,
            retention_days=retention_days, strict_mode=bool(strict_mode),
            embedding_provider=embedding_provider.strip(),
            embedding_model=embedding_model.strip(),
            embedding_base_url=embedding_base_url.strip())
    return RedirectResponse(f"/memory?project_id={quote(project_id)}", status_code=303)


@app.get("/api/memory/export")
def memory_export_api(project_id: str = ""):
    with get_session() as session:
        return JSONResponse(_memory_service(session, project_id).export(),
            headers={"Content-Disposition": 'attachment; filename="hagent-memory.json"'})


@app.get("/routing")
def routing_page(request: Request, project_id: str = ""):
    with get_session() as session:
        workspace = get_active_workspace(session)
        router = ModelRouter(session, workspace.id, project_id or None)
        decisions = list(session.scalars(select(RoutingDecision)
            .order_by(RoutingDecision.created_at.desc()).limit(50)).all())
        return templates.TemplateResponse(request, "routing.html", {
            "active": "routing", "projects": session.scalars(select(Project)).all(),
            "runtimes": session.scalars(select(Runtime).where(Runtime.archived.is_(False))).all(),
            "project_id": project_id, "policy": router.policy_dict(router.policy()),
            "decisions": [router.serialize(item) for item in decisions],
        })


@app.get("/api/routing/preview")
def routing_preview(prompt: str, project_id: str = "", runtime_id: str = "",
                    mode: str = "", model: str = "", effort: str = ""):
    with get_session() as session:
        workspace = get_active_workspace(session)
        runtime = require(session, Runtime, runtime_id) if runtime_id else None
        override = {key: value for key, value in {
            "runtime_id": runtime_id or None, "mode": mode or None,
            "model": model or None, "effort": effort or None}.items() if value is not None}
        return ModelRouter(session, workspace.id, project_id or None).route(
            prompt, default_runtime=runtime, override=override, persist=False)


@app.post("/routing/settings")
def routing_settings(project_id: str = Form(""), mode: str = Form("provider_fixed"),
                     provider: str = Form(""), model: str = Form(""),
                     effort: str = Form(""), max_effort: str = Form("high"),
                     max_cost_usd: str = Form(""), max_latency_ms: str = Form(""),
                     latency_preference: str = Form("balanced")):
    with get_session() as session:
        workspace = get_active_workspace(session)
        ModelRouter(session, workspace.id, project_id or None).set_policy(
            mode=mode, provider=provider.strip(), model=model.strip(), effort=effort,
            max_effort=max_effort,
            max_cost_usd=float(max_cost_usd) if max_cost_usd.strip() else None,
            max_latency_ms=int(max_latency_ms) if max_latency_ms.strip() else None,
            latency_preference=latency_preference)
    return RedirectResponse(f"/routing?project_id={quote(project_id)}", status_code=303)


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


@app.post("/settings")
async def update_settings(
    app_name: str = Form("Hagent"),
    tagline: str = Form(""),
    accent_color: str = Form("#e8a857"),
    density: str = Form("comfortable"),
    show_starfield: str | None = Form(None),
    default_agent_delegation_limit: int = Form(8),
    default_agent_require_approval: str | None = Form(None),
    default_agent_terminal_enabled: str | None = Form(None),
    default_agent_terminal_directory: str = Form(""),
    app_icon: UploadFile | None = File(None),
):
    app_name = app_name.strip()
    tagline = tagline.strip()
    accent_color = accent_color.strip().lower()
    if not app_name or len(app_name) > 40:
        raise HTTPException(400, "App name must be between 1 and 40 characters")
    if len(tagline) > 100:
        raise HTTPException(400, "Tagline must be 100 characters or fewer")
    if not re.fullmatch(r"#[0-9a-f]{6}", accent_color):
        raise HTTPException(400, "Accent color must be a six-digit hex color")
    if density not in {"comfortable", "compact"}:
        raise HTTPException(400, "Unknown interface density")
    if not 0 <= default_agent_delegation_limit <= 50:
        raise HTTPException(400, "Delegation calls per run must be between 0 and 50")
    terminal_directory = default_agent_terminal_directory.strip()
    if terminal_directory:
        terminal_directory = resolve_working_directory(terminal_directory)

    values = {
        "app_name": app_name,
        "tagline": tagline,
        "accent_color": accent_color,
        "density": density,
        "show_starfield": str(show_starfield is not None).lower(),
        "default_agent_delegation_limit": str(default_agent_delegation_limit),
        "default_agent_require_approval": str(default_agent_require_approval is not None).lower(),
        "default_agent_terminal_enabled": str(default_agent_terminal_enabled is not None).lower(),
        "default_agent_terminal_directory": terminal_directory,
    }

    if app_icon and app_icon.filename:
        content_types = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/x-icon": ".ico",
            "image/vnd.microsoft.icon": ".ico",
        }
        extension = content_types.get((app_icon.content_type or "").lower())
        if extension is None:
            raise HTTPException(400, "App icon must be PNG, JPEG, WebP, or ICO")
        data = await app_icon.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise HTTPException(413, "App icon exceeds 2 MiB")
        target = Path(__file__).parent / "static" / f"user_app_icon{extension}"
        target.write_bytes(data)
        values["app_icon_url"] = f"/static/{target.name}?v={int(datetime.now(timezone.utc).timestamp())}"

    _save_app_settings(values)
    app.title = app_name
    return RedirectResponse("/settings?saved=1", status_code=303)


HISTORY_CHAR_BUDGET = 16000


# Kept well above HISTORY_CHAR_BUDGET (the per-request prompt window) so trimming
# only fires occasionally in bulk, rather than shaving the thread on every message.
TRIM_TRIGGER_CHARS = 4 * HISTORY_CHAR_BUDGET
TRIM_TARGET_CHARS = 2 * HISTORY_CHAR_BUDGET


def _trim_thread_history(session, thread):
    session.refresh(thread, attribute_names=["messages"])
    messages = thread.messages
    total = sum(len(m.body) for m in messages)
    if total <= TRIM_TRIGGER_CHARS:
        return
    kept = 0
    cutoff = len(messages)
    for i, message in enumerate(reversed(messages)):
        kept += len(message.body)
        if kept > TRIM_TARGET_CHARS:
            cutoff = len(messages) - i - 1
            break
    for message in messages[:cutoff]:
        session.delete(message)
    session.commit()


HOLLY_VOICE_URL = "http://127.0.0.1:5000"


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
    resume_pending_runs()


@app.on_event("shutdown")
def _shutdown():
    pass


@app.get("/approvals")
def approvals_page(request: Request):
    with get_session() as s:
        runs = s.scalars(
            select(Run).where(Run.status == RunStatus.WAITING_APPROVAL).order_by(Run.created_at.desc())
        ).all()
        return templates.TemplateResponse(request, "approvals.html", {"runs": runs, "active": "approvals"})


A2A_INBOUND_PROJECT_NAME = "A2A Inbound"


@app.get("/")
def dashboard(request: Request):
    with get_session() as s:
        project_count = s.scalar(select(func.count()).select_from(Project))
        agent_count = s.scalar(select(func.count()).select_from(Agent))
        autopilot_count = s.scalar(select(func.count()).select_from(Autopilot))
        issue_count = s.scalar(select(func.count()).select_from(Issue))
        runtime_count = s.scalar(select(func.count()).select_from(Runtime))

        # An issue only "needs attention" for a failed run if its *latest* run
        # failed - a later successful retry should clear it, not leave it stuck
        # here forever.
        latest_run_at = (
            select(Run.issue_id, func.max(Run.created_at).label("latest_created_at"))
            .group_by(Run.issue_id)
            .subquery()
        )
        latest_failed_issue_ids = (
            select(Run.issue_id)
            .join(
                latest_run_at,
                (Run.issue_id == latest_run_at.c.issue_id)
                & (Run.created_at == latest_run_at.c.latest_created_at),
            )
            .where(Run.status == RunStatus.FAILED)
        )
        needs_attention = s.scalars(
            select(Issue)
            .where((Issue.status == IssueStatus.IN_PROGRESS) | Issue.id.in_(latest_failed_issue_ids))
            .order_by(Issue.updated_at.desc())
            .limit(8)
        ).all()

        # "Recently active" is meant to complement "Needs attention", not repeat
        # it - exclude anything already shown there so the two panels aren't
        # near-duplicates when most updates are in-progress/failed issues.
        needs_attention_ids = [i.id for i in needs_attention]
        recent_issues_query = select(Issue).order_by(Issue.updated_at.desc()).limit(6)
        if needs_attention_ids:
            recent_issues_query = select(Issue).where(Issue.id.notin_(needs_attention_ids)).order_by(
                Issue.updated_at.desc()
            ).limit(6)
        recent_issues = s.scalars(recent_issues_query).all()

        recent_runs = s.scalars(
            select(Run).options(joinedload(Run.issue)).order_by(Run.created_at.desc()).limit(6)
        ).all()

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "project_count": project_count,
                "agent_count": agent_count,
                "autopilot_count": autopilot_count,
                "issue_count": issue_count,
                "runtime_count": runtime_count,
                "needs_attention": needs_attention,
                "recent_issues": recent_issues,
                "recent_runs": recent_runs,
                "active": "dashboard",
            },
        )


SEARCH_PAGES = [
    ("Dashboard", "/"), ("Board", "/board"), ("Agents", "/agents"), ("Chat", "/chat"),
    ("Projects", "/projects"), ("Approvals", "/approvals"), ("Repos", "/repos"),
    ("Squads", "/squads"), ("Skills", "/skills"), ("Autopilots", "/autopilots"),
    ("General settings", "/settings"), ("Usage", "/usage"), ("Profile", "/profile"),
    ("Workspaces", "/workspaces"), ("Runtimes", "/runtimes"), ("Memory", "/memory"),
    ("Model routing", "/routing"),
]


@app.get("/api/search")
def api_search(q: str = ""):
    needle = q.strip().casefold()
    if not needle:
        return {"results": []}
    results = []
    with get_session() as s:
        for label, url in SEARCH_PAGES:
            if needle in label.casefold():
                results.append({"type": "page", "title": label, "subtitle": "Page", "url": url})
        for p in s.scalars(select(Project)).all():
            if needle in p.name.casefold():
                results.append({"type": "project", "title": p.name, "subtitle": "Project", "url": f"/projects/{p.id}"})
        for a in s.scalars(select(Agent)).all():
            if needle in a.name.casefold():
                results.append({"type": "agent", "title": a.name, "subtitle": "Agent", "url": f"/agents/{a.id}"})
                results.append({"type": "chat", "title": f"Chat with {a.name}", "subtitle": "Chat", "url": f"/chat/{a.id}"})
        for i in s.scalars(select(Issue)).all():
            if needle in i.title.casefold():
                project_name = i.project.name if i.project else ""
                results.append({"type": "issue", "title": i.title, "subtitle": f"Issue · {project_name}", "url": f"/issues/{i.id}"})
        for r in s.scalars(select(Runtime).where(Runtime.archived.is_(False))).all():
            if needle in r.name.casefold():
                results.append({"type": "runtime", "title": r.name, "subtitle": "Runtime", "url": f"/runtimes/{r.id}"})
        for skill in s.scalars(select(Skill)).all():
            if needle in skill.name.casefold():
                results.append({"type": "skill", "title": skill.name, "subtitle": "Skill", "url": "/skills"})
        for squad in s.scalars(select(Squad)).all():
            if needle in squad.name.casefold():
                results.append({"type": "squad", "title": squad.name, "subtitle": "Squad", "url": "/squads"})
    return {"results": results[:30]}


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


@app.get("/settings")
def settings_page(request: Request, saved: str = ""):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": get_app_settings(), "active": "settings", "saved": saved == "1"},
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
                "wide_layout": True,
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


@app.get("/board")
def all_issues_board(request: Request):
    """One board, every project's issues, grouped by status - so 'what's the current
    state of everything' doesn't require clicking into each project one at a time."""
    with get_session() as s:
        visible_statuses = [status for status in ISSUE_STATUS_ORDER if status != IssueStatus.CANCELLED]
        issues = s.scalars(select(Issue).where(Issue.status != IssueStatus.CANCELLED)).all()
        issues_by_status = {status: [] for status in visible_statuses}
        for i in sorted(issues, key=lambda i: i.updated_at, reverse=True):
            issues_by_status[i.status].append(i)
        return templates.TemplateResponse(
            request,
            "all_issues_board.html",
            {"statuses": visible_statuses, "issues_by_status": issues_by_status, "active": "board", "wide_layout": True},
        )


@app.get("/issues/{issue_id}/diff")
def issue_diff_page(request: Request, issue_id: str):
    from hagent.worktrees import compute_diff
    with get_session() as s:
        issue = require(s, Issue, issue_id)
        project = s.get(Project, issue.project_id)
        repo = s.get(Repo, project.repo_id) if project and project.repo_id else None
        diff_text = None
        error = None
        if not repo or not repo.local_path:
            error = "This issue's project isn't linked to a repo with a local checkout."
        else:
            diff_text = compute_diff(repo.local_path, issue_id)
            if diff_text is None:
                error = f"No isolated worktree exists yet for this issue - it hasn't run against '{repo.name}' yet."
        return templates.TemplateResponse(
            request,
            "issue_diff.html",
            {"issue": issue, "repo": repo, "diff_text": diff_text, "error": error, "active": "projects"},
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
def issue_run(issue_id: str, background_tasks: BackgroundTasks):
    with get_session() as s:
        i = require(s, Issue, issue_id)
        if i.assignee_agent_id:
            a = require(s, Agent, i.assignee_agent_id)
            run = queue_issue_run(s, i, a)
            if run.status == RunStatus.PENDING:
                background_tasks.add_task(process_queued_run_by_id, run.id)
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/runs/{run_id}/approve")
def approve_issue_run(issue_id: str, run_id: str, background_tasks: BackgroundTasks):
    with get_session() as s:
        issue = require(s, Issue, issue_id)
        run = require(s, Run, run_id)
        if run.issue_id != issue.id:
            raise HTTPException(404, "Run not found for this issue")
        changed = s.execute(
            update(Run).where(Run.id == run.id, Run.status == RunStatus.WAITING_APPROVAL).values(status=RunStatus.PENDING),
            execution_options={"synchronize_session": False},
        ).rowcount
        if not changed:
            raise HTTPException(409, "This run is no longer waiting for approval")
        issue.status = IssueStatus.IN_PROGRESS
        s.add(TimelineEvent(issue_id=issue.id, event_type="run_approved", detail=f"Run approved for agent {run.agent.name}"))
        s.commit()
        background_tasks.add_task(process_queued_run_by_id, run.id)
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/runs/{run_id}/reject")
def reject_issue_run(issue_id: str, run_id: str):
    with get_session() as s:
        issue = require(s, Issue, issue_id)
        run = require(s, Run, run_id)
        if run.issue_id != issue.id:
            raise HTTPException(404, "Run not found for this issue")
        changed = s.execute(
            update(Run).where(Run.id == run.id, Run.status == RunStatus.WAITING_APPROVAL).values(
                status=RunStatus.REJECTED, error="rejected by user", finished_at=datetime.now(timezone.utc)
            ),
            execution_options={"synchronize_session": False},
        ).rowcount
        if not changed:
            raise HTTPException(409, "This run is no longer waiting for approval")
        s.add(TimelineEvent(issue_id=issue.id, event_type="run_rejected", detail=f"Run rejected for agent {run.agent.name}"))
        s.commit()
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/runs/{run_id}/retry")
def issue_retry_run(issue_id: str, run_id: str, background_tasks: BackgroundTasks):
    """Retry a failed or cancelled run with the same agent and original prompt."""
    with get_session() as s:
        issue = require(s, Issue, issue_id)
        previous = require(s, Run, run_id)
        if previous.issue_id != issue.id:
            raise HTTPException(404, "Run not found for this issue")
        if previous.status not in (RunStatus.FAILED, RunStatus.CANCELLED):
            raise HTTPException(409, "Only failed or cancelled runs can be retried")
        agent = require(s, Agent, previous.agent_id)
        run = queue_issue_run(s, issue, agent, prompt=previous.prompt)
        if run.status == RunStatus.PENDING:
            background_tasks.add_task(process_queued_run_by_id, run.id)
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


# --- agents & runtimes ---

def _agent_activity_state_from_statuses(statuses) -> str:
    """Working = an active run in flight; thinking = a run queued or waiting
    on approval; resting = nothing pending."""
    if any(status == RunStatus.RUNNING for status in statuses):
        return "running"
    if any(status in (RunStatus.PENDING, RunStatus.WAITING_APPROVAL) for status in statuses):
        return "thinking"
    return "resting"


def _agent_activity_state(runs: list) -> str:
    return _agent_activity_state_from_statuses(r.status for r in runs)


def _chat_rows(s) -> list[dict]:
    agents = s.scalars(
        select(Agent).where(Agent.archived.is_(False)).order_by(Agent.name)
    ).all()
    threads = {t.agent_id: t for t in s.scalars(select(ChatThread)).all()}
    last_message = {}
    for thread in threads.values():
        if thread.messages:
            last_message[thread.agent_id] = thread.messages[-1]
    rows = [
        {
            "agent": agent,
            "thread": threads.get(agent.id),
            "unread": bool(threads.get(agent.id) and threads[agent.id].unread),
            "last_message": last_message.get(agent.id),
        }
        for agent in agents
    ]
    rows.sort(key=lambda r: (r["last_message"].created_at if r["last_message"] else r["agent"].created_at), reverse=True)
    return rows


@app.get("/chat")
def chat_index(request: Request):
    """General conversation with an agent - not tied to any issue. Also where
    an agent's direct messages to the owner (the message_user tool) show up."""
    with get_session() as s:
        rows = _chat_rows(s)
        return templates.TemplateResponse(request, "chat_list.html", {"rows": rows, "active": "chat"})


@app.get("/chat/{agent_id}")
def chat_thread_page(request: Request, agent_id: str):
    with get_session() as s:
        agent = require(s, Agent, agent_id)
        thread = s.scalars(select(ChatThread).where(ChatThread.agent_id == agent.id)).first()
        if thread and thread.unread:
            thread.unread = False
            s.commit()
        messages = thread.messages if thread else []
        rows = _chat_rows(s)
        return templates.TemplateResponse(
            request,
            "chat_thread.html",
            {"agent": agent, "messages": messages, "rows": rows, "active": "chat"},
        )


def _run_chat_turn(agent_id: str, message: str, resume_session_id: str | None, user_id: str | None) -> tuple[str, str | None]:
    """Runs on a worker thread with its own session - execute_agent needs a
    live session bound to the agent (routing, memory, delegation all read it)."""
    with get_session() as s:
        agent = require(s, Agent, agent_id)
        try:
            result = execute_agent(agent, message, resume_session_id=resume_session_id, memory_user_id=user_id)
            return (result.output or "").strip() or "(No response.)", result.session_id
        except Exception as exc:
            return f"Something went wrong reaching {agent.name}: {exc}", None


@app.post("/chat/{agent_id}/messages")
async def chat_send_message(request: Request, agent_id: str, message: str = Form(...)):
    message = message.strip()
    if not message:
        raise HTTPException(400, "Message cannot be empty")
    with get_session() as s:
        agent = require(s, Agent, agent_id)
        workspace = get_active_workspace(s)
        thread = s.scalars(select(ChatThread).where(ChatThread.agent_id == agent.id)).first()
        if thread is None:
            thread = ChatThread(workspace_id=workspace.id, agent_id=agent.id)
            s.add(thread)
            s.flush()
        s.add(ChatMessage(thread_id=thread.id, role="user", content=message))
        thread.unread = False
        s.commit()
        thread_id = thread.id
        resume_session_id = thread.session_id
        profile = s.scalar(select(UserProfile))
        user_id = profile.id if profile else None
    reply, new_session_id = await asyncio.to_thread(_run_chat_turn, agent_id, message, resume_session_id, user_id)
    with get_session() as s:
        thread = require(s, ChatThread, thread_id)
        s.add(ChatMessage(thread_id=thread.id, role="agent", content=reply))
        if new_session_id:
            thread.session_id = new_session_id
        s.commit()
    return RedirectResponse(f"/chat/{agent_id}", status_code=303)


@app.get("/agents")
def agents_page(request: Request):
    with get_session() as s:
        runtimes = s.scalars(select(Runtime).where(Runtime.archived.is_(False)).order_by(Runtime.name)).all()
        skills = s.scalars(select(Skill).order_by(Skill.name)).all()
        agents = s.scalars(
            select(Agent)
            .options(joinedload(Agent.runtime), selectinload(Agent.skills), selectinload(Agent.mcp_servers))
            .where(Agent.archived.is_(False))
            .order_by(Agent.name)
        ).all()
        agent_stats = {}
        runs_by_agent: dict[str, list] = {}
        for run in s.scalars(select(Run).order_by(Run.created_at.desc())).all():
            runs_by_agent.setdefault(run.agent_id, []).append(run)
        issue_counts = dict(
            s.execute(
                select(Issue.assignee_agent_id, func.count()).where(Issue.assignee_agent_id.is_not(None)).group_by(Issue.assignee_agent_id)
            ).all()
        )
        for agent in agents:
            runs = runs_by_agent.get(agent.id, [])
            agent_stats[agent.id] = {
                "run_count": len(runs),
                "issue_count": issue_counts.get(agent.id, 0),
                "last_run": runs[0] if runs else None,
                "is_running": any(r.status == RunStatus.RUNNING for r in runs),
                "state": _agent_activity_state(runs),
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
        active_statuses = s.scalars(
            select(Run.status).where(
                Run.agent_id == agent.id,
                Run.status.in_([RunStatus.RUNNING, RunStatus.PENDING, RunStatus.WAITING_APPROVAL]),
            )
        ).all()
        agent_state = _agent_activity_state_from_statuses(active_statuses)
        is_running = agent_state == "running"
        backup_runtime = s.get(Runtime, agent.backup_runtime_id) if agent.backup_runtime_id else None
        return templates.TemplateResponse(
            request,
            "agent_detail.html",
            {
                "agent": agent,
                "is_running": is_running,
                "agent_state": agent_state,
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
    designation: str = Form(""),
    description: str = Form(""),
    runtime_id: str = Form(...),
    backup_runtime_id: str = Form(""),
    instructions: str = Form(""),
    skill_ids: list[str] = Form(default=[]),
    mcp_server_ids: list[str] = Form(default=[]),
    environment_json: str = Form("{}"),
    terminal_enabled: str | None = Form(None),
    terminal_working_directory: str = Form(""),
    require_run_approval: str | None = Form(None),
    delegation_limit: int = Form(8),
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
        if not 0 <= delegation_limit <= 50:
            raise HTTPException(400, "Delegation calls per run must be between 0 and 50")
        agent.name = name.strip()
        agent.designation = designation.strip()
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
        agent.require_run_approval = require_run_approval is not None
        agent.delegation_limit = delegation_limit
        s.commit()
    return RedirectResponse(url=f"/agents/{agent_id}", status_code=303)


@app.get("/autopilots/graph")
def autopilots_graph(request: Request):
    """Read-only visual graph: Trigger -> Autopilot -> Agent -> Project for every
    autopilot in the workspace. A first, deliberately-scoped slice of a visual workflow
    builder - shows the real wiring, doesn't (yet) let you edit it here."""
    with get_session() as s:
        autopilots = s.scalars(select(Autopilot)).all()
        rows = []
        for ap in autopilots:
            triggers = list(ap.triggers)
            trigger_labels = [
                (f"cron: {t.cron_expression}" if t.type.value == "cron" else "webhook")
                for t in triggers
            ] or ["(no trigger)"]
            rows.append({
                "id": ap.id,
                "name": ap.name,
                "enabled": ap.enabled,
                "trigger_labels": trigger_labels,
                "agent_name": ap.agent.name if ap.agent else "(none)",
                "project_name": ap.project.name if ap.project else "(any project)",
            })
        return templates.TemplateResponse(request, "autopilots_graph.html", {"rows": rows, "active": "autopilots"})


@app.get("/usage")
def usage_page(request: Request):
    with get_session() as s:
        runtimes = s.scalars(select(Runtime)).all()
        runtime_rows = []
        for runtime in runtimes:
            runs = s.scalars(select(Run).join(Agent).where(Agent.runtime_id == runtime.id)).all()
            if not runs:
                continue
            runtime_rows.append({
                "name": runtime.name,
                "type": runtime.type.value,
                "run_count": len(runs),
                "input_tokens": sum(r.input_tokens or 0 for r in runs),
                "output_tokens": sum(r.output_tokens or 0 for r in runs),
                "completed": sum(1 for r in runs if r.status == RunStatus.COMPLETED),
                "failed": sum(1 for r in runs if r.status == RunStatus.FAILED),
            })
        agents = s.scalars(select(Agent).where(Agent.archived.is_(False))).all()
        agent_rows = []
        for agent in agents:
            runs = s.scalars(select(Run).where(Run.agent_id == agent.id)).all()
            if not runs:
                continue
            agent_rows.append({
                "name": agent.name,
                "runtime_name": agent.runtime.name if agent.runtime else "(none)",
                "run_count": len(runs),
                "input_tokens": sum(r.input_tokens or 0 for r in runs),
                "output_tokens": sum(r.output_tokens or 0 for r in runs),
                "last_run": max((r.created_at for r in runs), default=None),
            })
        agent_rows.sort(key=lambda row: row["last_run"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        verification_passed_count = len(s.scalars(select(TimelineEvent).where(TimelineEvent.event_type == "verification_passed")).all())
        verification_failed_count = len(s.scalars(select(TimelineEvent).where(TimelineEvent.event_type == "verification_failed")).all())
        return templates.TemplateResponse(request, "usage.html", {
            "active": "usage",
            "runtime_rows": runtime_rows,
            "agent_rows": agent_rows,
            "verification_passed": verification_passed_count,
            "verification_failed": verification_failed_count,
        })


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
            if runtime.health_status == "healthy":
                status.update(label="Provider responding", ready=True)
            elif runtime.health_status == "limited":
                status.update(label="Usage limit reached - backups promoted", ready=False)
            elif runtime.health_status == "error":
                status.update(label="Provider check failed", ready=False)
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
    designation: str = Form(""),
    runtime_id: str = Form(...),
    backup_runtime_id: str = Form(""),
    description: str = Form(""),
    instructions: str = Form(""),
    skill_ids: list[str] = Form(default=[]),
    terminal_enabled: str | None = Form(None),
    terminal_working_directory: str = Form(""),
    require_run_approval: str | None = Form(None),
    delegation_limit: int | None = Form(None),
):
    defaults = get_app_settings()
    if delegation_limit is None:
        delegation_limit = defaults["default_agent_delegation_limit"]
    if terminal_enabled is not None and not terminal_working_directory.strip():
        terminal_working_directory = defaults["default_agent_terminal_directory"]
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        runtime = require(s, Runtime, runtime_id)
        backup_runtime = require(s, Runtime, backup_runtime_id) if backup_runtime_id else None
        if backup_runtime and backup_runtime.id == runtime.id:
            raise HTTPException(400, "Backup runtime must differ from primary runtime")
        selected_skills = [require(s, Skill, skill_id) for skill_id in skill_ids]
        if not 0 <= delegation_limit <= 50:
            raise HTTPException(400, "Delegation calls per run must be between 0 and 50")
        agent = Agent(
            workspace_id=ws.id,
            runtime_id=runtime.id,
            backup_runtime_id=backup_runtime.id if backup_runtime else None,
            name=name.strip(),
            designation=designation.strip(),
            description=description.strip(),
            instructions=instructions.strip(),
            terminal_enabled=terminal_enabled is not None,
            terminal_working_directory=(
                resolve_working_directory(terminal_working_directory)
                if terminal_enabled is not None else None
            ),
            require_run_approval=require_run_approval is not None,
            delegation_limit=delegation_limit,
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
        skills = s.scalars(select(Skill).options(selectinload(Skill.agents), selectinload(Skill.files)).order_by(Skill.name)).all()
        agents = s.scalars(select(Agent).options(joinedload(Agent.runtime)).order_by(Agent.name)).all()
        return templates.TemplateResponse(request, "skills.html", {"skills": skills, "agents": agents, "skill_editor_ready": True, "active": "skills"})


@app.post("/skills")
def create_skill(name: str = Form(...), description: str = Form(""), content: str = Form("")):
    with get_session() as s:
        ws = get_or_create_default_workspace(s)
        used = s.scalars(select(Skill.emoji).where(Skill.workspace_id == ws.id)).all()
        emoji = choose_skill_emoji(name, description, content, used)
        s.add(Skill(workspace_id=ws.id, name=name, emoji=emoji, description=description, content=content))
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


@app.post("/skills/{skill_id}")
def update_skill(
    skill_id: str,
    name: str = Form(...),
    description: str = Form(""),
    content: str = Form(""),
    last_used_at: str = Form(""),
    agent_ids: list[str] = Form(default=[]),
):
    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(400, "Skill name cannot be empty")
    used_at = None
    if last_used_at.strip():
        try:
            used_at = datetime.fromisoformat(last_used_at.strip())
        except ValueError as exc:
            raise HTTPException(400, "Last used must be a valid date and time") from exc
        used_at = used_at.replace(tzinfo=timezone.utc) if used_at.tzinfo is None else used_at.astimezone(timezone.utc)
    with get_session() as s:
        skill = require(s, Skill, skill_id)
        selected_agents = [require(s, Agent, agent_id) for agent_id in dict.fromkeys(agent_ids)]
        skill.name = cleaned_name
        skill.description = description.strip()
        skill.content = content.strip()
        skill.last_used_at = used_at
        skill.agents = selected_agents
        s.commit()
    return RedirectResponse(url="/skills", status_code=303)



# --- autopilots ---

@app.get("/autopilots")
def autopilots_page(request: Request):
    with get_session() as s:
        autopilots = s.scalars(select(Autopilot)).all()
        autopilot_runs = s.scalars(
            select(AutopilotRun)
            .join(Autopilot, AutopilotRun.autopilot_id == Autopilot.id)
            .where(Autopilot.workspace_id == get_active_workspace(s).id)
            .order_by(AutopilotRun.started_at.desc())
            .limit(25)
        ).all()
        names = {autopilot.id: autopilot.name for autopilot in autopilots}
        request.state.recent_autopilot_runs = [
            (run, names.get(run.autopilot_id, 'Unknown autopilot')) for run in autopilot_runs
        ]
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
