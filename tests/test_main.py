from datetime import datetime

import pytest

from src.config import JST
from src.main import (
    SCHEDULED_PUBLISH_HOUR,
    SCHEDULED_PUBLISH_MINUTE,
    _bool_env,
    compute_next_publish_dt,
)


def test_compute_next_publish_dt_is_tomorrow_at_8_15_jst():
    now = datetime(2026, 6, 15, 22, 15, 0, tzinfo=JST)
    next_dt = compute_next_publish_dt(now)
    assert next_dt.date() == datetime(2026, 6, 16).date()
    assert next_dt.hour == SCHEDULED_PUBLISH_HOUR == 8
    assert next_dt.minute == SCHEDULED_PUBLISH_MINUTE == 15
    assert next_dt.tzinfo == JST


def test_compute_next_publish_dt_handles_month_boundary():
    now = datetime(2026, 6, 30, 22, 15, 0, tzinfo=JST)
    next_dt = compute_next_publish_dt(now)
    assert next_dt.date() == datetime(2026, 7, 1).date()


def test_compute_next_publish_dt_handles_year_boundary():
    now = datetime(2026, 12, 31, 22, 15, 0, tzinfo=JST)
    next_dt = compute_next_publish_dt(now)
    assert next_dt.date() == datetime(2027, 1, 1).date()


@pytest.mark.parametrize("val,expected", [
    ("true", True),
    ("True", True),
    ("TRUE", True),
    ("1", True),
    ("yes", True),
    ("on", True),
    ("false", False),
    ("0", False),
    ("no", False),
    ("off", False),
    ("", False),
])
def test_bool_env(monkeypatch, val, expected):
    monkeypatch.setenv("FOO", val)
    assert _bool_env("FOO") is expected


def test_bool_env_unset_default(monkeypatch):
    monkeypatch.delenv("FOO", raising=False)
    assert _bool_env("FOO") is False
    assert _bool_env("FOO", default=True) is False  # empty maps to False explicitly
