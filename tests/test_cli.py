import pytest

from retailpulse.cli import (
    MAX_EXTRACT_DAYS,
    PRODUCTION_OPT_IN_ENV,
    require_production_opt_in,
    validate_days,
)


def test_validate_days_rejects_zero():
    with pytest.raises(SystemExit):
        validate_days(0)


def test_validate_days_rejects_negative():
    with pytest.raises(SystemExit):
        validate_days(-3)


def test_validate_days_rejects_excessive_window():
    with pytest.raises(SystemExit):
        validate_days(MAX_EXTRACT_DAYS + 1)


def test_validate_days_accepts_reasonable_window():
    validate_days(1)
    validate_days(7)
    validate_days(MAX_EXTRACT_DAYS)


def test_production_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv(PRODUCTION_OPT_IN_ENV, raising=False)
    with pytest.raises(SystemExit) as exc_info:
        require_production_opt_in()
    assert exc_info.value.code == 3


def test_production_opt_in_allows_when_set(monkeypatch):
    monkeypatch.setenv(PRODUCTION_OPT_IN_ENV, "1")
    require_production_opt_in()  # must not raise


def test_production_opt_in_rejects_garbage_value(monkeypatch):
    monkeypatch.setenv(PRODUCTION_OPT_IN_ENV, "maybe")
    with pytest.raises(SystemExit):
        require_production_opt_in()
