"""MCP server that exposes the check registry to a review agent over stdio.

Environment:
    REVIEW_WORKSPACE  git repository the checks run in; all paths must stay inside it
    REVIEW_CHANGESET  optional JSON file with the commit's ChangeSet, used when run_checks
                      is called without a commit
    ENABLED_CHECKS    comma-separated check names (defaults to all registered checks)
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from checks import ChangeSet, Check, get_enabled_checks

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
MAX_READ_LINES = 400
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

mcp = MCPServer("code-review")


def _workspace() -> Path:
    raw = os.environ.get("REVIEW_WORKSPACE")
    if not raw:
        raise RuntimeError("REVIEW_WORKSPACE is not set")
    return Path(raw).resolve()


def _changeset() -> ChangeSet | None:
    raw = os.environ.get("REVIEW_CHANGESET")
    if not raw or not Path(raw).is_file():
        return None
    return ChangeSet.from_dict(json.loads(Path(raw).read_text(encoding="utf-8")))


def _safe_relative(workspace: Path, path: str) -> str:
    resolved = (workspace / path).resolve()
    if not resolved.is_relative_to(workspace):
        raise ToolError(f"Path is outside the workspace: {path}")
    return str(resolved.relative_to(workspace))


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode != 0:
        raise ToolError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def _resolve_commit(workspace: Path, ref: str) -> str:
    """Turn a user-supplied ref into a full commit SHA; only the SHA reaches later git calls."""
    try:
        out = _git(workspace, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    except ToolError as exc:
        raise ToolError(f"Not a commit: {ref}") from exc
    return out.strip()


def _commit_changeset(workspace: Path, sha: str) -> ChangeSet:
    parents = _git(workspace, "rev-list", "--parents", "-n", "1", sha).split()[1:]
    base = parents[0] if parents else EMPTY_TREE
    names = _git(workspace, "diff", "--name-only", "--diff-filter=AM", base, sha)
    paths = [line for line in names.splitlines() if line]
    diff = _git(workspace, "diff", "-U0", "--diff-filter=AM", base, sha)
    return ChangeSet.from_diff(sha, paths, diff)


def _run_selected(
    selected: list[Check], workspace: Path, paths: list[str], change: ChangeSet | None
) -> tuple[list[dict], dict[str, str]]:
    request = ChangeSet(sha=change.sha if change else "", changed_paths=paths)
    findings: list[dict] = []
    errors: dict[str, str] = {}
    for check in selected:
        try:
            results = check.run(workspace, check.applies_to(request, workspace))
        except (RuntimeError, OSError) as exc:
            errors[check.name] = str(exc)
            continue
        for finding in results:
            item = finding.to_dict()
            if change is not None:
                item["in_diff"] = finding.line in change.changed_lines.get(finding.path, set())
            findings.append(item)
    findings.sort(key=lambda f: (f["path"], f["line"] or 0))
    return findings, errors


@mcp.tool(annotations=READ_ONLY)
def list_checks() -> list[dict]:
    """List the code checks available for review, with their category and description."""
    return [
        {"name": c.name, "category": c.category.value, "description": c.description}
        for c in get_enabled_checks()
    ]


@mcp.tool(annotations=READ_ONLY)
def get_commit_changes(ref: str = "HEAD") -> dict:
    """Describe a commit: SHA, author, message, and the files it added or modified.

    `reviewable_files` lists the changed files that at least one enabled check applies to;
    pass them to run_checks together with `commit`. Checks run on the files as they are on
    disk, so `working_tree_matches` is false when HEAD is a different commit or those files
    have uncommitted edits - line numbers may then be off.
    """
    workspace = _workspace()
    sha = _resolve_commit(workspace, ref)
    meta = _git(workspace, "show", "-s", "--format=%an <%ae>%n%aI%n%B", sha).split("\n", 2)
    change = _commit_changeset(workspace, sha)
    reviewable = sorted(
        {p for c in get_enabled_checks() for p in c.applies_to(change, workspace)}
    )

    head = _git(workspace, "rev-parse", "HEAD").strip()
    dirty = change.changed_paths and _git(
        workspace, "status", "--porcelain", "--", *change.changed_paths
    ).strip()
    return {
        "sha": sha,
        "author": meta[0],
        "date": meta[1],
        "message": meta[2].strip(),
        "changed_files": {
            path: len(change.changed_lines.get(path, ())) for path in change.changed_paths
        },
        "reviewable_files": reviewable,
        "working_tree_matches": head == sha and not dirty,
    }


@mcp.tool(annotations=READ_ONLY)
def read_source(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read lines from a file in the commit under review, prefixed with line numbers.

    Use this to see the code around a finding before explaining or fixing it.
    """
    workspace = _workspace()
    file_path = workspace / _safe_relative(workspace, path)
    if not file_path.is_file():
        raise ToolError(f"File not found: {path}")
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(start_line, 1)
    end = min(end_line or len(lines), len(lines), start + MAX_READ_LINES - 1)
    return "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(start, end + 1))


@mcp.tool(annotations=READ_ONLY)
def run_checks(
    paths: list[str], checks: list[str] | None = None, commit: str | None = None
) -> dict:
    """Run code checks on files (paths relative to the repository root).

    Each check only runs on the files it applies to. When `commit` is given, findings
    include `in_diff`: true when the finding is on a line that commit added or changed.
    """
    workspace = _workspace()
    relative = [_safe_relative(workspace, p) for p in paths]
    selected = get_enabled_checks()
    if checks:
        selected = [c for c in selected if c.name in checks]

    if commit:
        change = _commit_changeset(workspace, _resolve_commit(workspace, commit))
    else:
        change = _changeset()

    findings, errors = _run_selected(selected, workspace, relative, change)
    return {
        "checks_run": [c.name for c in selected],
        "findings": findings,
        "summary": dict(Counter(f["severity"] for f in findings)),
        "errors": errors,
    }


if __name__ == "__main__":
    mcp.run()
