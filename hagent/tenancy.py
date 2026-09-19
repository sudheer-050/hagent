"""Workspace boundaries for local CLI and HTTP sessions (not authentication)."""

import click
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria
from hagent import models as m


class ScopeError(click.ClickException):
    pass


# Every owned table has one canonical ownership path. Other foreign keys are
# checked against that owner on writes, including association-table relationships.
PARENTS = {
    m.Issue: ("project_id", m.Project),
    m.PropertyValue: ("issue_id", m.Issue),
    m.Comment: ("issue_id", m.Issue),
    m.IssueMetadata: ("issue_id", m.Issue),
    m.IssueSubscriber: ("issue_id", m.Issue),
    m.TimelineEvent: ("issue_id", m.Issue),
    m.Run: ("issue_id", m.Issue),
    m.SquadMember: ("squad_id", m.Squad),
    m.SquadActivity: ("squad_id", m.Squad),
    m.SkillFile: ("skill_id", m.Skill),
    m.AutopilotTrigger: ("autopilot_id", m.Autopilot),
    m.AutopilotRun: ("autopilot_id", m.Autopilot),
    m.Attachment: ("issue_id", m.Issue),
    m.ProjectResource: ("project_id", m.Project),
    m.IssuePullRequest: ("issue_id", m.Issue),
    m.MemoryRevision: ("memory_id", m.Memory),
    m.MemoryEvent: ("session_id", m.MemorySession),
}
MODELS = {mapper.local_table.name: mapper.class_ for mapper in m.Base.registry.mappers}


def criterion(model, workspace_id):
    if hasattr(model, "workspace_id"):
        return model.workspace_id == workspace_id
    if model in PARENTS:
        field, parent = PARENTS[model]
        return getattr(model, field).in_(select(parent.id).where(criterion(parent, workspace_id)))
    return None


_read_scope_options: dict[str, tuple] = {}


def _read_options(ws):
    """Loader criteria for every scoped model, built once per workspace id."""
    options = _read_scope_options.get(ws)
    if options is None:
        options = tuple(
            with_loader_criteria(model, clause, include_aliases=True)
            for model in MODELS.values()
            if (clause := criterion(model, ws)) is not None
        )
        _read_scope_options[ws] = options
    return options


@event.listens_for(Session, "do_orm_execute")
def scope_reads(state):
    ws = state.session.info.get("workspace_id")
    if ws and state.is_select:
        state.statement = state.statement.options(*_read_options(ws))


@event.listens_for(Session, "before_flush")
def scope_writes(session, *_):
    ws = session.info.get("workspace_id")
    if not ws:
        return
    with session.no_autoflush:
        for obj in session.new | session.dirty | session.deleted:
            model = type(obj)
            if hasattr(model, "workspace_id") and obj.workspace_id != ws:
                raise ScopeError("Object is outside the selected workspace")
            for column in model.__table__.columns:
                value = getattr(obj, column.name)
                if value is None:
                    continue
                for fk in column.foreign_keys:
                    parent = MODELS[fk.column.table.name]
                    if parent is m.Workspace:
                        continue
                    if not session.get(parent, value):
                        raise ScopeError("Referenced object not found in selected workspace")
            for rel in model.__mapper__.relationships:
                if rel.secondary is not None:
                    for related in getattr(obj, rel.key):
                        if hasattr(related, "workspace_id") and related.workspace_id != ws:
                            raise ScopeError("Relationship crosses workspaces")
            if isinstance(obj, m.Issue) and obj.parent_issue_id:
                seen = {obj.id}
                parent_id = obj.parent_issue_id
                while parent_id:
                    if parent_id in seen:
                        raise ScopeError("Issue parent cycle")
                    seen.add(parent_id)
                    parent = session.get(m.Issue, parent_id)
                    if not parent or parent.project_id != obj.project_id:
                        raise ScopeError("Parent must belong to the same project")
                    parent_id = parent.parent_issue_id


class WorkspaceSession(Session):
    def get(self, entity, ident, **kwargs):
        if entity is m.Issue and isinstance(ident, str):
            from hagent.keys import resolve_issue_ref

            ident = resolve_issue_ref(self, ident)
        obj = super().get(entity, ident, **kwargs)
        ws = self.info.get("workspace_id")
        clause = criterion(entity, ws) if ws else None
        if obj is not None and clause is not None:
            # Also guard the identity-map shortcut in Session.get.
            with self.no_autoflush:
                if not self.scalar(select(entity.id).where(entity.id == ident, clause)):
                    return None
        return obj
