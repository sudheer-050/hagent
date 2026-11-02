"""Explicit removal policies shared by the app's removal controls."""
from collections import Counter
from fastapi import HTTPException
from sqlalchemy import select, delete, update
from hagent import models as m

KINDS = {
    'projects': m.Project, 'issues': m.Issue, 'agents': m.Agent,
    'runtimes': m.Runtime, 'squads': m.Squad, 'skills': m.Skill,
    'autopilots': m.Autopilot, 'triggers': m.AutopilotTrigger,
    'repos': m.Repo, 'labels': m.Label, 'properties': m.Property,
    'comments': m.Comment, 'attachments': m.Attachment,
    'resources': m.ProjectResource, 'memberships': m.SquadMember,
    'subscribers': m.IssueSubscriber, 'metadata': m.IssueMetadata,
    'skill-files': m.SkillFile, 'lessons': m.SkillLesson,
    'chats': m.ChatThread, 'messages': m.ChatMessage,
    'memories': m.Memory, 'routing-policies': m.RoutingPolicy,
    'workspaces': m.Workspace, 'terminal-sessions': m.TerminalSession,
}
ARCHIVE = (m.Agent, m.Runtime, m.Squad, m.Property)
ACTIVE = (m.RunStatus.PENDING, m.RunStatus.RUNNING, m.RunStatus.WAITING_APPROVAL)
# Only these ownership edges cascade. Other references are detached if nullable,
# or reported as blockers. Repository files and uploaded files are never erased.
OWNED = {
    'projects': {'issues', 'project_resources'},
    'issues': {'issues', 'comments', 'runs', 'property_values', 'issue_metadata',
               'issue_subscribers', 'timeline_events', 'attachments', 'issue_pull_requests'},
    'comments': {'attachments'},
    'skills': {'skill_files', 'skill_lessons'},
    'autopilots': {'autopilot_triggers', 'autopilot_runs'},
    'chat_threads': {'chat_messages'},
    'memories': {'memory_revisions'},
    'memory_sessions': {'memory_events'},
    'workspaces': {'workspace_members'},
}

def title(obj):
    return str(next((getattr(obj, key) for key in
        ('name', 'title', 'filename', 'label', 'key', 'content', 'body', 'cron_expression', 'cwd')
        if getattr(obj, key, None)), obj.id))[:160]

def model(kind):
    if kind not in KINDS:
        raise HTTPException(404, 'Unknown item type')
    return KINDS[kind]

def get_item(session, kind, ident):
    obj = session.get(model(kind), ident)
    if obj is None:
        raise HTTPException(404, 'Item not found in this workspace')
    return obj

def plan(session, obj):
    """Collect owned records before making any changes, including issue subtrees."""
    tables = m.Base.metadata.tables
    result = []
    seen = set()
    def visit(table, ident):
        key = (table.name, ident)
        if key in seen:
            return
        seen.add(key)
        children = OWNED.get(table.name, ())
        if table.name == 'workspaces':
            children = [t.name for t in tables.values() if 'workspace_id' in t.c]
        for child_name in children:
            child = tables[child_name]
            for col in child.columns:
                if any(fk.column.table.name == table.name for fk in col.foreign_keys):
                    for child_id in session.execute(select(child.c.id).where(col == ident)).scalars():
                        visit(child, child_id)
        result.append((table, ident))
    visit(obj.__table__, obj.id)
    # Workspace contents may be reached through several ownership paths. Delete
    # referencing tables first; stable sorting preserves child-before-parent issues.
    order = {table.name: index for index, table in enumerate(reversed(m.Base.metadata.sorted_tables))}
    result.sort(key=lambda pair: order[pair[0].name])
    return result

def remove(session, obj):
    if isinstance(obj, m.Workspace):
        from hagent.db import get_active_workspace
        if obj.id == get_active_workspace(session).id:
            raise HTTPException(409, 'Switch to another workspace before deleting this one.')
    if isinstance(obj, m.Runtime):
        dependent = session.scalar(select(m.Agent).where(m.Agent.archived.is_(False),
            (m.Agent.runtime_id == obj.id) | (m.Agent.backup_runtime_id == obj.id)
            | (m.Agent.failback_runtime_id == obj.id)))
        if dependent:
            raise HTTPException(409, f'Reassign {dependent.name} from this runtime first (including backup and recovery).')
    if isinstance(obj, m.Agent):
        dependent = session.scalar(select(m.Agent).where(m.Agent.archived.is_(False),
            m.Agent.verifier_agent_id == obj.id, m.Agent.id != obj.id))
        if dependent:
            raise HTTPException(409, f'Choose another verifier for {dependent.name} before archiving this agent.')
        if session.scalar(select(m.ChatThread.id).where(m.ChatThread.agent_id == obj.id, m.ChatThread.pending.is_(True))):
            raise HTTPException(409, 'Wait for this agent’s chat response to finish first.')
        if session.scalar(select(m.Run.id).where(m.Run.agent_id == obj.id, m.Run.status.in_(ACTIVE))):
            raise HTTPException(409, 'Stop this agent’s active or queued runs first.')
        if session.scalar(select(m.Autopilot.id).where(m.Autopilot.agent_id == obj.id, m.Autopilot.enabled.is_(True))):
            raise HTTPException(409, 'Remove or disable this agent’s autopilots first.')
        if session.scalar(select(m.Issue.id).where(m.Issue.assignee_agent_id == obj.id,
                m.Issue.status.notin_([m.IssueStatus.DONE, m.IssueStatus.CANCELLED]))):
            raise HTTPException(409, 'Reassign or finish this agent’s open issues first.')
    if isinstance(obj, ARCHIVE):
        obj.archived = True
        return
    if isinstance(obj, m.Project) and session.scalar(select(m.Autopilot.id).where(m.Autopilot.project_id == obj.id)):
        raise HTTPException(409, 'Remove this project?s autopilots first so they cannot start working across other projects.')
    records = plan(session, obj)
    deleting = {(table.name, ident) for table, ident in records}
    for table, ident in records:
        if table.name == 'runs':
            status = session.execute(select(table.c.status).where(table.c.id == ident)).scalar()
            if status in ACTIVE:
                raise HTTPException(409, 'Stop active or queued runs before deleting this work.')
        if table.name == 'autopilot_runs':
            status = session.execute(select(table.c.status).where(table.c.id == ident)).scalar()
            if status == 'running':
                raise HTTPException(409, 'Wait for the running autopilot to finish first.')
        if table.name == 'chat_threads':
            if session.execute(select(table.c.pending).where(table.c.id == ident)).scalar():
                raise HTTPException(409, 'Wait for this chat response to finish first.')
        if table.name == 'chat_messages':
            pending = session.execute(select(m.ChatThread.pending).join(m.ChatMessage,
                m.ChatMessage.thread_id == m.ChatThread.id).where(m.ChatMessage.id == ident)).scalar()
            if pending:
                raise HTTPException(409, 'Wait for this chat response to finish first.')
        if table.name == 'terminal_sessions':
            status = session.execute(select(table.c.status).where(table.c.id == ident)).scalar()
            if status in ('running', 'cleanup_failed'):
                raise HTTPException(409, 'Stop this workspace’s terminal sessions before deleting it.')
    # Check required references up front. SQL writes below run in one transaction.
    for table, ident in records:
        for child in m.Base.metadata.tables.values():
            for col in child.columns:
                if not any(fk.column.table.name == table.name for fk in col.foreign_keys):
                    continue
                if not col.nullable and 'id' in child.c:
                    refs = session.execute(select(child.c.id).where(col == ident)).scalars()
                    if any((child.name, ref) not in deleting for ref in refs):
                        raise HTTPException(409, f'Remove dependent {child.name.replace("_", " ")} first.')
    for table, ident in records:
        for child in m.Base.metadata.tables.values():
            for col in child.columns:
                if not any(fk.column.table.name == table.name for fk in col.foreign_keys):
                    continue
                if 'id' not in child.c:
                    session.execute(delete(child).where(col == ident))
                elif col.nullable:
                    session.execute(update(child).where(col == ident).values({col.name: None}))
        if table.name == 'comments':
            session.execute(update(m.Comment).where(m.Comment.parent_comment_id == ident).values(parent_comment_id=None))
        if table.name == 'repos':
            session.execute(update(m.Project).where(m.Project.repo_id == ident).values(repo_id=None))
        session.execute(delete(table).where(table.c.id == ident))

def summary(session, obj):
    if isinstance(obj, ARCHIVE):
        return 'Archive this item. Its history will be retained.'
    counts = Counter(table.name.replace('_', ' ') for table, _ in plan(session, obj))
    return 'Permanently delete: ' + ', '.join(f'{count} {name}' for name, count in counts.items()) + '. Linked references will be detached. Files on disk and remote repositories will be kept.'
