"""Project resources, skill labels, issue pull requests and the opencode runtime."""

import json
import subprocess

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hagent import db as hagent_db
from hagent.adapters import get_runtime_class
from hagent.adapters import opencode_cli
from hagent.adapters.opencode_cli import OpencodeCliRuntime, parse_events
from hagent.cli import cli
from hagent.models import Base, Issue, IssuePullRequest, Label, Project, ProjectResource, Repo, RuntimeType, Skill, Workspace
from hagent.tenancy import WorkspaceSession


def run_cli(*args):
    result = CliRunner().invoke(cli, list(args))
    return result


def created_id(result):
    assert result.exit_code == 0, result.output
    return result.output.strip().split()[-1]


def make_project(name="p"):
    return created_id(run_cli("project", "create", "--name", name))


# --- project resources ------------------------------------------------------

def test_project_resource_add_list_update_remove(cli_db):
    project_id = make_project()
    resource_id = created_id(run_cli("project", "resource", "add", project_id, "--url", "https://github.com/acme/app", "--label", "main"))

    listed = run_cli("project", "resource", "list", project_id).output
    assert "https://github.com/acme/app" in listed and "(main)" in listed

    assert run_cli("project", "resource", "update", project_id, resource_id, "--label", "primary", "--position", "5").exit_code == 0
    assert "(primary)" in run_cli("project", "resource", "list", project_id).output

    assert run_cli("project", "resource", "remove", project_id, resource_id).exit_code == 0
    assert run_cli("project", "resource", "list", project_id).output.strip() == ""


def test_project_resource_rejects_bad_url_duplicates_and_empty_update(cli_db):
    project_id = make_project()

    bad = run_cli("project", "resource", "add", project_id, "--url", "https://evil.example/acme/app")
    assert bad.exit_code != 0 and "github.com/owner/repo" in bad.output

    assert run_cli("project", "resource", "add", project_id, "--url", "https://github.com/acme/app").exit_code == 0
    dup = run_cli("project", "resource", "add", project_id, "--url", "https://github.com/acme/app")
    assert dup.exit_code != 0 and "already attached" in dup.output

    resource_id = run_cli("project", "resource", "list", project_id).output.split()[0]
    nothing = run_cli("project", "resource", "update", project_id, resource_id)
    assert nothing.exit_code != 0 and "Nothing to update" in nothing.output


def test_project_resource_cannot_be_reached_through_another_project(cli_db):
    first, second = make_project("first"), make_project("second")
    resource_id = created_id(run_cli("project", "resource", "add", first, "--url", "https://github.com/acme/app"))

    result = run_cli("project", "resource", "remove", second, resource_id)

    assert result.exit_code != 0 and "not found" in result.output
    assert "https://github.com/acme/app" in run_cli("project", "resource", "list", first).output


def test_project_create_accepts_repeatable_repo_option(cli_db):
    result = run_cli("project", "create", "--name", "p", "--repo", "https://github.com/a/one", "--repo", "https://github.com/a/two")
    project_id = created_id(result)

    listed = run_cli("project", "resource", "list", project_id).output
    assert "https://github.com/a/one" in listed and "https://github.com/a/two" in listed


def test_project_create_with_invalid_repo_creates_nothing(cli_db):
    result = run_cli("project", "create", "--name", "p", "--repo", "not-a-url")

    assert result.exit_code != 0
    assert run_cli("project", "list").output.strip() == ""


# --- skill labels -----------------------------------------------------------

def test_skill_label_add_list_remove(cli_db):
    skill_id = created_id(run_cli("skill", "create", "--name", "Research", "--content", "x"))
    label_id = created_id(run_cli("label", "create", "--name", "research"))

    assert run_cli("skill", "label", "add", skill_id, "--label", label_id).exit_code == 0
    assert run_cli("skill", "label", "add", skill_id, "--label", label_id).exit_code == 0  # idempotent
    assert run_cli("skill", "label", "list", skill_id).output.count("research") == 1

    assert run_cli("skill", "label", "remove", skill_id, "--label", label_id).exit_code == 0
    assert run_cli("skill", "label", "list", skill_id).output.strip() == ""


def test_skill_label_unknown_ids_fail_cleanly(cli_db):
    result = run_cli("skill", "label", "add", "nope", "--label", "nope")

    assert result.exit_code != 0 and "not found" in result.output


# --- issue pull requests ----------------------------------------------------

def make_issue_with_repo(cli_db, tmp_path):
    with sessionmaker(bind=cli_db, class_=WorkspaceSession)() as s:
        workspace = s.scalar(select(Workspace)) or Workspace(name="default")
        s.add(workspace)
        s.flush()
        repo = Repo(workspace_id=workspace.id, name="r", url="https://github.com/acme/app", local_path=str(tmp_path))
        s.add(repo)
        s.flush()
        project = Project(workspace_id=workspace.id, name="p", repo_id=repo.id)
        s.add(project)
        s.flush()
        issue = Issue(project_id=project.id, title="t")
        s.add(issue)
        s.commit()
        return issue.id


def test_pull_requests_empty_until_one_is_recorded(cli_db, tmp_path):
    issue_id = make_issue_with_repo(cli_db, tmp_path)
    assert run_cli("issue", "pull-requests", issue_id).output.strip() == ""

    with sessionmaker(bind=cli_db, class_=WorkspaceSession)() as s:
        s.add(IssuePullRequest(issue_id=issue_id, url="https://github.com/acme/app/pull/7", number=7, title="Fix it"))
        s.commit()

    output = run_cli("issue", "pull-requests", issue_id).output
    assert "#7" in output and "open" in output and "https://github.com/acme/app/pull/7" in output and "Fix it" in output


def test_pull_requests_refresh_discovers_and_updates_via_gh(cli_db, tmp_path, mocker):
    issue_id = make_issue_with_repo(cli_db, tmp_path)
    mocker.patch("hagent.cli.shutil.which", return_value="gh")
    payload = [{"number": 12, "url": "https://github.com/acme/app/pull/12", "title": "Add thing", "state": "MERGED"}]
    run = mocker.patch("hagent.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr=""))

    first = run_cli("issue", "pull-requests", issue_id, "--refresh")
    second = run_cli("issue", "pull-requests", issue_id, "--refresh")  # must update, not duplicate

    assert first.exit_code == 0 and "#12" in first.output and "merged" in first.output
    assert second.output.count("pull/12") == 1
    assert run.call_args.args[0][:3] == ["gh", "pr", "list"]


def test_pull_requests_refresh_without_gh_reports_it(cli_db, tmp_path, mocker):
    issue_id = make_issue_with_repo(cli_db, tmp_path)
    mocker.patch("hagent.cli.shutil.which", return_value=None)

    result = run_cli("issue", "pull-requests", issue_id, "--refresh")

    assert result.exit_code != 0 and "'gh' CLI is required" in result.output


def test_new_owned_tables_are_workspace_scoped(cli_db):
    with sessionmaker(bind=cli_db, class_=WorkspaceSession)() as s:
        ids = {}
        for name in ("a", "b"):
            ws = Workspace(name=name)
            s.add(ws)
            s.flush()
            project = Project(workspace_id=ws.id, name=name)
            s.add(project)
            s.flush()
            issue = Issue(project_id=project.id, title=name)
            s.add_all([issue, ProjectResource(project_id=project.id, ref=f"https://github.com/o/{name}")])
            s.flush()
            s.add(IssuePullRequest(issue_id=issue.id, url=f"https://github.com/o/{name}/pull/1"))
            ids[name] = ws.id
        s.commit()

    with sessionmaker(bind=cli_db, class_=WorkspaceSession)() as s:
        s.info["workspace_id"] = ids["a"]
        assert [r.ref for r in s.scalars(select(ProjectResource))] == ["https://github.com/o/a"]
        assert [r.url for r in s.scalars(select(IssuePullRequest))] == ["https://github.com/o/a/pull/1"]


# --- opencode runtime -------------------------------------------------------

# Trimmed from a real `opencode run --format json` session.
REAL_EVENTS = "\n".join(
    json.dumps(event)
    for event in [
        {"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}},
        {"type": "step_finish", "sessionID": "ses_1", "part": {"type": "step-finish", "tokens": {"input": 9264, "output": 17}}},
        {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "pong"}},
        {"type": "step_finish", "sessionID": "ses_1", "part": {"type": "step-finish", "tokens": {"input": 42, "output": 2}}},
    ]
)


def test_parse_events_collects_text_tokens_and_session():
    output, meta = parse_events("timestamp=... level=INFO log line\n" + REAL_EVENTS + "\n")

    assert output == "pong"
    assert meta == {"session_id": "ses_1", "input_tokens": 9306, "output_tokens": 19}


def test_parse_events_surfaces_error_events():
    with pytest.raises(RuntimeError, match="model exploded"):
        parse_events(json.dumps({"type": "error", "error": {"message": "model exploded"}}))


def test_opencode_type_is_registered():
    assert get_runtime_class(RuntimeType.OPENCODE_CLI) is OpencodeCliRuntime


def test_find_opencode_refuses_cmd_shim_without_native_binary(tmp_path):
    shim = tmp_path / "opencode.cmd"
    shim.write_text("@echo off")

    assert opencode_cli._find_opencode(str(shim)) is None

    native = tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    native.parent.mkdir(parents=True)
    native.write_text("")
    assert opencode_cli._find_opencode(str(shim)) == str(native)


@pytest.fixture()
def fake_opencode(mocker, tmp_path):
    exe = tmp_path / "opencode.exe"
    exe.write_text("")
    return mocker.patch(
        "hagent.adapters.opencode_cli.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, stdout=REAL_EVENTS, stderr=""),
    ), str(exe)


def test_run_passes_prompt_verbatim_and_denies_tools_by_default(fake_opencode):
    run, exe = fake_opencode
    prompt = 'say "hi" & echo pwned | more %PATH%\nsecond line'

    result = OpencodeCliRuntime("ollama/llama3.2:latest", {"command": exe}).run(prompt, context="ctx")

    args = run.call_args.args[0]
    assert args[:2] == [exe, "run"] and args[2] == f"ctx\n\n{prompt}"
    assert args[args.index("-m") + 1] == "ollama/llama3.2:latest"
    assert run.call_args.kwargs["env"]["OPENCODE_CONFIG_CONTENT"] == json.dumps({"permission": {"bash": "deny", "edit": "deny", "webfetch": "deny"}})
    assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert (result.output, result.session_id, result.input_tokens, result.output_tokens) == ("pong", "ses_1", 9306, 19)


def test_terminal_enabled_does_not_deny_tools_and_default_model_is_omitted(fake_opencode, monkeypatch):
    run, exe = fake_opencode
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)

    OpencodeCliRuntime("default", {"command": exe, "terminal_enabled": True}).run("go")

    assert "-m" not in run.call_args.args[0]
    assert "OPENCODE_CONFIG_CONTENT" not in run.call_args.kwargs["env"]


def test_run_reports_failures(mocker, tmp_path):
    exe = tmp_path / "opencode.exe"
    exe.write_text("")
    mocker.patch("hagent.adapters.opencode_cli.subprocess.run", return_value=subprocess.CompletedProcess([], 3, stdout="", stderr="boom"))

    with pytest.raises(RuntimeError, match=r"exit 3\): boom"):
        OpencodeCliRuntime("default", {"command": str(exe)}).run("go")
    with pytest.raises(RuntimeError, match="not installed"):
        OpencodeCliRuntime("default", {"command": str(tmp_path / "missing.exe")}).run("go")
