from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Optional so purely local steps (transform-silver, dbt, the dashboard)
    # need no Square credential at all. Commands that actually contact
    # Square call require_token() to enforce it with a clear error.
    square_access_token: SecretStr | None = Field(default=None, alias="SQUARE_ACCESS_TOKEN")
    square_environment: Literal["sandbox", "production"] = Field(
        default="sandbox", alias="SQUARE_ENVIRONMENT"
    )
    square_api_version: str = Field(default="2026-07-15", alias="SQUARE_API_VERSION")
    raw_data_dir: Path = Field(default=Path("data/bronze"), alias="RAW_DATA_DIR")

    @property
    def has_token(self) -> bool:
        return self.square_access_token is not None and bool(
            self.square_access_token.get_secret_value()
        )

    def require_token(self) -> SecretStr:
        """Return the token, or raise a clear error if it isn't configured."""
        if not self.has_token:
            raise RuntimeError(
                "SQUARE_ACCESS_TOKEN is not set. Copy .env.example to .env and add your "
                "Square Sandbox token before running commands that contact Square."
            )
        # has_token guarantees this is not None.
        assert self.square_access_token is not None
        return self.square_access_token

    @property
    def square_base_url(self) -> str:
        if self.square_environment == "production":
            return "https://connect.squareup.com"
        return "https://connect.squareupsandbox.com"

    @property
    def reporting_base_url(self) -> str:
        if self.square_environment == "production":
            return "https://connect.squareup.com/reporting"
        # Reporting API availability in Sandbox may differ. Keep this explicit.
        return "https://connect.squareupsandbox.com/reporting"

    @property
    def is_production(self) -> bool:
        return self.square_environment == "production"
