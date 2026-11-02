"""Long-lived native CLI sessions, isolated by agent and runtime."""
import atexit
from contextlib import ExitStack
from contextvars import copy_context
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

from hagent.adapters.base import RuntimeResult, ResumableError, CLIHistoryMissing
from hagent.managed_process import _terminate_tree, activity
from hagent.mcp_bridge import serve_tools
from hagent.runtime_env import BACKGROUND_PROCESS_FLAGS

log = logging.getLogger(__name__)
SUPPORTED = {'codex_cli', 'claude_code'}
# ponytail: four idle sessions bound RAM; active agents are never evicted.
IDLE_LIMIT = 4
_pool = {}
_pool_lock = threading.Lock()
_enabled = False
_stopping = False


class Session:
    def __init__(self, kind, model, config, tools):
        self.kind, self.model, self.config = kind, model, dict(config)
        self.tools = tools or []
        self.lock = threading.Lock()
        self.process = None
        self.resources = ExitStack()
        self.messages = queue.Queue(maxsize=10000)
        self.stderr = ''
        self.executor = None
        self.session_id = config.get('resume_session_id')
        self.thread_loaded = False
        self.ident = 0
        self.used_at = time.monotonic()
        self.cwd = None
        self.closed = False
        self.ready = False
        self.events = []

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                self.messages.put(value, timeout=5)
        except (OSError, ValueError, queue.Full):
            pass
        finally:
            try:
                self.messages.put_nowait(None)
            except queue.Full:
                pass

    def _read_errors(self):
        try:
            for line in self.process.stderr:
                self.stderr = (self.stderr + line)[-8000:]
        except (OSError, ValueError):
            pass

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def receive(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('CLI response timed out')
        try:
            message = self.messages.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError('CLI response timed out') from None
        if message is None:
            raise RuntimeError('CLI session exited: ' + self.stderr[-1500:])
        return message

    def request(self, method, params, deadline):
        self.ident += 1
        ident = self.ident
        if self.kind == 'codex_cli':
            self.send({'id': ident, 'method': method, 'params': params})
        else:
            self.send({'type': 'control_request', 'request_id': str(ident),
                       'request': {'subtype': method, **params}})
        while True:
            message = self.receive(deadline)
            if self.kind == 'codex_cli' and message.get('id') == ident and 'method' not in message:
                if 'error' in message:
                    detail = str(message['error'].get('message', ''))
                    if method == 'thread/resume' and 'thread not found:' in detail.lower():
                        raise CLIHistoryMissing(detail)
                    raise RuntimeError(str(message['error']))
                return message.get('result', {})
            response = message.get('response', {})
            if self.kind == 'claude_code' and response.get('request_id') == str(ident):
                if response.get('subtype') != 'success':
                    raise RuntimeError(str(response.get('error', 'CLI initialization failed')))
                return response.get('response', {})
            self.reject_request(message)
            if 'id' not in message and message.get('type') != 'control_request':
                self.events.append(message)

    def reject_request(self, message):
        # Never auto-approve a new CLI permission request. Agent tools have their
        # existing explicit allow list and all other prompts are rejected.
        if self.kind == 'codex_cli' and 'id' in message and 'method' in message:
            self.send({'id': message['id'], 'error': {'code': -32601, 'message': 'Interactive approval is unavailable'}})
        elif message.get('type') == 'control_request':
            self.send({'type': 'control_response', 'response': {
                'subtype': 'error', 'request_id': message['request_id'],
                'error': 'Interactive approval is unavailable'}})

    def prepare(self):
        if self.closed:
            raise RuntimeError('CLI session is closed')
        if self.process and self.process.poll() is None:
            return
        if self.process:
            raise RuntimeError('CLI session exited; retry to start a new session')
        from hagent.runtime_discovery import cli_command
        command = self.config.get('command') or cli_command(self.kind)
        if not command:
            raise RuntimeError('The configured CLI is not installed')
        if self.config.get('terminal_enabled'):
            self.cwd = self.config.get('working_directory') or str(Path.home())
        else:
            self.cwd = self.resources.enter_context(tempfile.TemporaryDirectory(prefix='hagent-ready-'))

        def execute(name, arguments):
            if self.executor is None:
                raise RuntimeError('This agent has no active turn')
            return self.tool_context.run(self.executor, name, arguments)

        bridge = self.resources.enter_context(serve_tools(self.tools, execute)) if self.tools else None
        stub = str(Path(__file__).with_name('mcp_bridge_stub.py'))
        if self.kind == 'codex_cli':
            args = [command, 'app-server']
            if bridge:
                args += ['-c', f'mcp_servers.hagent.command={json.dumps(sys.executable)}',
                         '-c', f'mcp_servers.hagent.args={json.dumps([stub, bridge])}']
        else:
            args = [command, '--print', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose']
            if self.model and self.model != 'default':
                args += ['--model', self.model]
            if self.config.get('reasoning_effort'):
                args += ['--effort', self.config['reasoning_effort']]
            if self.session_id:
                args += ['--resume', self.session_id]
            if bridge:
                path = Path(self.resources.enter_context(tempfile.TemporaryDirectory(prefix='hagent-ready-mcp-'))) / 'mcp.json'
                path.write_text(json.dumps({'mcpServers': {'hagent': {'command': sys.executable, 'args': [stub, bridge]}}}), encoding='utf-8')
                args += ['--mcp-config', str(path), '--strict-mcp-config', '--allowedTools',
                         *['mcp__hagent__' + tool['name'] for tool in self.tools]]
            if self.config.get('terminal_enabled'):
                args += ['--dangerously-skip-permissions']
        options = {'creationflags': BACKGROUND_PROCESS_FLAGS}
        if os.name == 'nt':
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            options = {'creationflags': subprocess.CREATE_NEW_CONSOLE, 'startupinfo': startup}
        self.process = subprocess.Popen(args, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', **options)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.error_reader = threading.Thread(target=self._read_errors, daemon=True)
        self.reader.start(); self.error_reader.start()
        deadline = time.monotonic() + 20
        if self.kind == 'codex_cli':
            self.request('initialize', {'clientInfo': {'name': 'hagent', 'version': '1.0'}}, deadline)
            self.send({'method': 'initialized', 'params': {}})
        else:
            self.request('initialize', {}, deadline)
        self.ready = True

    def run(self, prompt, context, config, executor):
        with self.lock, activity(config.get('_terminal_monitor')) as append:
            deadline = time.monotonic() + float(config.get('timeout', 600))
            try:
                self.prepare()
                self.executor = executor
                self.tool_context = copy_context()
                append('CLI ready. Processing request...\n')
                text = f'{context}\n\n{prompt}' if context else prompt
                if self.kind == 'codex_cli':
                    params = {'cwd': self.cwd, 'approvalPolicy': 'never',
                              'sandbox': 'danger-full-access' if config.get('terminal_enabled') else 'read-only'}
                    if self.model and self.model != 'default':
                        params['model'] = self.model
                    resume = config.get('resume_session_id')
                    if not self.thread_loaded or not resume or resume != self.session_id:
                        if resume:
                            params.update(threadId=resume, excludeTurns=True)
                        result = self.request('thread/resume' if resume else 'thread/start', params, deadline)
                        self.session_id = result['thread']['id']
                        self.thread_loaded = True
                    turn = {'threadId': self.session_id, 'input': [{'type': 'text', 'text': text}]}
                    if config.get('reasoning_effort'):
                        turn['effort'] = config['reasoning_effort']
                    self.request('turn/start', turn, deadline)
                else:
                    self.send({'type': 'user', 'session_id': self.session_id or '',
                               'parent_tool_use_id': None,
                               'message': {'role': 'user', 'content': text}})
                output, usage = [], None
                while True:
                    message = self.events.pop(0) if self.events else self.receive(deadline)
                    self.reject_request(message)
                    if self.kind == 'claude_code':
                        self.session_id = message.get('session_id') or self.session_id
                        if message.get('type') == 'assistant':
                            for item in message.get('message', {}).get('content', []):
                                if item.get('type') == 'text':
                                    append(item.get('text', '') + '\n')
                        if message.get('type') != 'result':
                            continue
                        if message.get('is_error') or str(message.get('subtype', '')).startswith('error'):
                            raise ResumableError(str(message.get('result') or message.get('errors')), self.session_id)
                        return RuntimeResult(output=message.get('result', ''), raw=message, session_id=self.session_id)
                    params = message.get('params', {})
                    if params.get('threadId') != self.session_id:
                        continue
                    method = message.get('method')
                    if method == 'item/completed':
                        item = params.get('item', {})
                        if item.get('type') == 'agentMessage':
                            output.append(item.get('text', ''))
                            append(item.get('text', '') + '\n')
                    elif method == 'thread/tokenUsage/updated':
                        usage = params.get('tokenUsage', {}).get('last')
                    elif method == 'turn/completed':
                        turn = params.get('turn', {})
                        if turn.get('status') != 'completed':
                            raise ResumableError(str(turn.get('error') or turn.get('status')), self.session_id)
                        counts = {'input_tokens': usage.get('inputTokens'), 'output_tokens': usage.get('outputTokens')} if usage else None
                        return RuntimeResult(output='\n'.join(output), raw={'usage': counts}, session_id=self.session_id)
            except Exception:
                self.close()
                raise
            finally:
                self.executor = None
                self.used_at = time.monotonic()

    def close(self):
        self.closed = True
        self.ready = False
        if self.process:
            _terminate_tree(self.process)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for name in ('reader', 'error_reader'):
                thread = getattr(self, name, None)
                if thread:
                    thread.join(timeout=2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream:
                    stream.close()
        self.resources.close()


def enabled(config):
    return _enabled and bool(config.get('_persistent_key')) and not config.get('worker')


def acquire(kind, model, config, tools):
    key = config['_persistent_key']
    spec = [kind, model, config.get('command'), bool(config.get('terminal_enabled')),
            config.get('working_directory') if config.get('terminal_enabled') else None,
            config.get('reasoning_effort'), tools or []]
    signature = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    with _pool_lock:
        if _stopping:
            raise RuntimeError('Hagent is shutting down')
        entry = _pool.get(key)
        if entry and (entry[0] != signature or entry[1].closed or
                      (entry[1].process and entry[1].process.poll() is not None) or
                      (kind == 'claude_code' and entry[1].session_id
                       and entry[1].session_id != config.get('resume_session_id'))):
            if entry[1].lock.locked():
                raise RuntimeError('This agent already has an active CLI turn')
            entry[1].close()
            _pool.pop(key)
            entry = None
        if not entry:
            idle = sorted(((k, row[1]) for k, row in _pool.items() if not row[1].lock.locked()), key=lambda row: row[1].used_at)
            if len(_pool) >= IDLE_LIMIT and idle:
                old_key, old = idle[0]
                _pool.pop(old_key); old.close()
            entry = (signature, Session(kind, model, config, tools))
            _pool[key] = entry
        return entry[1]


def run(kind, model, config, prompt, context, tools, executor):
    return acquire(kind, model, config, tools).run(prompt, context, config, executor)


def probe(kind, model, runtime_id, config, prompt):
    """Use a dedicated reusable conversation so checks never touch an agent's chat."""
    config = {**config, '_persistent_key': f'_runtime:{runtime_id}'}
    if model == 'default':
        config.pop('reasoning_effort', None)
    with _pool_lock:
        entry = _pool.get(config['_persistent_key'])
        if entry and not entry[1].closed and entry[1].session_id:
            config['resume_session_id'] = entry[1].session_id
    return run(kind, model, config, prompt, 'Do not use tools. Reply only OK.', [], None)


def ready(runtime_id):
    with _pool_lock:
        return any(key.endswith(':' + runtime_id) and row.ready and not row.closed
                   and row.process is not None and row.process.poll() is None
                   for key, (_, row) in _pool.items())


def stop():
    global _enabled, _stopping
    _enabled, _stopping = False, True
    with _pool_lock:
        rows = list(_pool.values()); _pool.clear()
    for _, session in rows:
        session.close()


def start():
    """Preload recent agents without sending a paid model request or blocking startup."""
    global _enabled, _stopping
    _enabled, _stopping = True, False

    def preload():
        from sqlalchemy import select
        from hagent.db import get_session
        from hagent.models import Agent, ChatThread, Runtime
        from hagent.engine import _build_agent_tools
        with get_session(scoped=False) as db:
            recent = db.scalars(select(ChatThread).order_by(ChatThread.updated_at.desc()).limit(20)).all()
            candidates = [(db.get(Agent, t.agent_id), t.runtime_id, t.session_id) for t in recent]
            candidates += [(a, None, None) for a in db.scalars(select(Agent).where(Agent.archived.is_(False))).all()]
            # One recent conversation per runtime at startup, rather than a
            # separate process for every agent. Other agents start on demand.
            candidates.sort(key=lambda row: 0 if row[2] else 1)
            provider_first, providers = [], set()
            for candidate in candidates:
                agent, pinned, _ = candidate
                if not agent or agent.archived:
                    continue
                key = pinned or (agent.runtime_id if agent else None)
                if key not in providers:
                    provider_first.append(candidate)
                providers.add(key)
            candidates = provider_first
            seen = set()
            for agent, pinned, resume in candidates:
                if _stopping or len(seen) >= IDLE_LIMIT:
                    break
                if not agent or agent.archived or agent.id in seen:
                    continue
                runtime = db.get(Runtime, pinned or agent.runtime_id)
                if not runtime or runtime.archived or runtime.type.value not in SUPPORTED:
                    continue
                config = json.loads(runtime.config_json or '{}')
                if config.get('worker'):
                    continue
                config.update(_persistent_key=f'{agent.id}:{runtime.id}', terminal_enabled=agent.terminal_enabled,
                              working_directory=agent.terminal_working_directory, resume_session_id=resume)
                if runtime.model == 'default':
                    config.pop('reasoning_effort', None)
                seen.add(agent.id)
                session = None
                try:
                    tools, _, _, _ = _build_agent_tools(agent, runtime, (agent.id,), {'limit': 8, 'used': 0})
                    if _stopping:
                        break
                    session = acquire(runtime.type.value, runtime.model, config, tools)
                    with session.lock:
                        session.prepare()
                    if _stopping:
                        session.close()
                except Exception as exc:
                    if session is not None:
                        session.close()
                    log.warning('CLI startup for agent %s failed (%s)', agent.id, type(exc).__name__)

            # Connected CLIs without agents still get a local readiness service.
            for runtime in db.scalars(select(Runtime).where(Runtime.archived.is_(False))).all():
                if _stopping or len(_pool) >= IDLE_LIMIT:
                    break
                if runtime.type.value not in SUPPORTED:
                    continue
                config = json.loads(runtime.config_json or '{}')
                if config.get('worker'):
                    continue
                config.update(_persistent_key=f'_runtime:{runtime.id}', terminal_enabled=False)
                if runtime.model == 'default':
                    config.pop('reasoning_effort', None)
                session = None
                try:
                    session = acquire(runtime.type.value, runtime.model, config, [])
                    with session.lock:
                        session.prepare()
                    if _stopping:
                        session.close()
                except Exception as exc:
                    if session is not None:
                        session.close()
                    log.warning('CLI startup for runtime %s failed (%s)', runtime.id, type(exc).__name__)

    thread = threading.Thread(target=preload, name='hagent-cli-preload', daemon=True)
    thread.start()
    return thread


atexit.register(stop)
