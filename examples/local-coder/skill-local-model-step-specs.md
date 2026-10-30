# Step specs for the local coding model

The local model (a small coder model served by Ollama) cannot explore a repo, remember earlier
steps, or make design decisions. Every sub-issue you assign to it must be fully self-contained.
Write each one with exactly these sections:

GOAL: one sentence.
FILE: the single file to create or change (exact path). Never more than one file per step.
CONTEXT: paste the CURRENT full contents of FILE (or "new file"), plus the exact signatures,
  types and constants it must use from other files. Never say "see file X": paste it.
TASK: numbered, concrete changes. Give function names, parameter types, return types, edge
  cases and error behaviour. No open choices; you decide everything.
DO NOT: touch other files, add dependencies, rename existing symbols, or add features not listed.
ACCEPTANCE: the exact command you will run (tests/lint) and the output that means success.
OUTPUT: the complete final contents of FILE in one fenced block, preceded by a line `FILE: <path>`.

Rules for planning:
- Keep each step small enough that FILE + CONTEXT stays under about 6,000 tokens.
- Put steps with no dependency on each other in the same stage; later steps in a later stage.
- Write the tests (or acceptance script) yourself or in an earlier stage, before the code they check.
- After a stage finishes, read each sub-issue's FULL run output (not the truncated summary),
  write it to disk, run the ACCEPTANCE command, and only then start the next stage.
- If a step fails, create ONE corrective step that quotes the exact error and the failing output.
  If the same step fails twice, do it yourself instead of retrying the local model.
