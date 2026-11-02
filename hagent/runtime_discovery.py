"""Live model discovery. Credentials never enter URLs or returned catalogs."""
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

import httpx

from hagent.runtime_env import BACKGROUND_PROCESS_FLAGS, scrub_inherited_environment

EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
API_PROVIDERS = {
    'openai': ('https://api.openai.com/v1', 'OPENAI_API_KEY'),
    'claude': ('https://api.anthropic.com/v1', 'ANTHROPIC_API_KEY'),
    'openrouter': ('https://openrouter.ai/api/v1', 'OPENROUTER_API_KEY'),
    'gemini': ('https://generativelanguage.googleapis.com/v1beta', 'GEMINI_API_KEY'),
    'xai': ('https://api.x.ai/v1', 'XAI_API_KEY'),
    'deepseek': ('https://api.deepseek.com', 'DEEPSEEK_API_KEY'),
    'mistral': ('https://api.mistral.ai/v1', 'MISTRAL_API_KEY'),
    'groq': ('https://api.groq.com/openai/v1', 'GROQ_API_KEY'),
    'together': ('https://api.together.xyz/v1', 'TOGETHER_API_KEY'),
    'fireworks': ('https://api.fireworks.ai/inference/v1', 'FIREWORKS_API_KEY'),
    'cerebras': ('https://api.cerebras.ai/v1', 'CEREBRAS_API_KEY'),
    'cohere': ('https://api.cohere.ai/compatibility/v1', 'COHERE_API_KEY'),
    'ollama': ('http://127.0.0.1:11434', ''),
    'openai_compatible': ('', ''),
}
CLI_PROVIDERS = {'openai': ('codex_cli', 'codex'), 'claude': ('claude_code', 'claude'), 'gemini': ('gemini_cli', 'gemini')}
_catalogs = {}
_lock = threading.Lock()


def provider_family(provider):
    return {'codex_cli': 'openai', 'claude_code': 'claude', 'gemini_cli': 'gemini'}.get(provider, provider)


def cli_command(provider):
    name = CLI_PROVIDERS.get(provider_family(provider), ('', ''))[1]
    found = shutil.which(name) if name else None
    if not found and name == 'codex':
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/OpenAI/Codex/bin/codex.exe'
        found = str(candidate) if candidate.is_file() else None
    return found


def connection_options(provider):
    command = cli_command(provider)
    return {'cli_available': bool(command), 'cli_label': 'Codex' if provider_family(provider) == 'openai' else 'Gemini' if provider_family(provider) == 'gemini' else 'Claude'}


def installed_clis():
    """Return supported assistants actually installed on this device."""
    return [{'provider':provider, 'label':connection_options(provider)['cli_label']}
            for provider in CLI_PROVIDERS if cli_command(provider)]


def _model(row, provider):
    ident = row.get('id') or row.get('model') or row.get('value') or row.get('name')
    if not isinstance(ident, str) or not ident:
        return None
    effort = (row.get('capabilities') or {}).get('effort') or row.get('effort') or {}
    reasoning = row.get('reasoning') or {}
    levels = row.get('supportedEffortLevels') or row.get('supported_reasoning_efforts') or effort.get('supported_levels')
    if levels is None and row.get('supportedReasoningEfforts'):
        levels = [x['reasoningEffort'] for x in row['supportedReasoningEfforts']]
    if levels is None and effort.get('supported'):
        levels = [name for name in EFFORTS if effort.get(name, {}).get('supported')]
    if levels is None and reasoning:
        levels = reasoning.get('supported_efforts')
        if levels is None:
            levels = [x for x in EFFORTS if x != 'none' or not reasoning.get('mandatory')]
    # The OpenAI Models API does not expose effort metadata. Use the common
    # documented reasoning tiers; provider-reported metadata takes precedence.
    if levels is None and provider == 'openai' and ident.startswith(('gpt-5', 'gpt-6', 'o1', 'o3', 'o4')):
        levels = ['high'] if '-pro' in ident else ['low', 'medium', 'high']
    levels = [x for x in EFFORTS if x in (levels or [])]
    return {'id': ident.removeprefix('models/') if provider == 'gemini' else ident, 'label': row.get('display_name') or row.get('displayName') or row.get('name') or ident,
            'efforts': levels, 'default_effort': row.get('defaultReasoningEffort') or reasoning.get('default_effort', ''),
            'is_default': bool(row.get('isDefault'))}


def api_models(provider, key, base_url=''):
    default, env_name = API_PROVIDERS[provider]
    base = (base_url or default).rstrip('/')
    parts = urlsplit(base)
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Enter a valid server URL.')
    if provider != 'ollama' and not key:
        raise ValueError('Enter an API key first.')
    headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'} if provider == 'claude' else {'x-goog-api-key': key} if provider == 'gemini' else ({'Authorization': 'Bearer ' + key} if key else {})
    path = '/api/tags' if provider == 'ollama' else '/models/user' if provider == 'openrouter' else '/models'
    if provider == 'cohere': base, path = 'https://api.cohere.com', '/v1/models'
    params = {'limit': 1000} if provider == 'claude' else {'output_modalities':'all'} if provider == 'openrouter' else {}
    rows, cursors = [], set()
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        while True:
            response = client.get(base + path, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            batch = data if isinstance(data, list) else data.get('models' if provider in ('ollama', 'gemini', 'cohere') else 'data', [])
            if not isinstance(batch, list):
                raise ValueError('Provider returned an invalid model list.')
            rows.extend(batch)
            if isinstance(data, list): break
            cursor = data.get('nextPageToken') or data.get('next_page_token') or data.get('next_cursor')
            if not cursor and data.get('has_more'): cursor = data.get('last_id') or (batch[-1].get('id') if batch else None)
            if not cursor and not data.get('has_more'): break
            if not cursor or cursor in cursors:
                raise ValueError('Provider returned incomplete model pagination.')
            cursors.add(cursor)
            params['pageToken' if provider == 'gemini' else 'page_token' if provider == 'cohere' else 'after_id'] = cursor
    return [m for row in rows if isinstance(row, dict) and (m := _model(row, provider))]


def cli_models(provider):
    command = cli_command(provider)
    if not command:
        raise ValueError('This CLI is not installed.')
    codex = provider_family(provider) == 'openai'
    gemini = provider_family(provider) == 'gemini'
    args = [command, 'app-server'] if codex else [command, '--acp'] if gemini else [command, '--print', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose', '--no-session-persistence', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--settings', '{"disableAllHooks":true}']
    env = os.environ.copy()
    scrub_inherited_environment(env)
    messages = queue.Queue(maxsize=2000)
    with tempfile.TemporaryDirectory(prefix='hagent-model-discovery-') as cwd:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', creationflags=BACKGROUND_PROCESS_FLAGS)
        def reader():
            try:
                for line in process.stdout:
                    try: messages.put_nowait(json.loads(line))
                    except (ValueError, queue.Full): pass
            finally:
                try: messages.put_nowait(None)
                except queue.Full: pass
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        def send(message):
            process.stdin.write(json.dumps(message) + '\n'); process.stdin.flush()
        def receive(ident):
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise ValueError('CLI model discovery timed out. Check its sign-in.')
                try: message = messages.get(timeout=remaining)
                except queue.Empty: raise ValueError('CLI model discovery timed out. Check its sign-in.') from None
                if message is None: raise ValueError('CLI could not load models. Check its sign-in and file permissions.')
                if (codex or gemini) and message.get('id') == ident:
                    if 'error' in message: raise ValueError('CLI rejected model discovery. Check its sign-in.')
                    return message.get('result', {})
                response = message.get('response', {})
                if not codex and response.get('request_id') == ident:
                    if response.get('subtype') != 'success': raise ValueError('CLI rejected model discovery. Check its sign-in.')
                    return response.get('response', {})
        try:
            if codex:
                send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name':'hagent', 'version':'1.0'}}})
                receive(1); send({'method':'initialized', 'params':{}})
                rows, cursor, ident, seen = [], None, 2, set()
                while True:
                    send({'id':ident, 'method':'model/list', 'params':{'limit':100, 'includeHidden':True, 'cursor':cursor}})
                    result = receive(ident); rows.extend(result.get('data', []))
                    cursor = result.get('nextCursor')
                    if not cursor: break
                    if cursor in seen: raise ValueError('CLI returned incomplete model pagination.')
                    seen.add(cursor); ident += 1
            elif gemini:
                send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':1,'clientCapabilities':{},'clientInfo':{'name':'hagent','version':'1.0'}}})
                receive(1)
                send({'jsonrpc':'2.0','id':2,'method':'session/new','params':{'cwd':cwd,'mcpServers':[]}})
                result = receive(2)
                rows = [{'id':r.get('modelId'), 'displayName':r.get('name')} for r in result.get('models',{}).get('availableModels',[])]
                for option in result.get('configOptions',[]):
                    if option.get('category') == 'model' and not rows:
                        rows = [{'id':r.get('value'), 'displayName':r.get('name')} for r in option.get('options',[])]
            else:
                send({'type':'control_request', 'request_id':'models', 'request':{'subtype':'initialize'}})
                rows = receive('models').get('models', [])
            return [m for row in rows if (m := _model(row, 'cli'))]
        finally:
            from hagent.managed_process import _terminate_tree
            _terminate_tree(process)
            process.wait(timeout=5)
            process.stdin.close(); thread.join(timeout=2); process.stdout.close()


def credential_fingerprint(provider, connection, key, base):
    return hashlib.sha256(json.dumps([provider, connection, key, base]).encode()).hexdigest()


def remember_catalog(scope, fingerprint, provider, connection, rows):
    token = str(uuid.uuid4())
    with _lock:
        now = time.monotonic()
        for ident in list(_catalogs):
            if _catalogs[ident]['expires'] < now: _catalogs.pop(ident)
        if len(_catalogs) >= 64: _catalogs.pop(next(iter(_catalogs)))
        _catalogs[token] = {'scope':scope, 'fingerprint':fingerprint, 'provider':provider, 'connection':connection,
                            'rows':{r['id']:r for r in rows}, 'expires':now + 900}
    return token


def selected_settings(token, scope, fingerprint, model, effort):
    with _lock:
        catalog = _catalogs.get(token)
        if not catalog or catalog['expires'] < time.monotonic() or catalog['scope'] != scope or catalog['fingerprint'] != fingerprint:
            raise ValueError('Load models again before saving this runtime.')
        row = catalog['rows'].get(model)
        if not row: raise ValueError('Choose a model returned by this connection.')
        if effort and effort not in row['efforts']: raise ValueError('Choose a supported effort for this model.')
        provider, connection = catalog['provider'], catalog['connection']
    actual = CLI_PROVIDERS[provider][0] if connection == 'cli' else provider
    parameter = 'output_config' if provider == 'claude' else 'reasoning' if provider == 'openrouter' else 'reasoning_effort'
    config = {'discovered_model': model, 'effort_selection': effort, 'reasoning_effort': effort,
              'effort_parameter': parameter,
              'capabilities': {'models':[model], 'efforts':['medium'], 'effort_map':{'medium':''}}}
    return actual, config
