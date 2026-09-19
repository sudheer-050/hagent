"""Client side of remote CLI access: send `hagent <command>` to a Hagent server on another machine.

`python -m hagent remote login --url https://host:8000` stores the server address and an API token
(create one on the server with `hagent auth token-create`). After that, every `python -m hagent`
command runs on that server, and `HAGENT_LOCAL=1` forces a command to run on this machine instead.

This module must stay stdlib-only so the client path starts fast.
"""

import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("HAGENT_REMOTE_PATH", str(Path.home() / ".hagent" / "remote.json")))
# These always run on this machine, never against the remote server.
LOCAL_ONLY = {"auth", "serve", "warm", "remote", "worker", "daemon"}
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
REQUEST_TIMEOUT = 3600  # agent runs can be long


class RemoteError(RuntimeError):
    pass


def load_config() -> dict | None:
    url, token = os.environ.get("HAGENT_REMOTE_URL"), os.environ.get("HAGENT_REMOTE_TOKEN")
    if url and token:
        return {"url": url.rstrip("/"), "token": token}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {"url": str(data["url"]).rstrip("/"), "token": str(data["token"])} if data.get("url") and data.get("token") else None
    except (OSError, ValueError, KeyError):
        return None


def is_configured() -> bool:
    return os.environ.get("HAGENT_LOCAL") != "1" and load_config() is not None


def _request(config: dict, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        config["url"] + path, data=data, method=method,
        headers={"Authorization": f"Bearer {config['token']}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
            detail = body.get("detail") or body.get("error") or ""
        except (ValueError, AttributeError):
            detail = ""
        if exc.code == 401:
            raise RemoteError("The server rejected this token (invalid, expired or revoked). Run: hagent remote login") from exc
        raise RemoteError(f"Server error {exc.code}" + (f": {detail}" if detail else "")) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RemoteError(f"Could not reach {config['url']}: {getattr(exc, 'reason', exc)}") from exc


def run_remote(argv: list[str]) -> int:
    """Run argv on the configured server and print its output. Returns the exit code."""
    try:
        reply = _request(load_config(), "POST", "/api/cli", {"argv": argv})
    except RemoteError as exc:
        print(f"hagent: {exc}", file=sys.stderr)
        return 1
    if reply.get("stdout"):
        sys.stdout.write(reply["stdout"])
        sys.stdout.flush()
    if reply.get("stderr"):
        sys.stderr.write(reply["stderr"])
        sys.stderr.flush()
    return int(reply.get("exit_code", 1))


def _flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def remote_command(args: list[str]) -> int:
    action = args[0] if args else ""
    if action == "login":
        url = (_flag(args, "--url") or input("Server URL (e.g. https://hagent.example:8000): ")).strip().rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            print("hagent: the URL must start with http:// or https://", file=sys.stderr)
            return 2
        if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK and "--allow-insecure" not in args:
            print(
                "hagent: refusing to send a token over plain http. Use https, or pass --allow-insecure "
                "if this is a network you already trust (for example Tailscale).",
                file=sys.stderr,
            )
            return 2
        token = _flag(args, "--token") or getpass.getpass("API token: ").strip()
        try:
            who = _request({"url": url, "token": token}, "GET", "/api/whoami")
        except RemoteError as exc:
            print(f"hagent: {exc}", file=sys.stderr)
            return 1
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps({"url": url, "token": token}), encoding="utf-8")
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass
        print(f"Logged in to {url} as {who.get('username')} ({who.get('role')}). Commands now run on that server.")
        return 0
    if action == "logout":
        CONFIG_PATH.unlink(missing_ok=True)
        print("Remote login removed. Commands run on this machine again.")
        return 0
    if action == "status":
        config = load_config()
        if not config:
            print("Not logged in to a remote server; commands run on this machine.")
            return 0
        try:
            who = _request(config, "GET", "/api/whoami")
        except RemoteError as exc:
            print(f"Configured for {config['url']}, but: {exc}")
            return 1
        print(f"Logged in to {config['url']} as {who.get('username')} ({who.get('role')})")
        return 0
    print("usage: hagent remote [login --url URL [--token TOKEN] [--allow-insecure] | logout | status]", file=sys.stderr)
    return 2
