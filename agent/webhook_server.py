"""FastAPI service that receives GitHub push webhooks and queues commit reviews.

Run: uvicorn agent.webhook_server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response

from agent.config import get_settings
from agent.pipeline import ZERO_SHA, run_for_commit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="AI Agent Monitor")


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Check GitHub's X-Hub-Signature-256 header (HMAC-SHA256 of the raw body)."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _review_safely(before: str, after: str) -> None:
    try:
        run_for_commit(before, after)
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception("Review failed for %s", after)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook/github", status_code=202)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
):
    settings = get_settings()
    body = await request.body()
    if not verify_signature(
        settings.github_webhook_secret.get_secret_value(), body, x_hub_signature_256
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if x_github_event == "ping":
        return {"status": "pong"}
    if x_github_event != "push":
        return Response(status_code=204)

    payload = json.loads(body)
    if payload.get("ref") != f"refs/heads/{settings.watched_branch}":
        return Response(status_code=204)
    before, after = payload.get("before", ZERO_SHA), payload.get("after", ZERO_SHA)
    if after == ZERO_SHA:
        return Response(status_code=204)

    log.info("Queued review for %s", after)
    background_tasks.add_task(_review_safely, before, after)
    return {"status": "queued", "commit": after}
