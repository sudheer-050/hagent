"""Owner-operated Windows terminal, with bounded output and durable session history."""
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import threading
import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update

from hagent.db import get_session, get_active_workspace, request_workspace
from hagent.models import TerminalSession, Run, TimelineEvent
from hagent.terminal import resolve_working_directory
from hagent.runtime_env import scrub_inherited_environment

router = APIRouter()
_active = {}
MAX_TERMINALS_PER_OWNER = 8
_lock = threading.Lock()
OUTPUT_LIMIT = 1_000_000
log = logging.getLogger(__name__)


def _owner(connection):
    user = getattr(connection.state, "user", None)
    if user and user.role != "owner":
        raise HTTPException(403, "Only the instance owner can open interactive terminals")
    return user.id if user else "local-owner"


@router.get("/terminal")
def terminal_page(request: Request):
    _owner(request)
    from hagent.web import templates
    return templates.TemplateResponse(request, "terminal.html", {
        "active": "terminal", "default_directory": str(Path.home()),
    })


@router.get("/api/terminal/sessions")
def terminal_sessions(request: Request):
    owner = _owner(request)
    with get_session() as s:
        rows = s.scalars(select(TerminalSession).where(TerminalSession.owner_id == owner)
                         .order_by(TerminalSession.started_at.desc()).limit(30)).all()
        return [{"id": r.id, "cwd": r.cwd, "status": r.status,
                 "started_at": r.started_at.isoformat()} for r in rows]


@router.get("/api/terminal/sessions/{session_id}")
def terminal_transcript(request: Request, session_id: str):
    owner = _owner(request)
    with get_session() as s:
        row = s.get(TerminalSession, session_id)
        if not row or row.owner_id != owner:
            raise HTTPException(404, "Terminal session not found")
        return {"id": row.id, "transcript": row.transcript, "status": row.status}


@router.get("/api/terminal/agents")
def agent_terminals(request: Request):
    _owner(request)
    from hagent.managed_process import active_terminals
    with get_session() as s:
        return active_terminals(get_active_workspace(s).id)


@router.get("/api/terminal/activity")
def terminal_activity(request: Request):
    _owner(request)
    with get_session() as s:
        runs = s.scalars(select(Run).order_by(Run.created_at.desc()).limit(20)).all()
        events = s.scalars(select(TimelineEvent).where(TimelineEvent.event_type.in_(
            ["tool_call", "verification_passed", "verification_failed", "run_started", "run_completed", "run_failed"]))
            .order_by(TimelineEvent.created_at.desc()).limit(30)).all()
        return {
            "runs": [{"id": r.id, "issue_id": r.issue_id, "title": r.issue.title,
                      "agent": r.agent.name, "status": r.status.value} for r in runs],
            "events": [{"issue_id": e.issue_id, "type": e.event_type, "detail": e.detail[-4000:]} for e in events],
        }


def _spawn(cwd):
    if os.name != "nt":
        raise RuntimeError("Interactive terminals currently require Windows")
    from winpty import PtyProcess
    shell = shutil.which("powershell.exe")
    if not shell:
        raise RuntimeError("Windows PowerShell could not be found")
    env = os.environ.copy()
    scrub_inherited_environment(env)
    # Hagent records its own transcript; do not mix commands with the user's
    # unrelated ConsoleHost history (which may also be locked by another shell).
    return PtyProcess.spawn([shell, "-NoLogo", "-NoProfile", "-NoExit", "-Command",
                             "Set-PSReadLineOption -HistorySaveStyle SaveNothing"], cwd=cwd, env=env)


def _stop(process):
    import psutil
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        parent.terminate()
        _, alive = psutil.wait_procs(children + [parent], timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(alive, timeout=3)
    except psutil.NoSuchProcess:
        pass
    finally:
        try:
            process.close(force=True)
        except (OSError, EOFError):
            pass


def _save(session_id, transcript, status=None):
    values = {"transcript": transcript[-OUTPUT_LIMIT:]}
    if status:
        values.update(status=status, ended_at=datetime.now(timezone.utc))
    with get_session(scoped=False) as s:
        s.execute(update(TerminalSession).where(TerminalSession.id == session_id).values(**values))
        s.commit()


def recover_terminal_sessions():
    with get_session(scoped=False) as s:
        s.execute(update(TerminalSession).where(TerminalSession.status == "running")
                  .values(status="interrupted", ended_at=datetime.now(timezone.utc)))
        s.commit()


@router.websocket("/ws/terminal")
async def terminal_socket(ws: WebSocket):
    try:
        owner = _owner(ws)
    except HTTPException:
        await ws.close(code=1008)
        return
    # Browser connections must supply a same-origin Origin, even in local mode.
    from urllib.parse import urlsplit
    origin = urlsplit(ws.headers.get("origin", ""))
    if origin.netloc != ws.headers.get("host") or origin.scheme not in {"http", "https"}:
        await ws.close(code=1008)
        return
    with _lock:
        if _active.get(owner, 0) >= MAX_TERMINALS_PER_OWNER:
            await ws.close(code=1008)
            return
        _active[owner] = _active.get(owner, 0) + 1
    token = request_workspace.set(ws.cookies.get("workspace_id"))
    process = None
    row_id = None
    transcript = ""
    tasks = []
    try:
        await ws.accept()
        cwd = resolve_working_directory(ws.query_params.get("cwd") or str(Path.home()))
        process = await asyncio.to_thread(_spawn, cwd)
        with get_session() as s:
            row = TerminalSession(workspace_id=get_active_workspace(s).id, owner_id=owner, cwd=cwd)
            s.add(row)
            s.commit()
            row_id = row.id
        await ws.send_json({"type": "ready", "id": row_id})

        async def output():
            nonlocal transcript
            while True:
                try:
                    chunk = await asyncio.to_thread(process.read, 4096)
                except (EOFError, OSError):
                    return
                transcript = (transcript + chunk)[-OUTPUT_LIMIT:]
                await ws.send_json({"type": "output", "data": chunk})

        async def inputs():
            while True:
                message = await ws.receive_json()
                if message.get("type") == "input":
                    data = message.get("data", "")
                    if not isinstance(data, str) or len(data) > 32768:
                        raise ValueError("Terminal input is too large")
                    await asyncio.to_thread(process.write, data)
                elif message.get("type") == "resize":
                    await asyncio.to_thread(process.setwinsize,
                        max(5, min(120, int(message["rows"]))), max(20, min(300, int(message["cols"]))))
                elif message.get("type") == "stop":
                    return

        async def checkpoint():
            while process.isalive():
                await asyncio.sleep(2)
                await asyncio.to_thread(_save, row_id, transcript)

        tasks = [asyncio.create_task(f()) for f in (output, inputs, checkpoint)]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        for task in tasks:
            task.cancel()
        try:
            status = "closed"
            if process:
                try:
                    await asyncio.to_thread(_stop, process)
                except Exception:
                    status = "cleanup_failed"
                    log.exception("Could not stop terminal session %s", row_id)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if row_id:
                try:
                    await asyncio.to_thread(_save, row_id, transcript, status)
                except Exception:
                    log.exception("Could not save terminal session %s", row_id)
        finally:
            # A storage or process error must not permanently lock this owner out.
            with _lock:
                remaining = _active.get(owner, 1) - 1
                if remaining:
                    _active[owner] = remaining
                else:
                    _active.pop(owner, None)
            request_workspace.reset(token)
            try:
                await ws.close()
            except RuntimeError:
                pass
