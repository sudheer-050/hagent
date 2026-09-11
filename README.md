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
  models.py       SQLAlchemy models for workspaces, issue extras, skills, chat,
                   attachments, repos, autopilots, and MCP server bindings
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

Run `hagent <noun> --help` for the full command set, including workspace/user profile,
issue search/timeline/metadata/subscribers, skill import/refresh/search, squad activity,
webhook triggers, repo checkout, attachments, chat, and MCP server bindings.

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

Phase 3 adds local workspace/profile management, issue extras and timeline events,
multi-file skill bundles, squad activity, cron and webhook triggers, real git checkout,
attachments, standalone chat, and MCP protocol tool invocation in runtime loops.

Marketplace-specific skill discovery and public webhook exposure remain intentionally
out of scope: local archives/direct URLs are supported, and a user must expose a local
webhook endpoint through their own tunnel or reverse proxy when needed.
