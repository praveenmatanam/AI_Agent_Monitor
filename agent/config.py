"""Settings loaded from the environment and the gitignored .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

TOOL_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. Secrets are only ever read from the environment or .env."""

    model_config = SettingsConfigDict(env_file=TOOL_ROOT / ".env", extra="ignore")

    github_token: SecretStr | None = None
    github_webhook_secret: SecretStr
    github_api_base: str = "https://api.github.com"
    github_repo: str = "praveenmatanam/AI_Agent_Monitor"
    github_clone_url: str = "https://github.com/praveenmatanam/AI_Agent_Monitor.git"
    watched_branch: str = "main"

    # Full OpenCode model id, provider/model; list them with `opencode models`.
    opencode_model: str = "github-copilot/gpt-5-mini"
    opencode_timeout_seconds: int = 600

    enabled_checks: str = "pylint"

    workspace_dir: Path = TOOL_ROOT / "workspace"
    reports_dir: Path = TOOL_ROOT / "reports"

    @property
    def enabled_check_names(self) -> list[str]:
        return [name.strip() for name in self.enabled_checks.split(",") if name.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
