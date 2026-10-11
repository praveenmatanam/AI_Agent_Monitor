"""Shared types for all checks: findings, severities, and the Check protocol."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Protocol

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class Severity(IntEnum):
    """Severity scale shared by every check."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class Category(StrEnum):
    """What kind of concern a check addresses."""

    QUALITY = "quality"
    SECURITY = "security"


@dataclass(frozen=True)
class Finding:
    """One issue reported by a check, in a tool-independent shape."""

    tool: str
    category: Category
    severity: Severity
    path: str
    line: int | None
    code: str
    message: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["category"] = self.category.value
        data["severity"] = self.severity.name
        return data


@dataclass(frozen=True)
class ChangeSet:
    """The files and lines touched by a commit."""

    sha: str
    changed_paths: list[str]
    changed_lines: dict[str, set[int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sha": self.sha,
            "changed_paths": self.changed_paths,
            "changed_lines": {p: sorted(lines) for p, lines in self.changed_lines.items()},
        }

    @classmethod
    def from_diff(cls, sha: str, changed_paths: list[str], unified_diff: str) -> ChangeSet:
        """Build a ChangeSet from `git diff -U0` output, recording added or modified lines."""
        changed_lines: dict[str, set[int]] = {}
        current: str | None = None
        for line in unified_diff.splitlines():
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else None
            elif current and (match := HUNK_RE.match(line)):
                start, count = int(match.group(1)), int(match.group(2) or 1)
                changed_lines.setdefault(current, set()).update(range(start, start + count))
        return cls(sha=sha, changed_paths=changed_paths, changed_lines=changed_lines)

    @classmethod
    def from_dict(cls, data: dict) -> ChangeSet:
        return cls(
            sha=data["sha"],
            changed_paths=list(data["changed_paths"]),
            changed_lines={p: set(lines) for p, lines in data.get("changed_lines", {}).items()},
        )


class Check(Protocol):
    """Interface every registered check implements."""

    name: str
    category: Category
    description: str

    def applies_to(self, change: ChangeSet, workspace: Path) -> list[str]:
        """Return the subset of changed paths this check should run on."""

    def run(self, workspace: Path, paths: list[str]) -> list[Finding]:
        """Run the check on paths (relative to workspace) and return findings."""
