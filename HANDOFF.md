# Hagent — Handoff / Status / Roadmap

Last updated: 2026-09-15, end of an extended build session that closed 10 of the 12 backlog items
below (see "Backlog closed this session"). This file exists so any future session (a
different AI, a different tool, or just a fresh context window) can pick up work on Hagent without
re-deriving everything from scratch. Read this before touching the code.

## What Hagent is

A self-hosted multi-agent orchestration platform, architecturally modeled on Multica (a commercial
tool the owner also uses). Python 3.10 + FastAPI + SQLAlchemy(SQLite) + Jinja2 dashboard + Click
CLI. Lives at `C:\Users\gsudh\Documents\projects\hagent`, private GitHub repo `sudheer-050/hagent`.
Built as a portfolio piece for a BA→AI/ML job transition, and as a genuinely-used personal tool.

## How to run it

```
cd C:\Users\gsudh\Documents\projects\hagent
python -m uvicorn hagent.web:app --host 127.0.0.1 --port 8000
```
`--host 127.0.0.1` is load-bearing, not decorative — see Security below. `launch_hagent.bat` in
the repo root does this plus opens a browser tab once the server responds.

**Gotcha discovered this session:** if you restart the server with `Start-Process` in PowerShell
without `-RedirectStandardOutput`/`-RedirectStandardError`, the log files
(`hagent-server.log`, `hagent-server-error.log`) silently stop updating and will show stale
content from whenever they were last properly redirected. Always pass both redirects when
restarting manually, or use `launch_hagent.bat`.

## CLI usage — the thing that will trip you up

`python -m hagent.cli <command>` only works when the current directory is the Hagent project root
(`cd` there first). A bare `hagent` command is **not installed** (`pip show hagent` returns nothing)
— it only works via `-m` module invocation with the project root as cwd, because Python adds cwd to
`sys.path` for `-m` invocations of local packages. This bit both me and an agent (Infrastructure
Monitor) during this session. All of Hagent's own skills (see below) have been corrected to say
`python -m hagent.cli`, prefixed with the necessary `cd`, but if you write new
instructions/skills/prompts referencing the CLI, get this right the first time.

Also: every `terminal_execute` tool call is a **fresh PowerShell process** — nothing persists
between calls (no cwd, no variables). Multi-step terminal work needs either one combined command
or separate sequential tool calls that don't depend on shared in-process state.

## Database migrations

Additive-only, in `hagent/db.py`, function `_ensure_schema()` — a plain dict of
`{table: {column: sql_type}}`. Adding a new model field means adding it to that dict too, or the
column silently never gets created on existing databases (only `Base.metadata.create_all` covers
brand-new tables). This has already bitten this session once (the `ANTIGRAVITY_CLI` enum vs. schema
mismatch — different issue, but same class of "forgot to keep two places in sync").

## Current live state (as of this handoff)

**13 agents**, all with role-specific instructions, terminal access (`terminal_enabled=True`,
working directory = `C:\Users\gsudh`), no approval gate (fully autonomous — a deliberate choice,
see Security/Risk below):

- Mika (orchestrator) — Codex primary / Claude Code backup
- Holly (owner's bridge + direct system/screen control — see below) — Claude Code primary / Codex backup
- Backend Engineer, HAI Product & Frontend Engineer, QA & Security Engineer, DevOps & Reliability
  Engineer, Data & ML Engineer — Codex primary / Claude Code backup
- Product Research & Growth Analyst, Infrastructure Monitor, Career Application Specialist,
  PR Review & Merge Gatekeeper — Claude Code primary / Codex backup
- Job Scout — local Ollama (`qwen3-fast8b`) primary / Claude Code backup
- Researcher — pre-existing test agent, untouched role-wise

**4 runtimes registered, 3 actually connected and in use:** `local-ollama` (qwen3-fast8b, free,
runs on this GPU), `Claude Code (local)` (uses the existing Claude Code login, not a separate paid
key), `Codex (local)` (uses the existing ChatGPT/OpenAI device-auth login). A 4th, Antigravity CLI
(`agy`), has a working adapter (`hagent/adapters/antigravity_cli.py`) and was verified working, but
was **removed from active use** — the owner reported it "not working at all" during a live test and
asked for it to be removed; root cause was never diagnosed. If revisiting: the adapter code still
exists, just re-create a runtime row (`hagent runtime create --name "Antigravity (local)" --type
antigravity_cli --model default`) and re-test from scratch — don't assume the old failure reason.
Grok/xAI and direct OpenAI API were explicitly ruled out (owner declined paid API keys). Gemini CLI
is dead — Google retired free individual access, redirects to Antigravity now.

**5 shared skills**, attached to varying subsets of agents:
- **Continuous Improvement Directive** — attached to all 13. Check-existing-skills-first,
  extract-shared-skill-on-2nd-3rd-repetition discipline. Explicitly states this never stops/never
  "graduates" to being done.
- **Workspace Operating Protocol** — attached to all 13. Chain of command is
  **Owner → Holly → Mika → everyone else**. Owner overrides of a normal caution are per-instance
  and must flow down the chain explicitly, never turn into a standing self-granted permission
  change by any agent.
- **Honest Counsel** — attached to all 13. No reflexive agreement, no apologizing for hard
  truths, push back on a flawed request *before* complying (not instead of), volunteer feedback on
  decision patterns. Holly specifically has a standing "ask a real probing question sometimes, be a
  thinking partner not just a translator" clause.
- **Agent Lifecycle Management** — attached to Mika and Holly. How to create a new expert agent
  via the CLI (Mika has real terminal access to `hagent`'s own CLI — she can create/configure/
  archive agents herself) and how to check `hagent agent tasks <id>` (now shows real timestamps,
  was fixed this session) to decide if an agent's been idle ~30 days and should be archived.
- **Cost-Aware Runtime Routing** — attached to Mika only. Route routine/low-stakes work to free
  local Ollama capacity, consequential work to the agent's normal cloud runtime; chunk long tasks
  into smaller sequential issues instead of one long run (works around Ollama's 16k context ceiling
  by avoiding it, not fighting it). Includes the exact `--backup-runtime` CLI flag mechanics for
  temporarily reassigning an agent's runtime for one task, then reverting.

**Squads:**
- **Intake** — Holly + Mika, so Holly has a real `delegate_<mika-id>` tool at runtime.
- **Delivery** — Mika + all 10 specialist agents, so Mika can actually delegate mid-run to a
  specialist instead of only assigning async issues.

**Verifier/critic wiring:** Backend Engineer, HAI Product & Frontend Engineer, DevOps & Reliability
Engineer, and Data & ML Engineer all have `verifier_agent_id` set to QA & Security Engineer. After
any of them completes a run successfully, QA automatically reviews it in a new issue (in an
auto-created "Verification Reviews" project) and posts PASS/FAIL back as a comment; a FAIL
automatically reopens the original issue to `in_progress`. Verified working for both outcomes with
real tests (see engine.py `_run_verifier_pass`).

**Repos linked for git worktree isolation:**
- "Auto Data Analyst" repo → "Auto Data Analyst" project (linked, not yet used for a real task)
- "Hagent" repo (this repo) → "Hagent Ops" project (linked, verified working with a real read-only
  test — isolated branch `hagent/dfaca8e9`, isolated path, main checkout untouched)
- "Worktree Test Repo" (a disposable local test repo) → "Worktree Isolation Test" project — this is
  scratch, safe to delete
Any project not linked to a repo (`project.repo_id IS NULL`) gets no isolation — issues run in the
assigned agent's normal `terminal_working_directory`, shared across concurrent runs. Link a project
with `hagent project update <project-id> --repo <repo-id>`; register an existing local checkout
with `hagent repo add --name X --local-path <path>` (added this session — doesn't require cloning).

**Holly's direct system control:** on top of her prompt-engineer/bridge role, Holly acts
*immediately herself* (not via Mika) when the owner asks her directly to read the screen or
control the computer — screenshot capture + her own file-viewing capability for screen reading;
tested, working PowerShell snippets for mouse move, click (P/Invoke `mouse_event`, since
`System.Windows.Forms` alone can't click), typing (`SendKeys`), and launching apps
(`Start-Process`, never call the .exe directly — that blocks the tool call until the app closes).
All of this is baked into her `instructions` field with real tested code, not placeholders. Fully
autonomous, no confirmation step — the owner chose this explicitly after being told the risk.

**Usage dashboard:** `/usage` page, real data. Known gap: token counts are accurate for Ollama runs
but show 0/0 for Codex and Claude Code runs — their CLI JSON output doesn't map into the
`input_tokens`/`output_tokens` fields the dashboard reads. Run counts and verification pass/fail
stats are accurate for all runtimes.

## Real bugs found and fixed this session (in case any resurface)

1. `codex_cli.py`'s `_find_codex()` silently fell back to the real installed Codex path even when
   a deliberately-custom (and broken) `command` config was given — fixed to only fall back when
   the command is the literal default `"codex"`. Same fix applied to `antigravity_cli.py`.
2. All CLI adapter subprocess calls (`claude_code.py`, `codex_cli.py`, `antigravity_cli.py`,
   `gemini_cli.py`) decoded output as Windows' default cp1252 instead of UTF-8, which threw
   `UnicodeDecodeError` on em-dashes and other non-Latin-1 characters in model output — all four
   fixed to force `encoding="utf-8", errors="replace"`.
3. `claude_code.py` and `codex_cli.py` passed the prompt as a CLI **argument**, which fails with
   "the command line is too long" once a prompt gets long enough (hit this for real with a
   moderately complex PowerShell-heavy prompt). Fixed both to pipe the prompt over **stdin**
   instead (`claude --print` and `codex exec -` both support this).
4. `hagent agent tasks <id>` didn't print run timestamps at all, only status/id/prompt — made the
   Agent Lifecycle skill's "check for ~30 days idle" instruction literally unfollowable. Fixed to
   include `run.created_at.isoformat()`.
5. `templates.TemplateResponse("name.html", {"request": request, ...})` (the old-style call) throws
   `TypeError: unhashable type: 'dict'` in this Starlette version — the working form used
   everywhere else in this codebase is `templates.TemplateResponse(request, "name.html", {...})`
   with `request` positional and NOT duplicated inside the context dict. Got this wrong once
   building the `/usage` page; fixed.
6. A schema/enum mismatch (`ANTIGRAVITY_CLI` added to the `RuntimeType` enum in `models.py` without
   restarting the already-running server process) caused `/agents`, `/runtimes`, `/workspaces` to
   500 for a while. Not a code bug, but a process-lifecycle gotcha: **the running uvicorn process
   has its own loaded copy of the Python modules — editing `.py` files does nothing to it until
   restarted.** Easy to forget mid-session when moving fast.

## What's verified working (not just written) as of this handoff

Real, independently-checked-outside-the-agent's-own-report tests, not just "the agent said it
worked": Holly→Mika delegation round trip; agent backup-runtime failover (forced a real primary
failure, confirmed the correct backup ran and the failover event logged); `terminal_execute`
running a real PowerShell command with a real timestamp back; Infrastructure Monitor's cron
autopilot manually fired once and correctly read real log content; verifier PASS and FAIL paths
(FAIL correctly reopened the issue with a specific reason); git worktree isolation (two issues on
the same repo, two branches, two directories, zero file collision, verified via `git worktree list`
and the filesystem directly, not just agent self-report); Holly's screen reading (described actual
live screen content accurately); Holly's mouse/keyboard/app-launch control (independently confirmed
via `Get-Process` that Chrome and a correctly-titled Notepad window actually existed after her
tool calls, not just her claiming so).

## Backlog closed this session (all verified with real tests, not just code review)

Everything below was open at the top of this session and is now built and independently verified:

- **PR/diff review workflow** — `hagent issue diff <id>` and `hagent issue pr <id>` (real git
  push + `gh pr create`). Verified with an actual PR opened on the real `sudheer-050/hagent`
  GitHub repo (github.com/sudheer-050/hagent/pull/1 — a harmless test PR, safe to close).
- **Sandboxed execution** — `Agent.sandbox_image` + Docker isolation via
  `mcr.microsoft.com/powershell`, verified with `Test-Path 'C:\Users'` genuinely returning `False`
  from inside the container. **Important limitation**: this only protects agents on Ollama/
  OpenAI-compatible/generic_cli runtimes. Codex and Claude Code have their own built-in shell
  tools that bypass Hagent's tool-dispatch layer entirely and run directly on the host — sandboxing
  does NOT apply to them. Since most coding agents here run on Codex, treat this as available
  infrastructure, not a blanket safety net for the current roster.
- **Session resume** — `hagent issue continue <id> --prompt "..."` genuinely resumes the prior CLI
  session (not a re-sent summary). Verified for both Claude Code and Codex with real
  memory-recall tests. Found and fixed two real bugs along the way: `codex exec resume` silently
  rejects `--sandbox`/`--color` (was causing a silent failover to a memoryless backup runtime —
  worse than failing loudly), and the verifier's PASS/FAIL detection was too brittle against
  verifiers that add a sentence of preamble before their verdict (fixed with a `\bFAIL\b`/`\bPASS\b`
  regex search instead of `startswith`).
- **Native document generation** — `python-docx`/`openpyxl`/`fpdf2` (all pre-installed, verified
  working) documented as a skill, attached to Career Application Specialist, Product Research &
  Growth Analyst, Data & ML Engineer. Verified with a real agent-generated PDF, independently
  re-opened and text-extracted to confirm it wasn't a hallucinated success claim.
- **Usage cost visibility** — Codex's adapter now uses `--json` and parses the JSONL event stream
  properly (was previously not parsing structured output at all, hence always 0/0 tokens); Claude
  Code's gap turned out to already be fixed as a side effect of the stdin-argument fix. `/usage` now
  shows real numbers for both.
- **Generic plugin architecture** — `hagent/adapters/generic_cli.py`, a config-only adapter (no
  Python needed) for any CLI that takes a prompt and returns single-shot JSON. Verified by driving
  Claude Code through it and matching the hand-written adapter's output, tokens, and session id
  exactly. Doesn't cover JSONL-event-stream CLIs (Codex's shape) - those still need a real adapter.
- **Mid-task checkpointing** — `ResumableError` in `adapters/base.py` recovers a session id from
  partial JSONL output when Codex times out mid-task; `issue continue` can resume from either a
  completed OR a checkpointed-failed run. Verified with a forced real 5-second timeout mid-task,
  followed by a successful resume that correctly recalled pre-timeout context. Codex-only for the
  same reason as session resume (Claude Code's single-JSON-blob output doesn't stream partial data
  the same way).
- **A2A (Agent2Agent) protocol support** — `/.well-known/agent-card.json` (workspace discovery) and
  `POST /a2a/agents/<id>/message` (real A2A message/task/artifact shape, synchronous only - no
  streaming/push notifications). Verified with an actual HTTP call that created a real, normal
  Hagent issue and returned a correctly-shaped Task response.

**Bonus finding while testing A2A discovery**: an agent named "Windows Desktop Automation Verifier"
(id `a7209265-600f-49be-ad49-ae24903afab7`) turned up that nobody remembers creating manually —
timestamp lines up exactly with GUI-automation testing done around 21:09 that session, and its
instructions match the exact style taught in the Agent Lifecycle Management skill. Almost certainly
Mika (or Codex acting through her terminal access) created it autonomously per that skill - a good
sign the mechanism works, except it skipped attaching the three standard skills (Continuous
Improvement, Workspace Operating Protocol, Honest Counsel), which have now been attached manually.
If you see other agents show up you don't remember creating, check `agent tasks <id>` for context
and verify they have the standard skills before assuming something's wrong.

## The last two, scoped down and also closed

Both were flagged as large enough that a rushed full version would likely be shallow/broken - the
owner asked to continue anyway, under real usage-budget pressure (partway through a Claude Pro
5-hour window). Rather than start either full build and risk running out mid-file, both got a
small, real, complete slice instead:

- **Embedded dev environment** → `/issues/<id>/diff`: a real diff-viewer page (backend already
  existed via `worktrees.compute_diff`, reused - not duplicated - from the CLI's `issue diff`),
  linked from the issue detail page. Verified with real diff content and the no-repo error path.
  Not an editor, not a live preview - just real, correct diff viewing in the browser instead of
  only the CLI.
- **Visual workflow builder** → `/autopilots/graph`: a read-only flow diagram (Trigger → Autopilot
  → Agent → Project) for every autopilot, linked from the autopilots list page. Verified against
  the real "Hagent self-health check" autopilot's actual wiring. Not a drag-and-drop canvas, not
  editable - just genuine visibility into wiring that previously only existed as separate list rows.

If either grows into the full original vision later (in-app editor with LSP, editable canvas),
treat these as the honest starting point, not a finished feature - they're deliberately narrow.

All 12 items from the original two comparisons (GitHub-native tools + commercial platforms) now
have real, verified work behind them: 10 fully built, 2 as scoped-down real slices rather than
full builds. Nothing on the list was skipped or faked.

## Security posture (deliberate, not accidental)

- Server bound to `127.0.0.1` only, explicitly (not relying on uvicorn's default) — set in
  `launch_hagent.bat` and should be set explicitly any time the server is started manually. **Do
  not change to `0.0.0.0` without adding real authentication first** — Hagent has none, and its
  agents have full terminal + screen/mouse/keyboard control of this machine.
- No login/auth on the web UI itself — acceptable only because it's loopback-only. If another user
  account or malware already has access to this physical machine, Hagent has no additional barrier
  — a known, accepted gap, not an oversight.
- Every agent has `terminal_enabled=True`, full home-directory (`C:\Users\gsudh`) reach, and
  `require_run_approval=False` (fully autonomous, no confirmation step) — the owner chose this
  explicitly, twice, after being told the specific risk each time (once for terminal access, once
  for screen/mouse/keyboard control). This is the load-bearing risk decision behind this whole
  setup; don't quietly walk it back or quietly extend it further without the same kind of explicit
  check-in.

## If you're a fresh AI session picking this up

Read this file first, then verify current state before trusting any specific claim above — things
like exact agent/skill/runtime IDs will drift as work continues, and this file will go stale. Use
`python -m hagent.cli agent list`, `skill list`, `runtime list`, `squad list`, `project list` (all
from the project root) to check current reality against what's written here, the same discipline
the Continuous Improvement Directive skill asks of the agents themselves.
