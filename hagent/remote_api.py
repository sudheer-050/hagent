"""Server side of remote CLI access: run Hagent commands on behalf of an authenticated client.

Commands run in this server process, so file-path arguments (attachments, skill imports, repo
paths) refer to the server machine, and `workspace switch` changes the server's active workspace,
exactly as it would if typed there.
"""

import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

# Commands that manage the host itself, or block forever, never run remotely.
DENIED_REMOTE = {"auth", "serve", "warm", "remote", "worker", "daemon"}
_run_lock = threading.Lock()
_LOCK_WAIT_SECONDS = 60
_init_wrapped = False


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


def _cli_module():
    """Import the CLI once and make its per-command database setup run only once per process."""
    global _init_wrapped
    from hagent import cli as cli_module

    if not _init_wrapped:
        real_init_db = cli_module.init_db
        done = []

        def init_db_once():
            if not done:
                real_init_db()
                done.append(True)

        cli_module.init_db = init_db_once
        _init_wrapped = True
    return cli_module


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
        from click.testing import CliRunner

        result = CliRunner().invoke(_cli_module().cli, argv, prog_name="hagent")
    finally:
        _run_lock.release()
    stderr = result.stderr
    exit_code = result.exit_code
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        import traceback

        stderr += "".join(traceback.format_exception(*result.exc_info))
        exit_code = 1
    return {"stdout": result.stdout, "stderr": stderr, "exit_code": exit_code}
