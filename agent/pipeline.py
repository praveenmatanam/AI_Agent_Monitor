"""Turns a pushed commit into a review: checkout, diff, agent run, report."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import threading
from pathlib import Path

import requests

from agent import reporter
from agent.config import Settings, get_settings
from agent.opencode_agent import AgentError, build_prompt, run_review
from checks import ChangeSet, get_enabled_checks

log = logging.getLogger(__name__)

ZERO_SHA = "0" * 40
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# All commits share one workspace checkout, so reviews run one at a time.
_workspace_lock = threading.Lock()


def _git(settings: Settings, *args: str, cwd: Path | None = None) -> str:
    env = dict(os.environ)
    if settings.github_token is not None:
        # Pass the token as a header via env so it never lands in .git/config or the remote URL.
        creds = base64.b64encode(
            f"x-access-token:{settings.github_token.get_secret_value()}".encode()
        ).decode()
        env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.extraHeader",
            GIT_CONFIG_VALUE_0=f"Authorization: Basic {creds}",
        )
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", *args],
        cwd=cwd or settings.workspace_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def prepare_workspace(settings: Settings, sha: str) -> Path:
    workspace = settings.workspace_dir
    if not (workspace / ".git").is_dir():
        workspace.parent.mkdir(parents=True, exist_ok=True)
        _git(settings, "clone", "--quiet", settings.github_clone_url, str(workspace),
             cwd=workspace.parent)
    _git(settings, "fetch", "--quiet", "origin")
    _git(settings, "checkout", "--quiet", "--force", "--detach", sha)
    _git(settings, "clean", "-fdxq")
    return workspace


def _commit_exists(settings: Settings, sha: str) -> bool:
    try:
        _git(settings, "cat-file", "-e", f"{sha}^{{commit}}")
    except RuntimeError:
        return False
    return True


def build_changeset(settings: Settings, before: str, sha: str) -> ChangeSet:
    """Collect files and line numbers added or modified between before and sha."""
    if before == ZERO_SHA or not _commit_exists(settings, before):
        # New branch or force push: review just this commit against its parent.
        before = f"{sha}^" if _commit_exists(settings, f"{sha}^") else EMPTY_TREE

    names = _git(settings, "diff", "--name-only", "--diff-filter=AM", before, sha)
    paths = [line for line in names.splitlines() if line]
    diff = _git(settings, "diff", "-U0", "--diff-filter=AM", before, sha)
    return ChangeSet.from_diff(sha, paths, diff)


def run_for_commit(before: str, sha: str, settings: Settings | None = None) -> Path:
    """Review one pushed commit and return the path of the written report."""
    settings = settings or get_settings()
    with _workspace_lock:
        log.info("Reviewing commit %s (before %s)", sha, before)
        workspace = prepare_workspace(settings, sha)
        change = build_changeset(settings, before, sha)
        message = _git(settings, "log", "-1", "--format=%B", sha)

        checks = get_enabled_checks(settings.enabled_check_names)
        files = sorted({p for c in checks for p in c.applies_to(change, workspace)})
        if not files:
            content = reporter.format_report(
                sha, [], "No files in this commit match an enabled check. Nothing to review."
            )
            return reporter.write_report(settings, sha, content)

        changeset_file = settings.reports_dir / ".changesets" / f"{sha}.json"
        changeset_file.parent.mkdir(parents=True, exist_ok=True)
        changeset_file.write_text(json.dumps(change.to_dict()), encoding="utf-8")

        try:
            review = run_review(settings, workspace, changeset_file,
                                build_prompt(sha, message, files))
        except AgentError as exc:
            log.error("Agent failed for %s: %s", sha, exc)
            content = reporter.format_report(sha, files, f"**Review failed:** {exc}")
            return reporter.write_report(settings, sha, content)
        finally:
            changeset_file.unlink(missing_ok=True)

        content = reporter.format_report(sha, files, review)
        path = reporter.write_report(settings, sha, content)
        try:
            reporter.post_commit_comment(settings, sha, content)
        except requests.RequestException as exc:
            log.error("Could not post commit comment for %s: %s", sha, exc)
        return path
