"""Pylint check: static analysis for Python files."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from checks import register
from checks.base import Category, ChangeSet, Finding, Severity

SEVERITY_MAP = {
    "fatal": Severity.CRITICAL,
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "refactor": Severity.LOW,
    "convention": Severity.INFO,
}


@register
class PylintCheck:
    """Runs pylint and converts its JSON output into findings."""

    name = "pylint"
    category = Category.QUALITY
    description = "Python static analysis: bugs, errors, code smells, and style (pylint)."

    def applies_to(self, change: ChangeSet, workspace: Path) -> list[str]:
        return [
            path for path in change.changed_paths
            if path.endswith(".py") and (workspace / path).is_file()
        ]

    def run(self, workspace: Path, paths: list[str]) -> list[Finding]:
        if not paths:
            return []
        cmd = [sys.executable, "-m", "pylint", "--output-format=json"]
        if (workspace / ".pylintrc").is_file():
            cmd.append("--rcfile=.pylintrc")
        cmd.extend(paths)

        result = subprocess.run(
            cmd, cwd=workspace, capture_output=True, text=True, timeout=120, check=False
        )
        # Pylint's exit code is a bit mask of message types; 32 means a usage error.
        if result.returncode & 32 or not result.stdout.strip():
            raise RuntimeError(f"pylint failed: {result.stderr.strip() or result.stdout.strip()}")

        return [
            Finding(
                tool=self.name,
                category=self.category,
                severity=SEVERITY_MAP.get(item["type"], Severity.INFO),
                path=item["path"],
                line=item.get("line"),
                code=f"{item['message-id']} {item['symbol']}",
                message=item["message"],
            )
            for item in json.loads(result.stdout)
        ]
