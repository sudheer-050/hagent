"""Server side of remote CLI access: run Hagent commands on behalf of an authenticated client.

Commands run in a short-lived child process on the server, so file-path arguments (attachments,
skill imports, repo paths) refer to the server machine.  Process isolation is important here:
Click's test runner temporarily replaces global stdout/stderr and is not safe beside background
agent threads in the web process.
"""

import threading
import subprocess
import sys

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

# Commands that manage the host itself, or block forever, never run remotely.
DENIED_REMOTE = {"auth", "serve", "warm", "remote", "worker", "daemon"}
_run_lock = threading.Lock()
_LOCK_WAIT_SECONDS = 60


class CliRequest(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=200)


def _require_user(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        # Only reachable before any account exists; remote execution needs a real identity.
        raise HTTPException(403, "Create an account first: hagent auth user-create")
    return user


@router.get("/api/whoami")
def whoami(request: Request):
    user = _require_user(request)
    return {"username": user.username, "role": user.role, "kind": user.kind}


@router.post("/api/cli")
def run_command(payload: CliRequest, request: Request):
    _require_user(request)
    argv = payload.argv
    if sum(len(a) for a in argv) > 65536:
        raise HTTPException(413, "Command too large")
    if argv[0] in DENIED_REMOTE:
        raise HTTPException(403, f"'{argv[0]}' commands can only be run on the machine that hosts Hagent")
    if not _run_lock.acquire(timeout=_LOCK_WAIT_SECONDS):
        raise HTTPException(503, "Another remote command is still running; try again shortly")
    try:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "hagent", *argv],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "stdout": exc.stdout or "",
                "stderr": (exc.stderr or "") + "Remote command timed out after 180 seconds.\n",
                "exit_code": 124,
            }
    finally:
        _run_lock.release()
    return {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}
