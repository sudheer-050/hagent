"""One active connection per assistant or API endpoint in each workspace."""
import json
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from hagent.models import Agent, ChatThread, RoutingPolicy, Runtime, RuntimeType
from hagent.runtime_catalog import provider_config


def identity(runtime_type, config):
    typ = getattr(runtime_type, 'value', runtime_type)
    if typ.endswith('_cli') or typ == 'claude_code':
        return ('cli', typ, config.get('command', '') if typ == 'generic_cli' else '')
    profile = provider_config(config.get('provider_id', typ))
    base = str(config.get('base_url') or profile.get('base_url') or '').rstrip('/')
    parts = urlsplit(base)
    host = (parts.hostname or '').lower()
    if host in ('localhost', '::1'): host = '127.0.0.1'
    netloc = host + (':' + str(parts.port) if parts.port else '')
    base = urlunsplit((parts.scheme.lower(), netloc, parts.path.rstrip('/'), parts.query, ''))
    return ('local' if typ == 'ollama' else 'api', base or config.get('provider_id', typ))


def lock_connections(session):
    # SQLite serializes the read-and-create transaction across web/CLI processes,
    # including simultaneous double submissions. No process-local lock is needed.
    connection = session.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN IMMEDIATE')


def existing_connection(session, workspace_id, runtime_type, config, exclude_id=None):
    wanted = identity(runtime_type, config)
    rows = session.scalars(select(Runtime).where(Runtime.workspace_id == workspace_id,
        Runtime.archived.is_(False)).order_by(Runtime.created_at, Runtime.id)).all()
    return next((r for r in rows if r.id != exclude_id and identity(r.type, json.loads(r.config_json or '{}')) == wanted), None)


def create_or_reuse(session, workspace_id, name, runtime_type, model, config):
    lock_connections(session)
    existing = existing_connection(session, workspace_id, runtime_type, config)
    if existing:
        return existing, False
    row = Runtime(workspace_id=workspace_id, name=name, type=RuntimeType(runtime_type), model=model,
                  config_json=json.dumps(config))
    session.add(row)
    session.flush()
    return row, True


def consolidate(session, workspace_id):
    """Retain old rows for history; reassign live references to the oldest row."""
    lock_connections(session)
    canonical, replacements = {}, {}
    rows = session.scalars(select(Runtime).where(Runtime.workspace_id == workspace_id,
        Runtime.archived.is_(False)).order_by(Runtime.created_at, Runtime.id)).all()
    for row in rows:
        key = identity(row.type, json.loads(row.config_json or '{}'))
        if key in canonical:
            replacements[row.id] = canonical[key].id
            row.archived = True
        else:
            canonical[key] = row
    if not replacements:
        return {}
    for agent in session.scalars(select(Agent).where(Agent.workspace_id == workspace_id)):
        changed = False
        for field in ('runtime_id', 'backup_runtime_id', 'failback_runtime_id'):
            if getattr(agent, field) in replacements:
                setattr(agent, field, replacements[getattr(agent, field)])
                changed = True
        if changed:
            if agent.backup_runtime_id == agent.runtime_id: agent.backup_runtime_id = None
            if agent.failback_runtime_id == agent.runtime_id: agent.failback_runtime_id = None
    for thread in session.scalars(select(ChatThread).where(ChatThread.workspace_id == workspace_id,
            ChatThread.runtime_id.in_(replacements))):
        thread.runtime_id = replacements[thread.runtime_id]
    def replace(value):
        if isinstance(value, str): return replacements.get(value, value)
        if isinstance(value, list): return [replace(item) for item in value]
        if isinstance(value, dict): return {key:replace(item) for key,item in value.items()}
        return value
    for policy in session.scalars(select(RoutingPolicy).where(RoutingPolicy.workspace_id == workspace_id)):
        policy.config_json = json.dumps(replace(json.loads(policy.config_json or '{}')))
    session.flush()
    return replacements
