"""Launch the code-review MCP server over stdio and call its tools against sample_app/.

Usage: python -m mcp_servers.test_client
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FILES = ["sample_app/inventory.py", "sample_app/report.py"]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_servers.code_review_server"],
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "REVIEW_WORKSPACE": str(REPO_ROOT),
        },
    )
    async with Client(params) as client:
        tools = await client.list_tools()
        print("tools:", [t.name for t in tools.tools])

        checks = await client.call_tool("list_checks", {})
        print("list_checks:", json.dumps(checks.structured_content, indent=2))

        result = await client.call_tool("run_checks", {"paths": SAMPLE_FILES})
        data = json.loads(result.content[0].text)
        print("summary:", data["summary"], "errors:", data["errors"])
        for f in data["findings"]:
            print(f"  {f['severity']:<8} {f['path']}:{f['line']} {f['code']}")

        escape = await client.call_tool("run_checks", {"paths": ["../../etc/passwd"]})
        print("path escape rejected:", escape.is_error, "-", escape.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
