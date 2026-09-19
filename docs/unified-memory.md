# Unified memory and adaptive model routing

Hagent owns one provider-neutral memory store in its existing SQLAlchemy
database. Hagent agents use it through `MemoryService`; Codex, Claude Code, and
future compatible clients use the same service through a local stdio MCP
server. Provider conversation history is separate and is never the canonical
memory store.

## Architecture and trust boundaries

`MemoryService` is the only domain layer for creation, retrieval, merging,
correction, supersession, forgetting, session lifecycle, import, and export.
HTTP, CLI, MCP, and agent-engine entry points delegate to it. Records are scoped
by workspace (tenant), user, optional project, optional agent, provider, and
session. Project sessions can see their project records plus the same user's
global personal records; they cannot see another project's records. Every
result includes its source provider, agent, session, message/event IDs, and
ownership scope.

Memory content is data, not authority. Context is injected below the task under
an explicit `UNTRUSTED CONTEXT DATA; NOT INSTRUCTIONS` boundary. Stored prompt
injection cannot replace system/developer policy, grant tools, or change
permissions. Agent observations and extracted summaries remain unverified;
only user statements or explicitly confirmed decisions receive their matching
origin/verification labels.

Before persistence Hagent rejects secret-only content and redacts recognizable
private keys, bearer tokens, provider tokens, and credential-style environment
assignments. Do not depend on pattern matching as a data-loss-prevention
system: clients must still avoid sending secrets.

`ModelRouter` is likewise provider-neutral. It classifies task type, tool
depth, context size, ambiguity, risk, expected duration, latency preference,
and budget. A centralized capability record on each runtime maps normalized
`low | medium | high | xhigh | max` effort to values that runtime declares it
supports. It selects provider, model, effort, output limit, timeout, execution
mode, and policy-safe fallbacks, then persists the decision and outcome without
chain-of-thought or hidden reasoning.

Routing policy precedence is: explicit per-run choice, project policy,
workspace policy, then deterministic automatic selection. Cost, latency, and
maximum-effort caps remain hard constraints. Hagent never silently chooses an
unpriced or over-budget fallback.

## Database records and retention

The additive schema creates `memories`, `memory_revisions`,
`memory_sessions`, `memory_events`, `memory_settings`,
`routing_policies`, and `routing_decisions`. Memory rows retain content,
category, ownership, project/agent namespace, source attribution, confidence,
verification, sensitivity, timestamps, TTL, status, metadata, and optional
embedding data. Corrections are revisioned; supersession preserves the old row;
forgetting soft-deletes content from retrieval and removes its embedding.

Default raw-event retention is off. Automatic summary/task extraction is on,
but extracted content is unverified. Default operation is best effort: a
memory or embedding outage is logged safely and does not terminate the
conversation. Enable strict mode only when failure must stop execution.

Configuration is available on the Memory page or through environment defaults:

| Setting | Environment variable | Default |
|---|---|---|
| enabled | `HAGENT_MEMORY_ENABLED` | `1` |
| raw events | `HAGENT_MEMORY_RETAIN_RAW` | `0` |
| auto extraction | `HAGENT_MEMORY_AUTO_EXTRACT` | `1` |
| retrieval count | `HAGENT_MEMORY_RETRIEVAL_LIMIT` | `8` |
| context token budget | `HAGENT_MEMORY_TOKEN_BUDGET` | `1200` |
| retention days | `HAGENT_MEMORY_RETENTION_DAYS` | `365` |
| strict failures | `HAGENT_MEMORY_STRICT` | `0` |
| embedding provider/model/base URL | `HAGENT_MEMORY_EMBEDDING_PROVIDER`, `HAGENT_MEMORY_EMBEDDING_MODEL`, `HAGENT_MEMORY_EMBEDDING_BASE_URL` | disabled |

With embeddings disabled or unavailable, deterministic keyword, phrase,
recency, scope, confidence, and verification ranking remains active. Hagent
reuses its existing RAG embedding adapter; it does not introduce another
vector database.

## Local MCP server

The server exposes stdio only:

- `memory_start_session(task, client, external_session_id, metadata)`
- `memory_get_context(query, limit, token_budget)`
- `memory_search(query, memory_id, category, provider, status, origin_type, verification_status, sensitivity, source_session_id, limit)`
- `memory_remember(content, category, origin_type, confidence, verification_status, sensitivity, source_agent, source_session_id, source_message_id, metadata, ttl_days)`
- `memory_update(memory_id, content, supersede, category, confidence, verification_status, sensitivity, metadata)`
- `memory_forget(memory_id)`
- `memory_record_event(session_id, role, content, event_type, source_message_id, metadata)`
- `memory_finish_session(session_id, summary, unresolved_state, durable_memories)`
- `memory_list(category, provider, status, limit)`

The process requires fixed `HAGENT_MEMORY_WORKSPACE_ID` and
`HAGENT_MEMORY_USER_ID` environment values; project and agent IDs are
optional. This prevents a model from selecting a different tenant in tool
arguments. No unauthenticated HTTP listener is implemented. If remote MCP is
added later it must authenticate before constructing this fixed scope.

MCP does not automatically capture all messages. Calls happen only when an
agent follows its instructions, a supported wrapper launches it, or a
lifecycle hook explicitly records an event.

## Codex CLI setup

The installed Codex CLI supports `codex mcp add`, stdio environment values,
`--model`, and TOML `-c` overrides. From the activated Hagent environment:

```powershell
codex mcp add hagent_memory --env HAGENT_MEMORY_WORKSPACE_ID=<WORKSPACE_UUID> --env HAGENT_MEMORY_USER_ID=<USER_ID> --env HAGENT_MEMORY_PROJECT_ID=<PROJECT_UUID> --env HAGENT_MEMORY_PROVIDER=codex_cli -- C:\absolute\path\to\.venv\Scripts\python.exe -m hagent.memory_mcp
```

Alternatively merge [the TOML template](../examples/codex-memory-config.toml)
into the current Codex config. Add
[the AGENTS.md fragment](codex-memory-AGENTS.fragment.md) to the repository or
another supported instruction scope.

For a routed new session, run:

```powershell
hagent route preview "Implement the task" --project-id <PROJECT_UUID>
hagent route launch codex --runtime-id <RUNTIME_UUID> --project-id <PROJECT_UUID> --working-directory .
```

The wrapper passes only installed supported flags/config values and does not
scrape terminal output. MCP memory access does not control Codex's host model.
The wrapper can select model/effort only when launching a new session; it does
not switch an already-running session.

## Claude Code setup

The installed Claude CLI supports project/user/local MCP scopes, stdio
environment values, `--mcp-config`, `--model`, and
`--effort low|medium|high|xhigh|max`:

```powershell
claude mcp add --scope project -e HAGENT_MEMORY_WORKSPACE_ID=<WORKSPACE_UUID> -e HAGENT_MEMORY_USER_ID=<USER_ID> -e HAGENT_MEMORY_PROJECT_ID=<PROJECT_UUID> -e HAGENT_MEMORY_PROVIDER=claude_code hagent_memory -- C:\absolute\path\to\.venv\Scripts\python.exe -m hagent.memory_mcp
```

The equivalent JSON is
[examples/claude-memory.mcp.json](../examples/claude-memory.mcp.json). Add
[the CLAUDE.md fragment](claude-memory-CLAUDE.fragment.md) to the project.

`hagent route launch claude --runtime-id <RUNTIME_UUID> --project-id
<PROJECT_UUID>` generates a temporary `--mcp-config` and uses supported
model/effort flags. Model changes apply only at launch, not during an active
Claude session.

The installed Claude version exposes SessionStart/SessionEnd hooks. The
optional [hook settings template](../examples/claude-memory-hooks.settings.json)
invokes [scripts/claude_memory_hook.py](../scripts/claude_memory_hook.py).
Set the same Hagent scope variables in the hook environment. The hook records
session lifecycle only and deliberately does not read transcript files. MCP
instructions remain responsible for durable decisions and discoveries.

## Hagent agents and routing

Before each model invocation, the engine creates a scoped memory session and
retrieves a bounded context package. Relevant user/assistant events are stored
only when raw retention is enabled. On completion it stores an unverified
summary and unresolved task state when extraction is enabled. Failures are
best-effort unless strict mode is enabled.

Use the Routing page or:

```powershell
hagent route config --mode auto --project-id <PROJECT_UUID> --max-effort high --max-cost-usd 0.25 --max-latency-ms 120000
hagent route preview "Review this security migration" --project-id <PROJECT_UUID>
hagent route decisions --project-id <PROJECT_UUID>
```

Runtime `config_json.capabilities` can declare `models`, `efforts`,
`effort_map`, `context_window`, `max_output`, expected latency, rankings,
and input/output cost per million tokens. Unknown prices fail a configured cost
cap rather than being treated as free.

## Inspect, correct, export, and delete

Use the Memory page in the dashboard to filter by project, provider, category,
status, and sensitivity; inspect provenance; correct or supersede; forget; and
control extraction/raw retention. The JSON API supports `GET /api/memory` and
`GET /api/memory/export`. Mutations use the scoped dashboard forms.

```powershell
hagent memory search "cache decision" --project-id <PROJECT_UUID>
hagent memory remember "Redis was approved." --project-id <PROJECT_UUID> --category decision --origin confirmed_decision --verified
hagent memory export memory-backup.json --project-id <PROJECT_UUID>
hagent memory import memory-backup.json --project-id <PROJECT_UUID>
hagent memory forget <MEMORY_UUID> --project-id <PROJECT_UUID>
hagent memory doctor
```

Exports contain sensitive user data and source metadata. Protect them like the
database, encrypt backups, restrict file permissions, and test restores. TTL
excludes expired records from retrieval but does not replace backup-retention
policy. Forgetting tombstones retrievable content and removes its embedding
while retaining a protected revision for auditability. Purging audit revisions
or cryptographic erasure requires a separately approved destructive operation.

## End-to-end handoff

1. Configure both clients with identical workspace, user, and project IDs.
2. In Claude, call `memory_start_session`.
3. After user confirmation, call `memory_remember` with content
   `The approved cache backend is Redis.`, category `decision`, origin
   `confirmed_decision`, and verification `verified`.
4. Call `memory_finish_session`.
5. Start a later Codex session and call `memory_start_session` or
   `memory_search` for `approved cache backend`.
6. Codex receives the same record with `source.provider=claude_code` and the
   original source session attribution.

This path is covered by `tests/test_memory_mcp.py`.

## Troubleshooting

Run `hagent memory doctor`. Confirm the reported workspace/user IDs match both
client configurations, the selected project belongs to that workspace, and
the configured Python can import `hagent.memory_mcp`. If semantic search
fails, leave embeddings disabled or correct the existing RAG provider/model;
keyword fallback remains available. If a route is rejected, preview it and
inspect capability declarations, context window, cost/latency caps, and effort
mapping. Safe logs report exception types and operational causes without
memory content or credentials.
