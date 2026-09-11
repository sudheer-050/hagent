# Holly

A self-hosted multi-agent orchestration platform: define **agents**, bind each one to a pluggable **runtime** (Claude, OpenAI, local Ollama), and run **tasks** through them from a CLI or a web dashboard.

Holly is a from-scratch reimplementation of the core ideas behind commercial multi-agent orchestration tools (Multica and similar): a workspace holding agents, runtimes, and tasks, with a clean adapter boundary so any AI backend can be plugged in without touching the rest of the system.

## Why this exists

Most orchestration platforms are closed SaaS products. Holly is built to:
- Run entirely locally — no dependency on a hosted backend, own your data and workflow.
- Support any AI runtime interchangeably (cloud APIs or local models) behind one interface.
- Be simple enough to read end-to-end: the whole execution path (CLI/web → Task → Agent → Runtime → result) is under a thousand lines.

## Architecture

```
holly/
  models.py       SQLAlchemy models: Workspace, Agent, Runtime, Task
  db.py           Engine/session setup (SQLite by default)
  adapters/       BaseRuntime interface + ClaudeRuntime, OllamaRuntime, OpenAIRuntime
  engine.py       Task execution engine: Task -> Agent -> Runtime -> result
  cli.py          click CLI (holly agent/runtime/task ...)
  web.py          FastAPI + Jinja2 dashboard
  templates/      Dashboard HTML templates
```

Every runtime backend implements one method — `run(prompt, context) -> RuntimeResult` — so adding a new AI provider never touches the CLI, web layer, or task engine.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # only needed for ClaudeRuntime
```

## CLI usage

```bash
holly runtime list
holly agent create --name "Researcher" --runtime ollama --model qwen3-fast8b
holly agent list
holly task create --agent <agent-id> --prompt "Summarize this repo's README"
holly task run <task-id>
holly task get <task-id>
```

## Web dashboard

```bash
uvicorn holly.web:app --reload
```

Then open http://127.0.0.1:8000 to view agents, runtimes, and tasks, and trigger a task run from the browser.

## Tests

```bash
pytest
```

## Status

Phase 1 MVP: core data model, Claude + Ollama runtime adapters, task execution engine, CLI, and a read/trigger web dashboard. Autopilots, squads, skills, and multi-runtime failover are deliberately out of scope for this phase — see the project plan for the roadmap.
