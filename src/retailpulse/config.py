from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    square_access_token: SecretStr = Field(alias="SQUARE_ACCESS_TOKEN")
    square_environment: Literal["sandbox", "production"] = Field(
        default="sandbox", alias="SQUARE_ENVIRONMENT"
    )
    square_api_version: str = Field(default="2026-07-15", alias="SQUARE_API_VERSION")
    raw_data_dir: Path = Field(default=Path("data/bronze"), alias="RAW_DATA_DIR")

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
