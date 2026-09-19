"""`hagent worker start`: run model calls for a Hagent server on this device.

The worker decides what may run here, not the server:
* only installed-assistant runtimes in ALLOWED_TYPES run (never generic commands or API keys);
* the executable is always the one found on this machine, never a path the server sends;
* the working directory is --workdir, never a path the server sends;
* the server's request for terminal access is ignored unless --allow-terminal is given.
"""

import threading
import time

from hagent import remote

ALLOWED_TYPES = ("claude_code", "codex_cli", "opencode_cli", "antigravity_cli", "gemini_cli")
MAX_TIMEOUT = 3600
RETRY_DELAY = 5.0


def execute_job(job: dict, workdir: str | None, allow_terminal: bool, types: list[str]) -> dict:
    """Run one job with the local adapter. Always returns a result dict; failures become 'error'."""
    from hagent.adapters import get_runtime_class
    from hagent.models import RuntimeType

    runtime_type = job.get("runtime_type")
    if runtime_type not in ALLOWED_TYPES or runtime_type not in types:
        return {"error": f"This worker does not run '{runtime_type}' jobs"}
    options = job.get("options") or {}
    config: dict = {"timeout": min(int(options.get("timeout") or 600), MAX_TIMEOUT)}
    if workdir:
        config["working_directory"] = workdir
    if allow_terminal and options.get("terminal_enabled"):
        config["terminal_enabled"] = True
    for key in ("resume_session_id", "reasoning_effort", "max_tokens"):
        if options.get(key):
            config[key] = options[key]
    try:
        adapter = get_runtime_class(RuntimeType(runtime_type))(model=job.get("model") or "default", config=config)
        result = adapter.run(job.get("prompt", ""), job.get("context", ""))
    except Exception as exc:  # the server should see why, whatever the adapter raised
        return {"error": str(exc)[:5000] or exc.__class__.__name__}
    return {"output": result.output, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "session_id": result.session_id}


def run_worker(config: dict, name: str, workdir: str | None = None, allow_terminal: bool = False, types: list[str] | None = None,
               stop: threading.Event | None = None, log=print, wait: float = 20.0) -> None:
    """Poll the server for jobs until `stop` is set. `config` is {"url", "token"}."""
    stop = stop or threading.Event()
    types = [t for t in (types or list(ALLOWED_TYPES)) if t in ALLOWED_TYPES]
    log(f"Worker '{name}' offering {', '.join(types)} to {config['url']}")
    while not stop.is_set():
        try:
            reply = remote._request(config, "POST", "/api/worker/claim", {"name": name, "types": types, "wait": wait})
        except remote.RemoteError as exc:
            log(f"Cannot reach the server ({exc}); retrying in {int(RETRY_DELAY)}s")
            stop.wait(RETRY_DELAY)
            continue
        job = reply.get("job")
        if not job:
            continue
        log(f"Running {job['runtime_type']} job {job['id'][:8]}")
        outcome = execute_job(job, workdir, allow_terminal, types)
        try:
            remote._request(config, "POST", f"/api/worker/jobs/{job['id']}/complete", outcome)
            log(f"Job {job['id'][:8]} {'failed: ' + outcome['error'][:120] if outcome.get('error') else 'done'}")
        except remote.RemoteError as exc:
            log(f"Could not report job {job['id'][:8]}: {exc}")
            time.sleep(1)
