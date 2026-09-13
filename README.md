# Hagent

A self-hosted multi-agent orchestration platform: projects with **issues** (a kanban tracker), **agents** bound to pluggable **runtimes** (major cloud APIs, local Ollama/LM Studio, and installed AI CLIs), **squads** and **skills**, and **autopilots** that run agents against matching issues on a schedule — from a CLI or a web dashboard.

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
  adapters/       BaseRuntime interface + direct API, OpenAI-compatible, Ollama, Gemini, and CLI adapters
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
pip install -e .                    # installs the hagent command
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


## Runtime providers

The web runtime picker includes direct OpenAI, Anthropic Claude, Google Gemini,
xAI Grok, Mistral, DeepSeek, and Perplexity profiles; Groq, OpenRouter, and
Together model gateways; local Ollama and LM Studio; authenticated Gemini CLI,
Codex CLI, and Claude Code assistants; plus a custom OpenAI-compatible endpoint.

Each agent chooses its own primary runtime and may optionally choose a different backup runtime. If the primary raises an execution error (including exhausted quota, rate limiting, authentication failure, or provider/CLI unavailability), Hagent immediately retries the task once on the backup and records a `runtime_failover` event. Cancellation and successful model responses do not trigger a backup request.

Each provider and recommended model includes a best-use description. Ollama
recommendations are rated against the detected system RAM and NVIDIA VRAM.
Cloud model sizes do not consume local VRAM. Provider catalogs change often, so
the form always allows an exact custom model ID.

API subscriptions and consumer chat subscriptions are separate products for
most providers. API runtimes require a provider API key unless an environment
variable is configured. CLI runtimes reuse the CLI's existing local login.

## Knowledge and RAG

Create a knowledge base from **Knowledge** in the sidebar, upload files, index
a local folder path (and let Hagent watch it automatically), or add public web pages; then test
retrieval and grant access to selected agents. Hagent extracts
text from PDF, DOCX, HTML, Markdown, text, CSV, JSON, YAML, logs, and common
source files; it chunks the text with overlap, stores it in the local database,
and returns ranked passages with source names and PDF page numbers where
available. Each document is limited to 10 MB. Folder scans skip hidden
directories, common dependency/build caches, and symbolic links; scans are
capped at 1,000 supported files. File additions, edits, moves, and deletions
are detected automatically as filesystem events arrive; the index updates after
the document is reprocessed, and watch registrations persist across Hagent
restarts. Scanned PDFs need an OCR pass before
their text can be indexed.

Embeddings can use local Ollama (`embeddinggemma` by default), OpenAI, Gemini,
or an OpenAI-compatible endpoint such as a self-hosted embedding server. API
keys are configured per knowledge base or through `OPENAI_API_KEY` and
`GEMINI_API_KEY`. If an embedding service is unavailable, the document remains
searchable with keyword retrieval; re-embed it later from the knowledge-base
page. Assigned agents receive relevant passages in both chat and issue runs,
with source labels they can cite. Retrieval is isolated to the selected
workspace and knowledge bases; public URL ingestion rejects private-network
addresses and does not follow redirects.

## Tests

```bash
pytest
```

## Status

Phase 3 adds local workspace/profile management, issue extras and timeline events,
multi-file skill bundles, squad activity, cron and webhook triggers, real git checkout,
attachments, standalone chat, and MCP protocol tool invocation in runtime loops.

The knowledge/RAG layer adds workspace-owned sources, document and URL
ingestion, pluggable embeddings, hybrid vector/keyword retrieval, per-agent
knowledge access, and citation-aware context injection.

Existing Phase 1/2 SQLite databases are upgraded additively on first start; no data reset is required.

Marketplace-specific skill discovery and public webhook exposure remain intentionally
out of scope: local archives/direct URLs are supported, and a user must expose a local
webhook endpoint through their own tunnel or reverse proxy when needed.

## Integrated terminal

Open **Terminal** in the sidebar for a VS Code-style, multi-tab PowerShell workspace. Each tab is a real Windows ConPTY session with ANSI support, resize handling, scrollback, a configurable starting directory, and a tab layout restored after page refresh. Sessions run as the Windows account that started Hagent; keep the server bound to localhost.

Terminal access for AI agents is optional and disabled by default. Enable **Allow terminal commands** on an agent and choose its starting directory. API and local tool-calling models receive an audited `terminal_execute` tool. Codex CLI, Gemini CLI, and Claude Code use their native unrestricted automation mode only when that checkbox is enabled. Commands and results are recorded as issue timeline tool calls. This permission allows arbitrary command execution with your Windows account, so enable it only for agents and projects you trust.
