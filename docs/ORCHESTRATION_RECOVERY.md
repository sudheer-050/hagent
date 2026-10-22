# Checking task execution and recovery

Assignment, execution, verification, and owner approval are separate facts. A model
turn completing does not mean its requested deliverable exists. An owner can still
approve a task with failed checks; the review page shows that verification result.

## Verify a handoff

1. Inspect the issue's Runs list. Confirm a distinct run exists for the intended
   implementer. `pending` means queued, `waiting_approval` needs execution approval,
   and `running` means the worker claimed the run.
2. If a coordinator reassigns the issue during its own run, the new run waits until
   that coordinator exits. The coordinator's response does not trigger verification
   against the implementer's unfinished work.
3. Check the implementer's output, files, tests, and installation evidence before
   accepting delivery. A closed verification issue only records that its check ran;
   read its PASS/FAIL verdict.

## Existing stalled tasks

- Inspect the latest comments and update the issue description with the intended
  scope. New runs snapshot the latest twenty comments as contextual discussion;
  agent comments cannot manufacture owner authorization.
- Reopen an owner-closed issue only when further work on it is intended. Reassign
  it to the implementer and confirm the new Run ID. Existing closed tasks are not
  reopened by installing these fixes.
- Failed verification queues rework for the current assignee, up to three failures
  since the latest assignment or successful verification. Exhaustion leaves the
  task in review for a decision. It does not keep retrying indefinitely.
- A coordinator waiting for child deliverables is blocked with a
  `verification_deferred` event. When all children are done/cancelled, it receives
  a follow-up run. Do not treat this waiting state as an implementation failure.
- Do not promise monitoring unless a scheduled monitor actually exists.

## Recurring runners

An autopilot without a status filter selects only backlog, todo, and in-progress
issues. Review, blocked, done, and cancelled issues require a deliberate status
filter or manual action. Use `create_issue` mode for recurring health snapshots;
a project-wide filter runner executes every matching issue as its configured
agent, regardless of each issue's assignee. Do not use that mode as a health check.
Run one Hagent server per database: multiple server processes each start their own
scheduler and can fire the same recurring job more than once.

## Validation

Run `python -m pytest -q` from the checkout. On Windows, if the default pytest
temporary directory is denied, use a fresh directory within the checkout:

```powershell
python -m pytest -q --basetemp=tmp/workflow-validation
```

Use a new, disposable path for each run: pytest may clear its basetemp directory.
After restarting the server, open an issue with verification history and confirm
the latest verification card appears. Preserve the live database and owner
approval history when restarting. Restart only when no agent work is active.

The desktop launcher checks that a listening port actually serves Hagent. If
another application owns port 8000, it uses an available port in 8000-8009 and
saves it in `output/hagent-local-port.txt`. The shortcut opens that selected URL
on subsequent launches. Other applications are not stopped to reclaim a port.
