"""Per-issue git worktree isolation.

When an issue's project is linked to a real local git repo, each issue gets its own
git worktree on its own branch, so two agents (or two runs) never touch the same
working copy of the same repo at once. Falls back to no isolation (returns None)
whenever the repo isn't configured or isn't a real git checkout - existing
non-git-backed agent behavior is unaffected.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def branch_name_for_issue(issue_id: str) -> str:
    return f"hagent/{issue_id[:8]}"


def base_branch(repo_local_path: str) -> str | None:
    """The branch an issue's work should be compared/merged against - whatever the
    main checkout currently has checked out. Returns None if that can't be determined
    (e.g. detached HEAD)."""
    result = _run_git(["branch", "--show-current"], cwd=repo_local_path)
    branch = result.stdout.strip()
    return branch or None


def worktree_path_for_issue(repo_local_path: str, issue_id: str) -> str:
    repo_root = Path(repo_local_path).resolve()
    return str(repo_root.parent / ".hagent-worktrees" / repo_root.name / issue_id)


def compute_diff(repo_local_path: str, issue_id: str) -> str | None:
    """The real diff between an issue's worktree branch and the repo's base branch, or
    None if no worktree exists yet for this issue (it hasn't run against this repo)."""
    worktree_path = worktree_path_for_issue(repo_local_path, issue_id)
    if not Path(worktree_path).is_dir():
        return None
    base = base_branch(repo_local_path)
    if not base:
        return None
    branch = branch_name_for_issue(issue_id)
    result = _run_git(["diff", f"{base}...{branch}"], cwd=worktree_path)
    if result.returncode != 0:
        return None
    return result.stdout or "(no changes)"


def ensure_worktree(repo_local_path: str, issue_id: str) -> str | None:
    """Return the working directory this issue's run should use, creating a
    dedicated git worktree + branch on first use. Returns None if repo_local_path
    isn't a usable git checkout, so the caller can fall back to normal behavior.
    """
    repo_root = Path(repo_local_path).expanduser()
    if not repo_root.is_dir() or not (repo_root / ".git").exists():
        return None

    worktree_path = worktree_path_for_issue(str(repo_root), issue_id)
    if Path(worktree_path).is_dir():
        return worktree_path

    branch = branch_name_for_issue(issue_id)
    Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)

    branch_exists = _run_git(["rev-parse", "--verify", branch], cwd=str(repo_root)).returncode == 0
    args = ["worktree", "add", worktree_path] if branch_exists else ["worktree", "add", "-b", branch, worktree_path]
    result = _run_git(args, cwd=str(repo_root))
    if result.returncode != 0:
        return None
    return worktree_path
