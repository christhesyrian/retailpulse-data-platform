import pytest
from pydantic import ValidationError

from retailpulse.config import Settings


def test_missing_token_raises_validation_error(monkeypatch, tmp_path):
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env file here
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_token_never_appears_in_repr_or_str(monkeypatch):
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "super-secret-value-123")
    settings = Settings(_env_file=None)
    assert "super-secret-value-123" not in repr(settings)
    assert "super-secret-value-123" not in str(settings)


def test_sandbox_is_default_environment(monkeypatch):
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "fake-token")
    monkeypatch.delenv("SQUARE_ENVIRONMENT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.square_environment == "sandbox"
    assert settings.is_production is False
    assert "squareupsandbox.com" in settings.square_base_url


def test_production_environment_maps_to_production_url(monkeypatch):
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "fake-token")
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
    settings = Settings(_env_file=None)
    assert settings.is_production is True
    assert settings.square_base_url == "https://connect.squareup.com"
