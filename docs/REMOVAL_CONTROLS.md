# Removal controls

Each app section links to **Delete or archive items**, a searchable removal list.
Projects, issues, agents, skills, squads, autopilots, repositories, workspaces,
and squad memberships also expose direct actions in their pages or detail views.
Runtime removal uses the same dependency policy as the shared workflow.

The removal list includes projects, issues, agents, runtimes, squads, skills,
autopilots, triggers, repositories, labels, properties, comments, attachments,
project resources, squad memberships, subscribers, metadata, skill files,
lessons, chats, messages, memories, routing policies, workspaces, and saved
terminal sessions. Connections use their existing Disconnect action, including
provider revocation. Built-in application catalog entries are not user-created.

Agents, runtimes, squads, and properties are archived to retain history.
Other deletions show a confirmation with counts of owned records to be removed.
Project deletion includes issues, subissues, comments, and run history. Workspace
deletion includes its contents, including archived records; the user must first
switch to another workspace. Disk files and remote repositories are retained.

Active runs, pending chats, running autopilots, and active terminal sessions block
deletion of their owning records. Agents used as verifiers, assigned open issues,
or enabled autopilots cannot be archived until those dependencies are resolved.
Runtime dependencies only count current agents. Project autopilots must be
removed before their project to prevent unintentionally broadening their scope.
Terminal history and workspace removal require owner access.

Removal is transactional. Owned records cascade according to an explicit policy;
optional outside references are detached and required outside references block
the operation. Workspace scoping also applies to individual chat messages.

Regression coverage: `tests/test_removal.py` and `tests/test_runtime_actions.py`.
