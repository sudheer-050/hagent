# Changelog

## 0.2.1 - 2026-09-19

### Added

- Skill-level learning memory records redacted lessons from relevant run failures and verifier rejections.
- Agents sharing a skill can retrieve its recent lessons on demand through `recall_lessons` without adding the full history to every prompt.
- Skill lesson history is visible in the Skills UI and exportable to workspace-scoped Markdown snapshots.
- The public download site now includes real screenshots, OS-specific setup instructions, a first-project walkthrough, practical use cases, a detailed FAQ, and an honest release-readiness assessment.

### Safety and reliability

- Infrastructure, cancellation, authentication, capacity, network, and timeout failures are excluded from skill learning.
- Lesson persistence is synchronous for reliable CLI operation, deduplicated, tenant-scoped, and mirrored only after the database commit succeeds.
- Generated lesson notes and exports are excluded from Git.
- Release validation: 286 tests pass; the 6 known agent/skill presentation failures remain unchanged from 0.2.0.

## 0.2.0 - 2026-09-19

This release expands Hagent from a local issue runner into a broader self-hosted agent control plane.

### Added

- Provider-neutral memory shared by Hagent, Codex CLI, Claude Code, and MCP clients.
- Adaptive model routing with capability, cost, latency, effort, and fallback policies.
- Authenticated remote CLI access and token-scoped worker devices.
- Provider health monitoring, automatic runtime failover, failback, and interrupted-run recovery.
- OpenCode, Antigravity, generic CLI, and remote-worker adapters.
- Agent approvals, live activity, usage reporting, chat threads, and direct owner messaging.
- Concurrent dispatch with agent and global limits, priorities, stage barriers, and verifier agents.
- Git worktree isolation, issue diff viewing, pull-request recording, and project resources.
- Multi-file skills, skill imports, labels, badges, and runtime-local skill support.
- Autopilot create-issue mode, timezone-aware schedules, webhook triggers, and graph visibility.
- Cross-workspace tenancy enforcement, authentication hardening, and expanded test coverage.

### Changed

- The application entry point is `hagent` or `python -m hagent` after installation.
- Windows launch uses `launch_hagent.vbs`.
- Chat, memory, routing, usage, and settings use dedicated dashboard views.
- SQLite uses WAL mode by default; `HAGENT_SQLITE_SAFE=1` restores conservative SQLite settings.

### Removed

- The embedded browser terminal and bundled xterm assets.
- The earlier knowledge/RAG pages; canonical memory replaces that application path.
- The old local file watcher and batch launcher.

### Known limitations

- Six agent/skill presentation tests remain to be reconciled with the latest templates; 282 tests pass.
- The terminal working directory is not a security sandbox.
- CI, a one-click installer, PyPI publication, and container packaging are not included.
- Runtime provider catalogs and third-party CLI compatibility can change independently of Hagent.

### Upgrade notes

- Back up `hagent.db` using SQLite's backup API before upgrading.
- Existing schemas are migrated additively during startup.
- Local databases, credentials, users, agents, issues, memory, and run history are not stored in Git.
- Review every agent's terminal and approval settings after upgrading.
