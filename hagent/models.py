"""SQLAlchemy models for Hagent's core object model (mirrors Multica's noun set)."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class RuntimeType(str, enum.Enum):
    CLAUDE = "claude"
    OPENAI = "openai"
    OLLAMA = "ollama"


class ProjectStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class IssueStatus(str, enum.Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    CANCELLED = "cancelled"


ISSUE_STATUS_ORDER = [
    IssueStatus.BACKLOG,
    IssueStatus.TODO,
    IssueStatus.IN_PROGRESS,
    IssueStatus.IN_REVIEW,
    IssueStatus.DONE,
    IssueStatus.CANCELLED,
]


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TriggerType(str, enum.Enum):
    CRON = "cron"
    WEBHOOK = "webhook"


class PropertyType(str, enum.Enum):
    TEXT = "text"
    NUMBER = "number"
    SELECT = "select"
    DATE = "date"


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, default="member")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Runtime(Base):
    __tablename__ = "runtimes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[RuntimeType] = mapped_column(Enum(RuntimeType), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agents: Mapped[list["Agent"]] = relationship(back_populates="runtime")


agent_skills = Table(
    "agent_skills",
    Base.metadata,
    Column("agent_id", ForeignKey("agents.id"), primary_key=True),
    Column("skill_id", ForeignKey("skills.id"), primary_key=True),
)

agent_mcp_servers = Table(
    "agent_mcp_servers",
    Base.metadata,
    Column("agent_id", ForeignKey("agents.id"), primary_key=True),
    Column("server_id", ForeignKey("mcp_servers.id"), primary_key=True),
)

issue_labels = Table(
    "issue_labels",
    Base.metadata,
    Column("issue_id", ForeignKey("issues.id"), primary_key=True),
    Column("label_id", ForeignKey("labels.id"), primary_key=True),
)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    runtime_id: Mapped[str] = mapped_column(ForeignKey("runtimes.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar_path: Mapped[str | None] = mapped_column(String, nullable=True)
    env_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    runtime: Mapped["Runtime"] = relationship(back_populates="agents")
    runs: Mapped[list["Run"]] = relationship(back_populates="agent")
    skills: Mapped[list["Skill"]] = relationship(secondary=agent_skills, back_populates="agents")
    mcp_servers: Mapped[list["McpServer"]] = relationship(secondary=agent_mcp_servers, back_populates="agents")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), default=ProjectStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    issues: Mapped[list["Issue"]] = relationship(back_populates="project")


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    parent_issue_id: Mapped[str | None] = mapped_column(ForeignKey("issues.id"), nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[IssueStatus] = mapped_column(Enum(IssueStatus), default=IssueStatus.BACKLOG)
    position: Mapped[int] = mapped_column(Integer, default=0)
    assignee_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    project: Mapped["Project"] = relationship(back_populates="issues")
    assignee: Mapped["Agent | None"] = relationship()
    parent: Mapped["Issue | None"] = relationship(remote_side=[id])
    comments: Mapped[list["Comment"]] = relationship(back_populates="issue", order_by="Comment.created_at")
    runs: Mapped[list["Run"]] = relationship(back_populates="issue", order_by="Run.created_at")
    labels: Mapped[list["Label"]] = relationship(secondary=issue_labels, back_populates="issues")
    property_values: Mapped[list["PropertyValue"]] = relationship(back_populates="issue")
    metadata_values: Mapped[list["IssueMetadata"]] = relationship(back_populates="issue", cascade="all, delete-orphan")
    subscribers: Mapped[list["IssueSubscriber"]] = relationship(back_populates="issue", cascade="all, delete-orphan")
    timeline: Mapped[list["TimelineEvent"]] = relationship(back_populates="issue", order_by="TimelineEvent.created_at", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="issue", cascade="all, delete-orphan")


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    color: Mapped[str] = mapped_column(String, default="#888888")

    issues: Mapped[list["Issue"]] = relationship(secondary=issue_labels, back_populates="labels")


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[PropertyType] = mapped_column(Enum(PropertyType), default=PropertyType.TEXT)
    options_json: Mapped[str] = mapped_column(Text, default="[]")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class PropertyValue(Base):
    __tablename__ = "property_values"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"), nullable=False)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")

    issue: Mapped["Issue"] = relationship(back_populates="property_values")
    property: Mapped["Property"] = relationship()


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"), nullable=False)
    author: Mapped[str] = mapped_column(String, default="you")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    issue: Mapped["Issue"] = relationship(back_populates="comments")


class IssueMetadata(Base):
    __tablename__ = "issue_metadata"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"), nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
    issue: Mapped["Issue"] = relationship(back_populates="metadata_values")


class IssueSubscriber(Base):
    __tablename__ = "issue_subscribers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    issue: Mapped["Issue"] = relationship(back_populates="subscribers")


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    issue: Mapped["Issue"] = relationship(back_populates="timeline")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.PENDING)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    transcript_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    issue: Mapped["Issue"] = relationship(back_populates="runs")
    agent: Mapped["Agent"] = relationship(back_populates="runs")


class Squad(Base):
    __tablename__ = "squads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    members: Mapped[list["SquadMember"]] = relationship(back_populates="squad")


class SquadMember(Base):
    __tablename__ = "squad_members"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    squad_id: Mapped[str] = mapped_column(ForeignKey("squads.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, default="member")

    squad: Mapped["Squad"] = relationship(back_populates="members")
    agent: Mapped["Agent"] = relationship()


class SquadActivity(Base):
    __tablename__ = "squad_activities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    squad_id: Mapped[str] = mapped_column(ForeignKey("squads.id"), nullable=False)
    issue_id: Mapped[str | None] = mapped_column(ForeignKey("issues.id"), nullable=True)
    evaluation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)

    agents: Mapped[list["Agent"]] = relationship(secondary=agent_skills, back_populates="skills")
    files: Mapped[list["SkillFile"]] = relationship(back_populates="skill", cascade="all, delete-orphan")


class SkillFile(Base):
    __tablename__ = "skill_files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    skill: Mapped["Skill"] = relationship(back_populates="files")


class Autopilot(Base):
    __tablename__ = "autopilots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    filter_status: Mapped[IssueStatus | None] = mapped_column(Enum(IssueStatus), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    agent: Mapped["Agent"] = relationship()
    project: Mapped["Project | None"] = relationship()
    triggers: Mapped[list["AutopilotTrigger"]] = relationship(back_populates="autopilot")


class AutopilotTrigger(Base):
    __tablename__ = "autopilot_triggers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    autopilot_id: Mapped[str] = mapped_column(ForeignKey("autopilots.id"), nullable=False)
    cron_expression: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[TriggerType] = mapped_column(Enum(TriggerType), default=TriggerType.CRON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    webhook_token: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    autopilot: Mapped["Autopilot"] = relationship(back_populates="triggers")


class AutopilotRun(Base):
    __tablename__ = "autopilot_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    autopilot_id: Mapped[str] = mapped_column(ForeignKey("autopilots.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")
    summary: Mapped[str] = mapped_column(Text, default="")


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    local_path: Mapped[str | None] = mapped_column(String, nullable=True)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    comment_id: Mapped[str | None] = mapped_column(ForeignKey("comments.id"), nullable=True)
    issue_id: Mapped[str | None] = mapped_column(ForeignKey("issues.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    issue: Mapped["Issue | None"] = relationship(back_populates="attachments")


class ChatThread(Base):
    __tablename__ = "chat_threads"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="thread", order_by="ChatMessage.created_at", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.id"), nullable=False)
    author: Mapped[str] = mapped_column(String, default="you")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    thread: Mapped["ChatThread"] = relationship(back_populates="messages")


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    transport: Mapped[str] = mapped_column(String, default="stdio")
    command: Mapped[str | None] = mapped_column(String, nullable=True)
    args_json: Mapped[str] = mapped_column(Text, default="[]")
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    agents: Mapped[list["Agent"]] = relationship(secondary=agent_mcp_servers, back_populates="mcp_servers")
