"""Personal settings from the environment: API keys.

Unlike the charter, these belong to whoever runs aiarb, not to the repo, so they
are never read from files in it. In particular, the target repo's `.env` is not
loaded: it may hold that app's own keys.
"""

from typing import Any

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    anthropic_api_key: SecretStr | None = None
    """From ANTHROPIC_API_KEY. Optional: Claude Code may be logged in without one."""
    typesafe_api_key: SecretStr | None = None
    """From TYPESAFE_API_KEY. Optional: enables Jev for scoring."""

    @field_validator("anthropic_api_key", "typesafe_api_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        # `KEY=` left unfilled from .env.example means no key, not an empty one.
        if isinstance(value, str):
            return value.strip() or None
        return value
