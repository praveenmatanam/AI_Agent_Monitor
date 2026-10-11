# AI_Agent_Monitor
Monitors the git commits and run pylint on python files

When a commit is pushed, a GitHub webhook calls this service. It checks out the commit, finds
the changed files, and runs an [OpenCode](https://opencode.ai) agent that lints them through a
local MCP server and writes a triaged review. The review is saved under `reports/` and posted as
a comment on the commit.

```
GitHub push -> webhook_server.py -> pipeline.py -> opencode run --agent commit-reviewer
                                                        |
                                         code-review MCP server -> checks registry -> pylint
```

## Current mode: review from Cursor

For now the OpenCode agent and webhook are not used. The `code-review` MCP server is registered
with Cursor in `.cursor/mcp.json`, and Cursor's agent acts as the reviewer, following
`.cursor/rules/commit-review.mdc`.

1. Create the venv and install requirements (see Setup).
2. Open this folder in Cursor, then enable `code-review` under **Cursor Settings -> MCP**.
3. In chat, ask for example "review the latest commit" or "review commit abc1234".

The server reviews the repository open in Cursor (`REVIEW_WORKSPACE=${workspaceFolder}`) and
exposes four read-only tools:

| Tool | Purpose |
| --- | --- |
| `get_commit_changes(ref)` | Commit metadata, changed files, and which files the checks apply to |
| `run_checks(paths, checks, commit)` | Run checks; with `commit`, findings on lines it changed get `in_diff: true` |
| `read_source(path, start_line, end_line)` | Read code around a finding |
| `list_checks()` | Enabled checks |

Checks run on the files as they are on disk. `get_commit_changes` returns
`working_tree_matches: false` when HEAD is a different commit or those files have uncommitted
edits, because line numbers may then not match the commit.

The rest of this README describes the automated webhook mode, which needs OpenCode
(`brew install opencode`) logged in to an LLM provider (GitHub Copilot by default).

## How it works (webhook mode)

1. `agent/webhook_server.py` verifies the `X-Hub-Signature-256` HMAC, accepts pushes to
   `WATCHED_BRANCH`, returns `202`, and queues the review in the background.
2. `agent/pipeline.py` clones or fetches the repo into `workspace/`, checks out the commit, and
   diffs it against the previous head to get the changed files and lines.
3. `agent/opencode_agent.py` runs `opencode run --agent commit-reviewer` from this directory.
   The agent's built-in tools (shell, file edit, file read, web) are all denied; it can only call
   the `code-review` MCP tools.
4. `mcp_servers/code_review_server.py` exposes `list_checks`, `run_checks`, and `read_source`.
   Every path is confined to the checked-out workspace. Findings on lines the commit changed are
   flagged `in_diff`.
5. `agent/reporter.py` writes `reports/<sha>.md` and posts the review as a commit comment.

OpenCode runs from this directory, not from the checkout. That way the configuration in
`opencode.json` and `.opencode/agents/` always comes from this trusted install, and a pushed commit
cannot change the agent's permissions or tools.

## Requirements

- Python 3.12+
- git
- OpenCode CLI, tested with **1.18.34** (`curl -fsSL https://opencode.ai/install | bash` or
  `brew install opencode`)
- A GitHub Copilot subscription, connected with `opencode auth login` -> GitHub Copilot
  (any other OpenCode provider works too; set `OPENCODE_MODEL` to its `provider/model` id)
- A GitHub personal access token that can read the repo and create commit comments

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then fill in the real values
```

| Variable | Purpose |
| --- | --- |
| `GITHUB_TOKEN` | Clones the repo and posts commit comments. Never passed to the agent. |
| `GITHUB_WEBHOOK_SECRET` | Shared secret for webhook signatures. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `GITHUB_API_BASE` | REST API root, `https://api.github.com` (GitHub Enterprise: `https://<host>/api/v3`). |
| `GITHUB_REPO` / `GITHUB_CLONE_URL` | Repo to review. |
| `WATCHED_BRANCH` | Only pushes to this branch are reviewed. |
| `OPENCODE_MODEL` | Full OpenCode model id, for example `github-copilot/gpt-5-mini`. List them with `opencode models github-copilot`. |
| `ENABLED_CHECKS` | Comma-separated check names, for example `pylint`. |

`.env` is gitignored. Don't commit real values. If your network uses a corporate CA, set `REQUESTS_CA_BUNDLE` (and `GIT_SSL_CAINFO` for git) to the CA bundle; don't disable TLS
verification.

## Run

```bash
uvicorn agent.webhook_server:app --host 0.0.0.0 --port 8000
curl localhost:8000/health
```

### Register the webhook

In the repo on GitHub: **Settings -> Webhooks -> Add webhook**

- Payload URL: `https://<host reachable from GitHub>/webhook/github`. From a laptop, expose the
  local server with a tunnel: `cloudflared tunnel --url http://localhost:8000` prints a
  `https://<random>.trycloudflare.com` URL (it changes on every restart).
- Content type: `application/json`
- Secret: the value of `GITHUB_WEBHOOK_SECRET`
- Events: **Just the push event**

GitHub sends a `ping` first. The service answers it, and it should show a green tick in the
webhook's Recent Deliveries tab.

### Test without GitHub

With the server running, send a signed push for the last local commit:

```bash
python scripts/simulate_push.py                    # HEAD~1..HEAD
python scripts/simulate_push.py <before> <after>
```

The commits must exist in the remote at `GITHUB_CLONE_URL`. To try the agent on the bundled
`sample_app/` (code with deliberate pylint problems), push a commit that changes it.

### Test pieces individually

```bash
python -m mcp_servers.test_client      # MCP server and pylint check, no LLM needed

REVIEW_PYTHON=$PWD/.venv/bin/python REVIEW_TOOL_ROOT=$PWD REVIEW_WORKSPACE=$PWD \
  opencode mcp list                    # OpenCode can start the MCP server

opencode debug agent commit-reviewer   # resolved agent permissions
```

## Add a check

Each check is one module in `checks/`, and the registry picks it up automatically. The agent
discovers it through `list_checks`, so its prompt doesn't change.

```python
# checks/bandit_check.py
from checks import register
from checks.base import Category, ChangeSet, Finding, Severity

@register
class BanditCheck:
    name = "bandit"
    category = Category.SECURITY
    description = "Python security linter (bandit)."

    def applies_to(self, change: ChangeSet, workspace) -> list[str]:
        return [p for p in change.changed_paths if p.endswith(".py")]

    def run(self, workspace, paths) -> list[Finding]:
        ...  # run the tool, map its severities onto Severity, return Findings
```

Then add it to `ENABLED_CHECKS` (`pylint,bandit`) and to `requirements.txt`.

## Layout

```
agent/        webhook server, pipeline, OpenCode runner, reporter, settings
checks/       check registry, shared types, pylint check
mcp_servers/  code-review MCP server and a standalone test client
.opencode/    commit-reviewer agent definition
opencode.json MCP server configuration
sample_app/   intentionally lint-dirty demo code
scripts/      simulate_push.py
```
