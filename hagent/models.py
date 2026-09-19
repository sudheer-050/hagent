"""SQLAlchemy models for Hagent's core object model (mirrors Multica's noun set)."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import event, text, Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from hagent.skill_icons import default_skill_emoji


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
    GEMINI = "gemini"
    GEMINI_CLI = "gemini_cli"
    CODEX_CLI = "codex_cli"
    CLAUDE_CODE = "claude_code"
    ANTIGRAVITY_CLI = "antigravity_cli"
    OPENCODE_CLI = "opencode_cli"
    GENERIC_CLI = "generic_cli"
    OPENAI_COMPATIBLE = "openai_compatible"


class ProjectStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class IssueStatus(str, enum.Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


ISSUE_STATUS_ORDER = [
    IssueStatus.BACKLOG,
    IssueStatus.TODO,
    IssueStatus.IN_PROGRESS,
    IssueStatus.IN_REVIEW,
    IssueStatus.BLOCKED,
    IssueStatus.DONE,
    IssueStatus.CANCELLED,
]


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


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
    issue_prefix: Mapped[str] = mapped_column(String, default="")
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


class User(Base):
    """A login for this Hagent instance. Accounts are instance-wide, not per workspace."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, default="member")  # "owner" or "member"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuthToken(Base):
    """A revocable credential: a long-lived API token, a browser login session or a worker token.

    Only the SHA-256 of the secret is stored, so a leaked database does not leak usable tokens.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="api")  # "api", "session" or "worker"
    name: Mapped[str] = mapped_column(String, default="")
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Worker(Base):
    """A device running `hagent worker`, seen through its polling heartbeat."""

    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    capabilities: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of runtime types it offers
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkerJob(Base):
    """One model call handed to a worker device and the result it sent back."""

    __tablename__ = "worker_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    worker_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    runtime_type: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, default="")
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    prompt: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="pending")  # pending, running, done, failed, abandoned
    output: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
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
    health_status: Mapped[str] = mapped_column(String, default="unknown")
    health_detail: Mapped[str] = mapped_column(Text, default="")
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

skill_labels = Table(
    "skill_labels",
    Base.metadata,
    Column("skill_id", ForeignKey("skills.id"), primary_key=True),
    Column("label_id", ForeignKey("labels.id"), primary_key=True),
)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    runtime_id: Mapped[str] = mapped_column(ForeignKey("runtimes.id"), nullable=False)
    backup_runtime_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set only while an automatic provider failover is active. Keeping this
    # separate from backup_runtime_id distinguishes recovery from a normal backup.
    failback_runtime_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    designation: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar_path: Mapped[str | None] = mapped_column(String, nullable=True)
    env_json: Mapped[str] = mapped_column(Text, default="{}")
    terminal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    terminal_working_directory: Mapped[str | None] = mapped_column(String, nullable=True)
    require_run_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    delegation_limit: Mapped[int] = mapped_column(Integer, default=8)
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, default=1)
    verifier_agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sandbox_image: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    runtime: Mapped["Runtime"] = relationship(back_populates="agents")
    runs: Mapped[list["Run"]] = relationship(back_populates="agent")
    squad_memberships: Mapped[list["SquadMember"]] = relationship(back_populates="agent")
    skills: Mapped[list["Skill"]] = relationship(secondary=agent_skills, back_populates="agents")
    mcp_servers: Mapped[list["McpServer"]] = relationship(secondary=agent_mcp_servers, back_populates="agents")
    chat_threads: Mapped[list["ChatThread"]] = relationship(back_populates="agent")


class ChatThread(Base):
    """One running conversation between the workspace owner and a single agent -
    general discussion, not tied to any issue. Also where an agent can reach the
    owner directly mid-run (see the message_user tool in engine.py) with a
    question or recommendation that isn't worth a formal approval gate."""

    __tablename__ = "chat_threads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)  # runtime resume token, continues context turn to turn
    unread: Mapped[bool] = mapped_column(Boolean, default=False)  # set when the agent messages first, cleared when the owner opens the thread
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    agent: Mapped["Agent"] = relationship(back_populates="chat_threads")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="thread", cascade="all, delete-orphan", order_by="ChatMessage.created_at")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" | "agent"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    thread: Mapped["ChatThread"] = relationship(back_populates="messages")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), default=ProjectStatus.ACTIVE)
    repo_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    issues: Mapped[list["Issue"]] = relationship(back_populates="project")
    resources: Mapped[list["ProjectResource"]] = relationship(back_populates="project", cascade="all, delete-orphan", order_by="ProjectResource.position")


class ProjectResource(Base):
    """Something attached to a project for agents to work with, e.g. a GitHub repo URL."""

    __tablename__ = "project_resources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False, default="github_repo")
    ref: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped["Project"] = relationship(back_populates="resources")


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
    priority: Mapped[str] = mapped_column(String, default="none")  # none, low, medium, high, urgent
    start_date: Mapped[str | None] = mapped_column(String, nullable=True)  # YYYY-MM-DD
    due_date: Mapped[str | None] = mapped_column(String, nullable=True)  # YYYY-MM-DD
    stage: Mapped[int | None] = mapped_column(Integer, nullable=True)  # barrier group among a parent's sub-issues
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # per-workspace sequence: HAG-12
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
    pull_requests: Mapped[list["IssuePullRequest"]] = relationship(back_populates="issue", cascade="all, delete-orphan", order_by="IssuePullRequest.created_at")


@event.listens_for(Issue, "before_insert")
def _assign_issue_number(mapper, connection, target):
    """Give each new issue the next number in its workspace (HAG-1, HAG-2, ...)."""
    if target.number is not None:
        return
    workspace_id = connection.execute(text("SELECT workspace_id FROM projects WHERE id = :pid"), {"pid": target.project_id}).scalar()
    next_in_db = connection.execute(
        text("SELECT COALESCE(MAX(i.number), 0) + 1 FROM issues i JOIN projects p ON p.id = i.project_id WHERE p.workspace_id = :ws"),
        {"ws": workspace_id},
    ).scalar()
    # Several issues can be inserted in one flush before any row exists to count, so remember
    # what this connection already handed out.
    handed_out = connection.info.setdefault("issue_numbers", {})
    target.number = max(next_in_db, handed_out.get(workspace_id, 0) + 1)
    handed_out[workspace_id] = target.number


class IssuePullRequest(Base):
    """A pull request linked to an issue (recorded by `issue pr` or discovered from the issue's branch)."""

    __tablename__ = "issue_pull_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"), nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    state: Mapped[str] = mapped_column(String, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    issue: Mapped["Issue"] = relationship(back_populates="pull_requests")


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    color: Mapped[str] = mapped_column(String, default="#888888")

    issues: Mapped[list["Issue"]] = relationship(secondary=issue_labels, back_populates="labels")
    skills: Mapped[list["Skill"]] = relationship(secondary=skill_labels, back_populates="labels")


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
    parent_comment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
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
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript_json: Mapped[str] = mapped_column(Text, default="{}")
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)  # process running it, for crash recovery
    owner_started: Mapped[float | None] = mapped_column(Float, nullable=True)  # that process's start time (pid-reuse guard)
    resumed_from: Mapped[str | None] = mapped_column(String, nullable=True)  # the interrupted run this one continues

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
    agent: Mapped["Agent"] = relationship(back_populates="squad_memberships")


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
    emoji: Mapped[str] = mapped_column(String, default=default_skill_emoji)
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agents: Mapped[list["Agent"]] = relationship(secondary=agent_skills, back_populates="skills")
    files: Mapped[list["SkillFile"]] = relationship(back_populates="skill", cascade="all, delete-orphan")
    labels: Mapped[list["Label"]] = relationship(secondary=skill_labels, back_populates="skills")


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
    mode: Mapped[str] = mapped_column(String, default="filter")  # "filter" (existing issues) or "create_issue"
    description: Mapped[str] = mapped_column(Text, default="")  # the prompt for create_issue mode
    issue_title_template: Mapped[str] = mapped_column(String, default="")

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
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)  # IANA name; cron fires in this zone
    label: Mapped[str] = mapped_column(String, default="")

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


class Memory(Base):
    """Canonical provider-neutral durable memory owned by Hagent."""
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    source_provider: Mapped[str] = mapped_column(String, default="hagent")
    source_agent: Mapped[str] = mapped_column(String, default="")
    source_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_hash: Mapped[str] = mapped_column(String, nullable=False)
    origin_type: Mapped[str] = mapped_column(String, default="agent_observation")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    verification_status: Mapped[str] = mapped_column(String, default="unverified")
    sensitivity: Mapped[str] = mapped_column(String, default="normal")
    status: Mapped[str] = mapped_column(String, default="active")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    embedding_model: Mapped[str] = mapped_column(String, default="")
    superseded_by_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revisions: Mapped[list["MemoryRevision"]] = relationship(back_populates="memory", cascade="all, delete-orphan")


class MemoryRevision(Base):
    __tablename__ = "memory_revisions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    actor: Mapped[str] = mapped_column(String, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    memory: Mapped["Memory"] = relationship(back_populates="revisions")


class MemorySession(Base):
    __tablename__ = "memory_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String, default="hagent")
    client: Mapped[str] = mapped_column(String, default="hagent")
    external_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    task: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String, default="active")
    summary: Mapped[str] = mapped_column(Text, default="")
    unresolved_state: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    events: Mapped[list["MemoryEvent"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class MemoryEvent(Base):
    __tablename__ = "memory_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("memory_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, default="message")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    session: Mapped["MemorySession"] = relationship(back_populates="events")


class MemorySetting(Base):
    __tablename__ = "memory_settings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    retain_raw_events: Mapped[bool] = mapped_column(Boolean, default=False)
    automatic_extraction: Mapped[bool] = mapped_column(Boolean, default=True)
    retrieval_limit: Mapped[int] = mapped_column(Integer, default=8)
    token_budget: Mapped[int] = mapped_column(Integer, default=1200)
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
    strict_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding_provider: Mapped[str] = mapped_column(String, default="")
    embedding_model: Mapped[str] = mapped_column(String, default="")
    embedding_base_url: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RoutingPolicy(Base):
    __tablename__ = "routing_policies"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String, default="auto")
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    effort: Mapped[str] = mapped_column(String, default="")
    max_effort: Mapped[str] = mapped_column(String, default="high")
    max_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_preference: Mapped[str] = mapped_column(String, default="balanced")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RoutingDecision(Base):
    __tablename__ = "routing_decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    memory_session_id: Mapped[str | None] = mapped_column(ForeignKey("memory_sessions.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    task_type: Mapped[str] = mapped_column(String, default="general")
    selected_runtime_id: Mapped[str] = mapped_column(ForeignKey("runtimes.id"), nullable=False)
    selected_provider: Mapped[str] = mapped_column(String, nullable=False)
    selected_model: Mapped[str] = mapped_column(String, nullable=False)
    effort: Mapped[str] = mapped_column(String, nullable=False)
    provider_effort: Mapped[str] = mapped_column(String, default="")
    complexity_score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    fallback_json: Mapped[str] = mapped_column(Text, default="[]")
    output_limit: Mapped[int] = mapped_column(Integer, default=4096)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    execution_mode: Mapped[str] = mapped_column(String, default="standard")
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String, default="planned")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
