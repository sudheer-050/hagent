# Planner plus local coder: running a small Ollama model under a stronger agent

This page describes a way to combine two kinds of runtime that Hagent already supports, with no
code changes:

- a **planner** agent on a strong CLI runtime (Claude Code or Codex CLI) that designs, writes tests,
  verifies and commits, and
- a **local coder** agent on an Ollama runtime that only turns one fully specified step into one
  file.

The planner does the thinking and all file writes. The local model does the typing. The idea is that
a small model (for example a 14B coder model) is unreliable at open-ended work but reasonable at
small, precisely specified, single-file tasks, and the planner's tests catch its mistakes.

The example prompts are in [`examples/local-coder/`](../examples/local-coder/).

## What it uses

Everything here is an existing Hagent feature:

| Piece | Hagent feature |
|---|---|
| Local model | an `ollama` runtime (`base_url`, `num_ctx`, `timeout` in its config) |
| Planner and coder | two agents, each bound to a runtime |
| How to write a step | a skill attached to the planner |
| Handing work over | sub-issues with `--parent`, `--assignee` and `--stage` |
| Reading results | `hagent issue runs` and `hagent issue run-messages` |

The Ollama server does not have to be on the same machine as Hagent. Set the runtime's `base_url` to
wherever it is reachable, for example a port forwarded over SSH to a GPU machine:

```text
ssh -N -L 127.0.0.1:11435:127.0.0.1:11434 <gpu-host>      # runtime base_url = http://127.0.0.1:11435
```

Keep the Ollama port off the open network; it has no authentication.

## Setup

1. **Runtime for the local model.** Give it a large enough context. Ollama's default context is
   small and silently truncates long prompts.

   ```text
   hagent runtime create --name local-coder-ollama --type ollama --model qwen2.5-coder:14b \
       --config '{"base_url": "http://127.0.0.1:11434", "num_ctx": 16384, "timeout": 900, "think": false}'
   ```

   On the Ollama server, `OLLAMA_CONTEXT_LENGTH=16384` (and, on small GPUs, `OLLAMA_FLASH_ATTENTION=1`
   and `OLLAMA_KV_CACHE_TYPE=q8_0`) keep the model resident and fast. `OLLAMA_NUM_PARALLEL=1` makes
   steps queue instead of competing for the GPU.

2. **Runtime for the planner.** An existing Claude Code or Codex CLI runtime works. If you want the
   planner confined to a dedicated repo, create a runtime whose config has a `working_directory`:

   ```text
   hagent runtime create --name planner --type claude_code --model default \
       --config '{"working_directory": "<path to a dedicated git repo>", "timeout": 1500}'
   ```

3. **Agents.** Use [`local-coder-agent.txt`](../examples/local-coder/local-coder-agent.txt) as the
   coder's instructions and [`planner-agent.txt`](../examples/local-coder/planner-agent.txt) for the
   planner (fill in the two placeholders). Name the coder agent exactly as the planner prompt refers to
   it (`Local Coder`). Optionally set the planner's backup runtime with `hagent agent update --backup-runtime`.

4. **Skill.** Create it from [`skill-local-model-step-specs.md`](../examples/local-coder/skill-local-model-step-specs.md)
   and attach it to the planner:

   ```text
   hagent skill create --name "Local model step specs" --file skill-local-model-step-specs.md
   hagent agent skills add <planner id> --skill <skill id>
   ```

5. **Planner terminal access.** The planner has to run commands and write files, so it needs terminal
   access with its working directory set. Do this in the agent's settings in the dashboard (the
   `hagent agent` commands do not set it). The coder needs no terminal access and should not have it.

6. **Run it.** Create an issue assigned to the planner with status `todo`. See
   [`example-task.txt`](../examples/local-coder/example-task.txt) for the level of detail that worked.

## How a task runs

1. The planner writes the tests first and commits them.
2. It creates one sub-issue per file for the local coder, grouped into stages, each written in the
   step format from the skill (goal, file, pasted context, numbered task, do-nots, acceptance, output).
3. The local coder replies with the whole file. Hagent shows only the first 600 characters of a run
   in a stage summary, so the planner reads the full text with `hagent issue runs <step id>` and
   `hagent issue run-messages <run id>`.
4. The planner saves the file, runs the tests, and either continues or sends one corrective step that
   quotes the exact error. After two failures on the same step it writes that file itself.
5. Hagent wakes the planner each time a stage finishes, so the planner runs several times per task.
   That is how the stage barrier works, not a loop.

## Things to know

These are observations from trial runs, not guarantees.

- **Small tasks only.** Results were good for single-file modules of roughly 20 to 80 lines with a
  precise spec. In early runs the model was wrong the first time on about half of the steps; the tests
  caught every mistake. Do not expect it to design, refactor across files or debug open-ended problems.
- **False refusals.** The model sometimes answered `BLOCKED: the file does not exist` for a file it was
  asked to create. The sample prompts tell it that new files are expected, and tell the planner to
  resend once. Keep that wording.
- **The spec is the program.** The model implements exactly what the step says. A self-contradictory
  spec produces a program that is wrong in the same way and can still pass tests written from the same
  spec. Read specs for contradictions, and test the boundary cases.
- **Cancelling.** Cancelling a planner run did not stop its already created sub-issues or its CLI
  subprocess. A cancelled planner kept creating steps and wrote into the repo while a second planner
  worked there. Cancel the children first, then the parent, check `hagent queue`, and use a fresh project
  for the next attempt.
- **CLI database.** The `hagent` CLI uses a `hagent.db` relative to the current directory unless
  `HAGENT_DB_PATH` is set. A planner running in another directory must set it, or its sub-issues go to a
  stray database and nothing happens. The planner prompt does this.
- **Inherited environment.** Claude Code and Codex CLI runs inherit the environment of the `hagent serve`
  process. A server started from inside a sandboxed session (proxy variables pointing nowhere, network
  disabled) makes every CLI run fail with connection errors. Start the server from a normal terminal.
- **Permissions.** For an agent with terminal access, Hagent runs Claude Code with permission prompts
  skipped (`--dangerously-skip-permissions`) and Codex with its sandbox bypassed
  (`--dangerously-bypass-approvals-and-sandbox`). Without terminal access, Claude Code cannot write files
  in `--print` mode and Codex runs read-only, which is why the planner needs terminal access. A
  terminal-enabled planner has the full rights of the user running Hagent; its working directory is a
  starting point, not a boundary. Use a dedicated account, container or VM for anything untrusted (see the
  security notes in `HANDOFF.md`).
