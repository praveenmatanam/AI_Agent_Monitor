"""Send a signed GitHub push webhook for a local commit range to the monitor.

Usage:
    python scripts/simulate_push.py                 # HEAD~1..HEAD of this repo
    python scripts/simulate_push.py BEFORE AFTER --url http://localhost:8000/webhook/github

The signing secret is read from GITHUB_WEBHOOK_SECRET (environment or .env).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.config import get_settings  # pylint: disable=wrong-import-position


def rev_parse(ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("before", nargs="?", default="HEAD~1")
    parser.add_argument("after", nargs="?", default="HEAD")
    parser.add_argument("--url", default="http://localhost:8000/webhook/github")
    args = parser.parse_args()

    settings = get_settings()
    payload = {
        "ref": f"refs/heads/{settings.watched_branch}",
        "before": rev_parse(args.before),
        "after": rev_parse(args.after),
        "repository": {"full_name": settings.github_repo},
    }
    body = json.dumps(payload).encode()
    secret = settings.github_webhook_secret.get_secret_value().encode()
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

    response = requests.post(
        args.url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": signature,
        },
        timeout=30,
    )
    print(response.status_code, response.text)


if __name__ == "__main__":
    main()
