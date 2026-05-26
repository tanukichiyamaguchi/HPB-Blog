from datetime import datetime
from pathlib import Path

import pytest

from src.config import JST
from src.main import (
    POST_SLOTS,
    SCHEDULED_PUBLISH_HOUR,
    SCHEDULED_PUBLISH_MINUTE,
    PostSlot,
    _already_posted_today,
    _bool_env,
    compute_next_publish_dt,
    compute_publish_dt,
)


def test_compute_next_publish_dt_is_tomorrow_at_8_15_jst():
    now = datetime(2026, 6, 15, 22, 15, 0, tzinfo=JST)
    next_dt = compute_next_publish_dt(now)
    assert next_dt.date() == datetime(2026, 6, 16).date()
    assert next_dt.hour == SCHEDULED_PUBLISH_HOUR == 8
    assert next_dt.minute == SCHEDULED_PUBLISH_MINUTE == 15
    assert next_dt.tzinfo == JST


def test_compute_next_publish_dt_clamps_past_to_next_day():
    """If a delayed CI run happens AFTER tomorrow's 08:15, clamp forward."""
    # Suppose run starts at next-day JST 09:00 (cron was delayed > 10h)
    now = datetime(2026, 6, 16, 9, 0, 0, tzinfo=JST)
    next_dt = compute_next_publish_dt(now)
    # Naive impl would return 2026-06-17 08:15 (next day) — we should ALSO get that
    assert next_dt > now
    # And specifically, it should be on or after tomorrow
    assert next_dt.date() >= datetime(2026, 6, 17).date()


def test_compute_next_publish_dt_strictly_in_future():
    """Property: returned datetime must be strictly after `now`."""
    cases = [
        datetime(2026, 6, 15, 7, 0, 0, tzinfo=JST),    # early morning before 8:15
        datetime(2026, 6, 15, 8, 14, 0, tzinfo=JST),   # 1 min before 8:15
        datetime(2026, 6, 15, 8, 15, 0, tzinfo=JST),   # exactly 8:15
        datetime(2026, 6, 15, 8, 16, 0, tzinfo=JST),   # 1 min after 8:15
        datetime(2026, 6, 15, 22, 15, 0, tzinfo=JST),  # normal cron time
    ]
    for now in cases:
        next_dt = compute_next_publish_dt(now)
        assert next_dt > now, f"compute_next_publish_dt({now}) returned {next_dt}, not > now"


def test_already_posted_today_false_when_no_sentinel(tmp_path: Path):
    assert _already_posted_today(tmp_path) is False


def test_already_posted_today_true_when_sentinel_success(tmp_path: Path):
    from src.utils import write_json
    write_json(tmp_path / "salon_board_result.json", {"success": True, "final_url": "x"})
    assert _already_posted_today(tmp_path) is True


def test_already_posted_today_false_when_sentinel_failure(tmp_path: Path):
    from src.utils import write_json
    write_json(tmp_path / "salon_board_result.json", {"success": False, "error": "boom"})
    assert _already_posted_today(tmp_path) is False


def test_already_posted_today_false_when_sentinel_corrupt(tmp_path: Path):
    (tmp_path / "salon_board_result.json").write_text("not-json", encoding="utf-8")
    # Corrupt sentinel should be treated as "no successful post" rather than crashing
    assert _already_posted_today(tmp_path) is False


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
    # Unset env now respects the supplied default (fixed semantics)
    assert _bool_env("FOO", default=True) is True


def test_bool_env_empty_string_respects_default(monkeypatch):
    """Empty-string env var should behave the same as unset (use default)."""
    monkeypatch.setenv("FOO", "")
    assert _bool_env("FOO") is False
    assert _bool_env("FOO", default=True) is True
    monkeypatch.setenv("FOO", "   ")
    assert _bool_env("FOO", default=True) is True


# ---------- Multi-slot daily posting ---------- #


def test_post_slots_definition_is_morning_noon_evening():
    """Three slots: morning 08:15, noon 12:15, evening 19:15."""
    assert len(POST_SLOTS) == 3
    labels = [s.label for s in POST_SLOTS]
    assert labels == ["morning", "noon", "evening"]
    times = [(s.hour, s.minute) for s in POST_SLOTS]
    assert times == [(8, 15), (12, 15), (19, 15)]


def test_compute_publish_dt_returns_tomorrow_when_cron_fires_at_22_15():
    """Normal cron at JST 22:15 today → tomorrow's slot."""
    now = datetime(2026, 6, 15, 22, 15, 0, tzinfo=JST)
    for slot in POST_SLOTS:
        dt = compute_publish_dt(now, slot)
        assert dt.date() == datetime(2026, 6, 16).date()
        assert (dt.hour, dt.minute) == (slot.hour, slot.minute)


def test_compute_publish_dt_clamps_past_times_to_next_day():
    """If now is after tomorrow's slot already, clamp another day forward."""
    # Suppose cron is delayed and now is 2026-06-16 13:00 JST (past tomorrow's
    # morning 08:15 AND noon 12:15, but before evening 19:15)
    now = datetime(2026, 6, 16, 13, 0, 0, tzinfo=JST)
    morning = compute_publish_dt(now, POST_SLOTS[0])
    noon = compute_publish_dt(now, POST_SLOTS[1])
    evening = compute_publish_dt(now, POST_SLOTS[2])
    # All must be strictly after now
    assert morning > now
    assert noon > now
    assert evening > now


def test_compute_publish_dt_strictly_in_future_for_all_slots():
    cases = [
        datetime(2026, 6, 15, 7, 0, 0, tzinfo=JST),
        datetime(2026, 6, 15, 12, 14, 0, tzinfo=JST),
        datetime(2026, 6, 15, 22, 15, 0, tzinfo=JST),
    ]
    for now in cases:
        for slot in POST_SLOTS:
            dt = compute_publish_dt(now, slot)
            assert dt > now, f"slot={slot.label} now={now} returned {dt}"


def test_compute_next_publish_dt_is_morning_slot_for_backwards_compat():
    """Legacy single-slot helper returns the same as morning slot's publish_dt."""
    now = datetime(2026, 6, 15, 22, 15, 0, tzinfo=JST)
    assert compute_next_publish_dt(now) == compute_publish_dt(now, POST_SLOTS[0])


def test_post_slot_is_frozen_dataclass():
    slot = PostSlot("test", 10, 30)
    assert slot.label == "test"
    assert slot.hour == 10
    assert slot.minute == 30
    # Frozen → no mutation
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        slot.hour = 11  # type: ignore[misc]
