import json
from datetime import datetime
from pathlib import Path

import pytest

from src.utils import (
    get_season,
    get_today_date_str,
    read_json,
    read_text,
    retry,
    write_json,
    write_text,
)


def test_get_season_spring():
    assert get_season(3) == "春"
    assert get_season(4) == "春"
    assert get_season(5) == "春"


def test_get_season_summer():
    assert get_season(6) == "夏"
    assert get_season(8) == "夏"


def test_get_season_autumn():
    assert get_season(9) == "秋"
    assert get_season(11) == "秋"


def test_get_season_winter():
    assert get_season(12) == "冬"
    assert get_season(1) == "冬"
    assert get_season(2) == "冬"


def test_get_season_invalid():
    with pytest.raises(ValueError):
        get_season(0)
    with pytest.raises(ValueError):
        get_season(13)


def test_get_today_date_str_formats_jst():
    dt = datetime(2026, 1, 15, 23, 0, 0)
    s = get_today_date_str(dt)
    assert s == "2026-01-15"


def test_write_read_text_roundtrip(tmp_path: Path):
    p = tmp_path / "sub" / "a.txt"
    write_text(p, "こんにちは")
    assert p.exists()
    assert read_text(p) == "こんにちは"


def test_write_read_json_roundtrip(tmp_path: Path):
    p = tmp_path / "data.json"
    payload = {"theme": "梅雨に映える眉", "count": 1}
    write_json(p, payload)
    loaded = read_json(p)
    assert loaded == payload
    # Ensure UTF-8 is preserved (not escaped)
    assert "梅雨" in p.read_text(encoding="utf-8")


def test_retry_returns_on_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert retry(fn, max_attempts=3, base_delay=0) == "ok"
    assert calls["n"] == 1


def test_retry_recovers_after_failure():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    assert retry(fn, max_attempts=3, base_delay=0) == "ok"
    assert calls["n"] == 2


def test_retry_raises_after_max_attempts():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RuntimeError("always fails")

    with pytest.raises(RuntimeError, match="always fails"):
        retry(fn, max_attempts=3, base_delay=0)
    assert calls["n"] == 3
