# Hagent

A self-hosted multi-agent orchestration platform: projects with **issues** (a kanban tracker), **agents** bound to pluggable **runtimes** (Claude, OpenAI, local Ollama), **squads** and **skills**, and **autopilots** that run agents against matching issues on a schedule — from a CLI or a web dashboard.

Hagent is a from-scratch reimplementation of the core ideas behind commercial multi-agent orchestration tools (Multica and similar): a workspace holding projects, issues, agents, and runtimes, with a clean adapter boundary so any AI backend can be plugged in without touching the rest of the system.

## Why this exists

Most orchestration platforms are closed SaaS products. Hagent is built to:
- Run entirely locally — no dependency on a hosted backend, own your data and workflow.
- Support any AI runtime interchangeably (cloud APIs or local models) behind one interface.
- Model work the way real orchestration tools do — issues with status/labels/properties/sub-issues/comments, not just a flat task queue.

## Architecture

```
hagent/
  models.py       SQLAlchemy models: Workspace, Project, Issue, Label, Property, Comment,
                   Run, Agent, Runtime, Squad, Skill, Autopilot, AutopilotTrigger, Repo
  db.py           Engine/session setup (SQLite by default)
  adapters/       BaseRuntime interface + ClaudeRuntime, OllamaRuntime, OpenAIRuntime
  engine.py       run_issue(): Issue -> Agent -> Runtime -> Run result
  scheduler.py    APScheduler cron runner: fires Autopilots against matching issues
  cli.py          click CLI mirroring Multica's noun/verb structure
  web.py          FastAPI + Jinja2 kanban dashboard
  templates/      Sidebar-nav'd dashboard: project boards, issue detail, squads/skills/autopilots/repos
```

Every runtime backend implements one method — `run(prompt, context) -> RuntimeResult` — so adding a new AI provider never touches the CLI, web layer, or task engine.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # only needed for ClaudeRuntime
```

## CLI usage

```bash
hagent runtime create --name local-ollama --type ollama --model qwen3-fast8b
hagent agent create --name Researcher --runtime <runtime-id>
hagent project create --name "Website Revamp"
hagent issue create --project <project-id> --title "Ping the agent" --description "..." --assignee <agent-id>
hagent issue rerun <issue-id>              # run the issue's assigned agent now
hagent squad create --name Ops && hagent squad member add <squad-id> --agent <agent-id>
hagent skill create --name Terse --content "Always answer in one word." 
hagent autopilot create --name "Todo runner" --agent <agent-id> --project <project-id> --filter-status todo
hagent autopilot trigger-add <autopilot-id> --cron "*/5 * * * *"
hagent daemon start                         # run the autopilot scheduler in the foreground
```

Run `hagent <noun> --help` for the full command set (project/label/property/issue/squad/skill/autopilot/repo/agent/runtime/daemon).

## Web dashboard

```bash
uvicorn hagent.web:app --reload
```

Open http://127.0.0.1:8000 for a kanban board per project, issue detail pages (status/assignee/labels/comments/run history), and list+create pages for agents, squads, skills, autopilots, and repos. The autopilot scheduler starts automatically with the web server.

## Tests

```bash
pytest
```

## Status

Phase 2: full issue-tracker object model (projects/issues/labels/properties/comments/sub-issues), squads, skills, autopilots with cron scheduling, and a kanban web UI — all verified live (CLI smoke tests, a real autonomous cron firing, and a real browser session), not just unit tests.

Deliberately out of scope (see the project plan for why): skill marketplace import, MCP server management, webhook autopilot triggers, multi-user auth, and sandboxed repo checkout.
