# Hagent contributor handoff

This file contains repository-level development notes. It deliberately excludes machine-specific paths, live users, agent rosters, credentials, runtime IDs, and private operational decisions. Inspect the active database when diagnosing a particular installation; do not assume its contents from documentation.

## Current release

- Version: 0.2.1
- Python: 3.10 or newer
- Application: FastAPI, SQLAlchemy, SQLite, Jinja2, Click, APScheduler, MCP
- Primary tested host: Windows
- Default database: `hagent.db`
- Default web address: `http://127.0.0.1:8000`

## Development setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
python -m hagent serve
```

Run the test suite with:

```bash
python -m pytest -q
```

Run the local orchestration-overhead benchmark from the repository root with:

```bash
python -m scripts.benchmark --runs 200
```

## Architecture map

- `hagent/models.py`: persisted domain model.
- `hagent/db.py`: database configuration and additive migrations.
- `hagent/engine.py`: agent execution, tools, delegation, memory, routing, and fallback.
- `hagent/dispatcher.py`: queued-run concurrency and priority dispatch.
- `hagent/scheduler.py`: Autopilot scheduling and trigger execution.
- `hagent/adapters/`: provider API, local-model, coding-CLI, generic CLI, and worker adapters.
- `hagent/memory.py`: canonical provider-neutral memory domain.
- `hagent/router.py`: adaptive model selection and routing audit.
- `hagent/auth.py`: users, sessions, API tokens, and request protection.
- `hagent/worker.py` and `worker_api.py`: execution on connected devices.
- `hagent/web.py`: dashboard and JSON/HTML endpoints.
- `hagent/cli.py`: local and remote command surface.

## Data and migrations

Schema upgrades are additive and implemented in `_ensure_schema()` in `hagent/db.py`. A new mapped column must also have a migration entry for existing SQLite installations. Back up the database with SQLite's backup API before testing migrations.

Never commit the live database, WAL/SHM files, `.env`, logs, credentials, local CLI state, or generated authentication material. Git intentionally excludes these files. The repository contains application code and examples, not a deployable copy of one user's live workspace.

## Security invariants

- Anonymous access is intended only for direct loopback use before accounts exist.
- Non-loopback serving requires accounts and should use HTTPS or a trusted private network.
- Session cookies, bearer tokens, worker tokens, and workspace scoping must remain enforced at every relevant entry point.
- Terminal access is disabled by default.
- `terminal_working_directory` is only the subprocess starting directory. It is not confinement.
- A terminal-enabled agent inherits the OS permissions and reachable credentials of the Hagent process user.
- Run approval applies before execution begins, not before every tool call.
- Worker terminal access requires both the server request and the worker's local opt-in.
- Agent memory is untrusted context data, not executable policy or authority.

Use a dedicated OS account, container, VM, or isolated worker for untrusted autonomous execution. Do not describe the application as sandboxing host-level CLI agents unless that exact runtime and platform path has been verified.

## Release validation

Before publishing a release:

1. Confirm `git status` contains no database, log, credential, or generated local-state files.
2. Run the full test suite and record the exact result.
3. Exercise one API/local-model runtime and one CLI runtime when available.
4. Verify a queued run, cancellation, restart recovery, approval, and provider fallback.
5. Verify database upgrade from a copy of the prior release.
6. Review README security and installation instructions.
7. Update `CHANGELOG.md` and the version in `pyproject.toml`.

The 0.2.1 snapshot has 286 passing tests and 6 pre-existing failures confined to agent/skill presentation markup. Treat a fully green suite as the next release gate.

## Known development gaps

- Reconcile the six UI presentation tests with the latest templates.
- Add continuous integration for supported Python versions.
- Add a reproducible container image and deployment guide.
- Decide whether to publish signed packages or a PyPI distribution.
- Expand macOS/Linux verification for terminal and CLI adapter behavior.
- Keep runtime catalogs conservative because provider model names and CLI flags change independently.

## Working-tree discipline

Do not discard or overwrite an unknown dirty working tree. Review changes before editing, keep unrelated user work intact, and use focused commits. Feature work should include tests, migration coverage when persistence changes, documentation for new security boundaries, and a recovery path for interrupted runs.
