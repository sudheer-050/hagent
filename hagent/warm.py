"""Optional warm CLI process: keeps Hagent's imports loaded so commands skip interpreter startup.

`python -m hagent <args>` sends the command to a running warm process over a
loopback socket and prints its output; if no warm process is running, or
anything looks unsafe or unusual, it silently runs the command the normal way.

Security model: the server binds 127.0.0.1 only and every request must carry a
random token stored in the user's ~/.hagent/warm.json. This is a convenience
for one user on one machine, not an authentication boundary.

This module's top level must stay stdlib-only so the client path is fast.
"""

import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

STATE_PATH = Path(os.environ.get("HAGENT_WARM_PATH", str(Path.home() / ".hagent" / "warm.json")))
MAX_MESSAGE = 4 * 1024 * 1024
# Long-running or process-owning commands always take the normal path.
NEVER_WARM = {"daemon", "warm", "auth", "remote", "worker", "serve"}


# --- wire protocol: 4-byte big-endian length + JSON -------------------------

def _send(sock: socket.socket, message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    sock.sendall(len(data).to_bytes(4, "big") + data)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    while size:
        chunk = sock.recv(min(size, 65536))
        if not chunk:
            raise ConnectionError("connection closed")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _recv(sock: socket.socket) -> dict:
    size = int.from_bytes(_recv_exact(sock, 4), "big")
    if size > MAX_MESSAGE:
        raise ValueError("message too large")
    return json.loads(_recv_exact(sock, size))


def _effective_paths(cwd: str, env) -> tuple[str, str]:
    """Where a command run from `cwd` with `env` would put its database and config."""
    db = os.path.abspath(os.path.join(cwd, env.get("HAGENT_DB_PATH", "hagent.db")))
    config = env.get("HAGENT_CONFIG_PATH") or str(Path.home() / ".hagent" / "config.json")
    return db, os.path.abspath(os.path.join(cwd, config))


def _code_mtime() -> float:
    root = Path(__file__).resolve().parent
    return max((p.stat().st_mtime for p in root.rglob("*.py")), default=0.0)


# --- client -----------------------------------------------------------------

def _read_state() -> dict | None:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _request(state: dict, message: dict, timeout: float | None = None) -> dict:
    with socket.create_connection(("127.0.0.1", state["port"]), timeout=0.5) as sock:
        sock.settimeout(timeout)
        _send(sock, {"token": state["token"], **message})
        return _recv(sock)


def try_warm(argv: list[str]) -> int | None:
    """Run argv in the warm process. Returns an exit code, or None to fall back."""
    if not argv or argv[0] in NEVER_WARM:
        return None
    state = _read_state()
    if not state:
        return None
    cwd = os.getcwd()
    if _effective_paths(cwd, os.environ) != (state.get("db_path"), state.get("config_path")):
        return None  # would read a different database than the warm process holds
    try:
        reply = _request(state, {"op": "run", "argv": argv, "cwd": cwd, "env": dict(os.environ)})
    except (OSError, ValueError):
        return None
    if reply.get("fallback"):
        return None
    if reply.get("stdout"):
        sys.stdout.write(reply["stdout"])
        sys.stdout.flush()
    if reply.get("stderr"):
        sys.stderr.write(reply["stderr"])
        sys.stderr.flush()
    return int(reply.get("exit_code", 1))


# --- server -----------------------------------------------------------------

class WarmServer:
    def __init__(self, state_path: Path = STATE_PATH):
        self.state_path = Path(state_path)
        self.token = secrets.token_hex(32)
        self.run_lock = threading.Lock()
        self.stopping = threading.Event()
        self.close_lock = threading.Lock()
        self.closed = False
        self.started_mtime = _code_mtime()
        self.sock: socket.socket | None = None

    def start(self) -> dict:
        # Heavy imports happen once, here, instead of on every command.
        from hagent import cli as cli_module

        self.cli_module = cli_module
        real_init_db = cli_module.init_db
        initialized = []

        def init_db_once():
            if not initialized:
                real_init_db()
                initialized.append(True)

        cli_module.init_db = init_db_once

        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        db_path, config_path = _effective_paths(os.getcwd(), os.environ)
        state = {"port": self.sock.getsockname()[1], "token": self.token, "pid": os.getpid(), "db_path": db_path, "config_path": config_path}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass
        return state

    def serve_forever(self) -> None:
        self.sock.settimeout(0.5)
        while not self.stopping.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
        self.close()

    def close(self) -> None:
        self.stopping.set()
        with self.close_lock:
            if self.closed:
                return
            self.closed = True
        try:
            if self.sock:
                self.sock.close()
        finally:
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if state.get("token") == self.token:
                    self.state_path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass  # already removed (e.g. by `warm stop`) or briefly locked

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(5)
                message = _recv(conn)
                if not hmac.compare_digest(str(message.get("token", "")), self.token):
                    return
                conn.settimeout(None)
                op = message.get("op")
                if op == "ping":
                    _send(conn, {"ok": True, "pid": os.getpid()})
                elif op == "stop":
                    _send(conn, {"ok": True})
                    self.stopping.set()
                elif op == "run":
                    _send(conn, self._run(message))
            except (OSError, ValueError):
                return

    def _run(self, message: dict) -> dict:
        if _code_mtime() > self.started_mtime:
            self.stopping.set()  # loaded code is out of date; let the caller use fresh code
            return {"fallback": True}
        argv, cwd, env = message.get("argv"), message.get("cwd"), message.get("env")
        if not (isinstance(argv, list) and all(isinstance(a, str) for a in argv) and argv and argv[0] not in NEVER_WARM):
            return {"fallback": True}
        if not (isinstance(cwd, str) and isinstance(env, dict)):
            return {"fallback": True}
        if _effective_paths(cwd, env) != _effective_paths(os.getcwd(), os.environ):
            return {"fallback": True}
        if not self.run_lock.acquire(blocking=False):
            return {"fallback": True}  # another command is running; don't queue behind it
        saved_env, saved_cwd = dict(os.environ), os.getcwd()
        try:
            from click.testing import CliRunner

            os.chdir(cwd)
            os.environ.clear()
            os.environ.update({str(k): str(v) for k, v in env.items()})
            result = CliRunner().invoke(self.cli_module.cli, argv, prog_name="hagent")
            stderr = result.stderr
            if result.exception is not None and not isinstance(result.exception, SystemExit):
                stderr += "".join(traceback.format_exception(*result.exc_info))
                return {"stdout": result.stdout, "stderr": stderr, "exit_code": 1}
            return {"stdout": result.stdout, "stderr": stderr, "exit_code": result.exit_code}
        finally:
            os.environ.clear()
            os.environ.update(saved_env)
            os.chdir(saved_cwd)
            self.run_lock.release()


# --- management commands (`hagent warm start|stop|status`) ------------------

def _warm_command(action: str) -> int:
    state = _read_state()
    if action == "serve":
        server = WarmServer()
        server.start()
        server.serve_forever()
        return 0
    if action == "start":
        if state and _alive(state):
            print(f"warm process already running (pid {state['pid']})")
            return 0
        log = STATE_PATH.parent / "warm.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        with open(log, "ab") as out:
            subprocess.Popen([sys.executable, "-m", "hagent", "warm", "serve"], stdout=out, stderr=out, stdin=subprocess.DEVNULL, creationflags=flags, cwd=os.getcwd())
        for _ in range(100):
            time.sleep(0.15)
            state = _read_state()
            if state and _alive(state):
                print(f"warm process started (pid {state['pid']})")
                return 0
        print(f"warm process did not start; see {log}", file=sys.stderr)
        return 1
    if action == "stop":
        if not state:
            print("warm process is not running")
            return 0
        try:
            _request(state, {"op": "stop"}, timeout=5)
        except (OSError, ValueError):
            pass
        STATE_PATH.unlink(missing_ok=True)
        print("warm process stopped")
        return 0
    if action == "status":
        if state and _alive(state):
            print(f"warm process running (pid {state['pid']}, port {state['port']})")
            return 0
        print("warm process is not running")
        return 1
    print("usage: hagent warm [start|stop|status]", file=sys.stderr)
    return 2


def _alive(state: dict) -> bool:
    try:
        return bool(_request(state, {"op": "ping"}, timeout=2).get("ok"))
    except (OSError, ValueError):
        return False


def _use_utf8_console() -> None:
    """Windows' console defaults stdout/stderr to the system codepage (often
    cp1252), which can't represent characters an agent's own output may
    legitimately contain (arrows, smart quotes, em-dashes, etc.). Without this,
    any CLI command that echoes agent output - `issue rerun` in particular -
    raises UnicodeEncodeError and loses the report even though the underlying
    run completed and persisted successfully. errors="replace" is a deliberate
    fallback for the rare character neither UTF-8 output encoding nor the
    terminal font can render, so a display quirk never turns into a crash."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> None:
    _use_utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["warm"]:
        raise SystemExit(_warm_command(argv[1] if len(argv) > 1 else ""))
    if argv[:1] == ["remote"]:
        from hagent.remote import remote_command

        raise SystemExit(remote_command(argv[1:]))
    if argv and argv[0] not in NEVER_WARM:
        from hagent import remote

        if remote.is_configured():
            raise SystemExit(remote.run_remote(argv))
    code = try_warm(argv)
    if code is not None:
        raise SystemExit(code)
    from hagent.cli import cli

    cli(args=argv, prog_name="hagent")
