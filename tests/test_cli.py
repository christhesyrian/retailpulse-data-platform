import pytest

from retailpulse.cli import MAX_EXTRACT_DAYS, validate_days


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
