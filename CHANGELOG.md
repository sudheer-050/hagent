# Changelog

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
