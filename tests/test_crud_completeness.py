"""Edit/remove operations that complete the lifecycle of things Hagent could already create."""

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from hagent import db as hagent_db
from hagent.cli import cli
from hagent.models import Agent, Attachment, Comment, Runtime, RuntimeType, Workspace


def run_cli(*args):
    return CliRunner().invoke(cli, list(args))


def created_id(result):
    assert result.exit_code == 0, result.output
    return result.output.splitlines()[0].split()[-1]


@pytest.fixture()
def env(cli_db):
    project = created_id(run_cli("project", "create", "--name", "P"))
    issue = created_id(run_cli("issue", "create", "--project", project, "--title", "an issue"))
    with hagent_db.SessionLocal() as s:
        ws = s.scalar(select(Workspace))
        runtime = Runtime(workspace_id=ws.id, name="r", type=RuntimeType.OLLAMA, model="m", config_json="{}")
        s.add(runtime)
        s.flush()
        agents = [Agent(workspace_id=ws.id, runtime_id=runtime.id, name=name, instructions="x") for name in ("Coder", "Reviewer")]
        s.add_all(agents)
        s.commit()
        return {"issue": issue, "coder": agents[0].id, "reviewer": agents[1].id}


def lines(*args):
    result = run_cli(*args)
    assert result.exit_code == 0, result.output
    return [line for line in result.output.splitlines() if line.strip()]


# --- comments ---------------------------------------------------------------

def test_comment_threads_edit_resolve_and_delete(env):
    issue = env["issue"]
    root = created_id(run_cli("issue", "comment", "add", issue, "--body", "question?"))
    reply = created_id(run_cli("issue", "comment", "add", issue, "--body", "answer", "--parent", root[:8]))
    other = created_id(run_cli("issue", "comment", "add", issue, "--body", "unrelated"))

    listing = lines("issue", "comment", "list", issue)
    assert listing[1].startswith("    ") and "answer" in listing[1]  # replies are indented
    assert len(lines("issue", "comment", "list", issue, "--roots-only")) == 2
    assert len(lines("issue", "comment", "list", issue, "--tail", "1")) == 1

    assert run_cli("issue", "comment", "update", issue, root, "--body", "question, edited?").exit_code == 0
    assert "edited" in run_cli("issue", "comment", "list", issue).output

    run_cli("issue", "comment", "resolve", issue, root)
    assert "(resolved)" in run_cli("issue", "comment", "list", issue).output
    assert all("question" not in line for line in lines("issue", "comment", "list", issue, "--unresolved"))
    run_cli("issue", "comment", "unresolve", issue, root)
    assert "(resolved)" not in run_cli("issue", "comment", "list", issue).output

    assert "Deleted 2" in run_cli("issue", "comment", "delete", issue, root).output  # the comment and its reply
    with hagent_db.SessionLocal() as s:
        assert [c.id for c in s.scalars(select(Comment))] == [other]
    assert reply not in run_cli("issue", "comment", "list", issue).output


def test_deleting_a_comment_keeps_its_attachments_on_the_issue(env):
    comment = created_id(run_cli("issue", "comment", "add", env["issue"], "--body", "see file"))
    with hagent_db.SessionLocal() as s:
        issue_id = s.scalar(select(Comment.issue_id))
        s.add(Attachment(issue_id=issue_id, comment_id=comment, filename="f.txt", path="x"))
        s.commit()

    assert run_cli("issue", "comment", "delete", env["issue"], comment).exit_code == 0

    with hagent_db.SessionLocal() as s:
        kept = s.scalar(select(Attachment))
        assert kept is not None and kept.comment_id is None and kept.issue_id == issue_id


def test_comment_errors_are_clear(env):
    a = created_id(run_cli("issue", "comment", "add", env["issue"], "--body", "one"))

    assert "not found" in run_cli("issue", "comment", "update", env["issue"], "nope", "--body", "x").output
    assert "not found" in run_cli("issue", "comment", "add", env["issue"], "--body", "x", "--parent", "nope").output
    assert run_cli("issue", "comment", "delete", "DEF-404", a).exit_code != 0


# --- labels, properties, metadata, subscribers -----------------------------

def test_issue_labels_can_be_listed_and_removed(env):
    label = created_id(run_cli("label", "create", "--name", "bug"))
    run_cli("issue", "label", "add", env["issue"], "--label", label)

    assert "bug" in run_cli("issue", "label", "list", env["issue"]).output
    assert run_cli("issue", "label", "remove", env["issue"], "--label", label).exit_code == 0
    assert run_cli("issue", "label", "list", env["issue"]).output.strip() == ""
    assert run_cli("issue", "label", "remove", env["issue"], "--label", label).exit_code == 0  # idempotent


def test_issue_properties_can_be_listed_and_unset(env):
    prop = created_id(run_cli("property", "create", "--name", "Team", "--type", "text"))
    run_cli("issue", "property", "set", env["issue"], "--property", prop, "--value", "infra")

    assert "Team=infra" in run_cli("issue", "property", "list", env["issue"]).output
    assert run_cli("issue", "property", "unset", env["issue"], "--property", prop).exit_code == 0
    assert run_cli("issue", "property", "list", env["issue"]).output.strip() == ""
    assert run_cli("issue", "property", "unset", env["issue"], "--property", prop).exit_code != 0


def test_metadata_list_get_key_delete_and_issue_keys(env):
    run_cli("issue", "metadata", "set", "DEF-1", "--key", "build", "--value", "42")  # by key, not UUID
    run_cli("issue", "metadata", "set", env["issue"], "--key", "env", "--value", "prod")

    assert lines("issue", "metadata", "list", "DEF-1") == ["build=42", "env=prod"]
    assert lines("issue", "metadata", "get", env["issue"], "--key", "build") == ["42"]
    assert run_cli("issue", "metadata", "get", env["issue"], "--key", "missing").exit_code != 0
    assert run_cli("issue", "metadata", "delete", env["issue"], "--key", "build").exit_code == 0
    assert lines("issue", "metadata", "list", env["issue"]) == ["env=prod"]
    assert run_cli("issue", "metadata", "delete", env["issue"], "--key", "build").exit_code != 0


def test_subscribers_can_be_removed_and_use_issue_keys(env):
    run_cli("issue", "subscriber", "add", "DEF-1", "--name", "sudheer")

    assert "sudheer" in run_cli("issue", "subscriber", "list", env["issue"]).output
    assert run_cli("issue", "subscriber", "remove", "DEF-1", "--name", "sudheer").exit_code == 0
    assert run_cli("issue", "subscriber", "list", env["issue"]).output.strip() == ""
    assert "not subscribed" in run_cli("issue", "subscriber", "remove", env["issue"], "--name", "sudheer").output
    assert run_cli("issue", "subscriber", "add", "DEF-404", "--name", "x").exit_code != 0


# --- squads, skills, agents, MCP -------------------------------------------

def test_squad_members_can_be_listed_re_roled_and_removed(env):
    squad = created_id(run_cli("squad", "create", "--name", "Ops"))
    run_cli("squad", "member", "add", squad, "--agent", env["coder"])
    run_cli("squad", "member", "add", squad, "--agent", env["reviewer"], "--role", "lead")

    listing = lines("squad", "member", "list", squad)
    assert len(listing) == 2 and "lead" in listing[1]

    assert "now leader" in run_cli("squad", "member", "set-role", squad, "--agent", "coder", "--role", "leader").output
    assert "leader" in run_cli("squad", "member", "list", squad).output
    assert run_cli("squad", "member", "remove", squad, "--agent", "Coder").exit_code == 0
    assert len(lines("squad", "member", "list", squad)) == 1
    assert "not in squad" in run_cli("squad", "member", "remove", squad, "--agent", "Coder").output


def test_skill_files_can_be_added_updated_and_deleted(env, tmp_path):
    skill = created_id(run_cli("skill", "create", "--name", "S", "--content", "x"))

    assert "Added" in run_cli("skill", "files", "upsert", skill, "notes.md", "--content", "v1").output
    assert "Updated" in run_cli("skill", "files", "upsert", skill, "notes.md", "--content", "v2").output
    source = tmp_path / "src.txt"
    source.write_text("from file", encoding="utf-8")
    run_cli("skill", "files", "upsert", skill, "b.txt", "--content-file", str(source))
    assert run_cli("skill", "files", "get", skill, "notes.md").output.strip() == "v2"
    assert run_cli("skill", "files", "get", skill, "b.txt").output.strip() == "from file"

    assert run_cli("skill", "files", "delete", skill, "notes.md").exit_code == 0
    assert "notes.md" not in run_cli("skill", "files", "list", skill).output
    assert run_cli("skill", "files", "delete", skill, "notes.md").exit_code != 0


@pytest.mark.parametrize("name", ["../escape.txt", "a/../../b", "/etc/passwd"])
def test_skill_file_names_cannot_escape_the_bundle(env, name):
    skill = created_id(run_cli("skill", "create", "--name", "S", "--content", "x"))

    result = run_cli("skill", "files", "upsert", skill, name, "--content", "x")

    assert result.exit_code != 0 and "relative" in result.output


def test_skill_files_need_exactly_one_content_source(env):
    skill = created_id(run_cli("skill", "create", "--name", "S", "--content", "x"))

    assert run_cli("skill", "files", "upsert", skill, "f", "--content", "a", "--content-file", "b").exit_code != 0
    assert run_cli("skill", "files", "upsert", skill, "f").exit_code != 0


def test_agent_skills_can_be_listed_and_replaced(env):
    one = created_id(run_cli("skill", "create", "--name", "One", "--content", "x"))
    two = created_id(run_cli("skill", "create", "--name", "Two", "--content", "x"))
    run_cli("agent", "skills", "add", env["coder"], "--skill", one)

    assert "One" in run_cli("agent", "skills", "list", env["coder"]).output
    assert "now has 1" in run_cli("agent", "skills", "set", env["coder"], "--skill", two).output
    listing = run_cli("agent", "skills", "list", env["coder"]).output
    assert "Two" in listing and "One" not in listing
    assert "now has 0" in run_cli("agent", "skills", "set", env["coder"]).output
    assert run_cli("agent", "skills", "set", env["coder"], "--skill", "nope").exit_code != 0


def test_mcp_servers_can_be_updated_and_listed_per_agent(env):
    server = created_id(run_cli("workspace", "mcp", "add", "--name", "tools", "--command", "npx"))
    run_cli("agent", "mcp", "add", env["coder"], "--server", server)

    assert "tools" in run_cli("agent", "mcp", "list", env["coder"]).output
    assert run_cli("workspace", "mcp", "update", server, "--name", "toolbox", "--command", "uvx").exit_code == 0
    assert "toolbox" in run_cli("workspace", "mcp", "list").output
    assert run_cli("workspace", "mcp", "update", server).exit_code != 0  # nothing to update
    assert run_cli("workspace", "mcp", "update", server, "--transport", "sse").exit_code != 0  # sse needs a url


def test_version_command():
    assert run_cli("version").output.startswith("hagent ")
