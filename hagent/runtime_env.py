"""Keep variables inherited from whatever launched Hagent out of agent runs.

When `hagent serve` is started from inside a Claude Code session, the server process inherits
that session's private variables: CLAUDECODE / CLAUDE_CODE_* markers (which make a child
`claude` think it is a nested sub-session) and often ANTHROPIC_BASE_URL / OPENAI_BASE_URL
pointing at a local proxy such as Headroom on 127.0.0.1:8787. Every agent run then inherits
them too. When that proxy is not running, every Claude Code and API run fails with
ECONNREFUSED - but only after the CLI has spent ~3 minutes retrying - and the backup runtime
fails the same way. That made Hagent look hung.

Paperclip and Multica avoid this by building each agent's environment explicitly instead of
passing the server's own environment through. Hagent keeps the simpler inherit-by-default
model but scrubs the known-bad variables once, at startup.
"""

import logging
import os
import socket
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Session markers a parent Claude Code process sets for its own children. They describe the
# parent's session, never a run Hagent starts, so they are always removed.
SESSION_MARKERS = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
)
# Provider endpoint overrides. Kept when they point somewhere reachable (a real gateway the
# operator set up on purpose); removed when they point at a loopback port nothing listens on.
BASE_URL_VARIABLES = ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL")
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _loopback_port_closed(url: str, timeout: float = 0.5) -> bool:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in _LOOPBACK:
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((parts.hostname, port), timeout=timeout):
            return False
    except OSError:
        return True


def scrub_inherited_environment(environ=None) -> list[str]:
    """Remove inherited variables that break agent runs. Returns the names removed.

    Set HAGENT_KEEP_INHERITED_ENV=1 to skip this entirely.
    """
    environ = os.environ if environ is None else environ
    if environ.get("HAGENT_KEEP_INHERITED_ENV", "").lower() in {"1", "true", "yes"}:
        return []
    removed = [name for name in SESSION_MARKERS if environ.pop(name, None) is not None]
    for name in BASE_URL_VARIABLES:
        value = environ.get(name)
        if value and _loopback_port_closed(value):
            environ.pop(name)
            removed.append(name)
            log.warning("Ignoring inherited %s=%s: nothing is listening there.", name, value)
    if removed:
        log.info("Removed inherited environment variables: %s", ", ".join(removed))
    return removed
