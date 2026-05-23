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


def test_env_or_returns_default_for_empty(monkeypatch):
    from src.config import _env_or

    monkeypatch.setenv("FOO_TEST", "")
    assert _env_or("FOO_TEST", "default") == "default"


def test_env_or_returns_default_for_whitespace(monkeypatch):
    from src.config import _env_or

    monkeypatch.setenv("FOO_TEST", "   ")
    assert _env_or("FOO_TEST", "default") == "default"


def test_env_or_returns_default_when_unset(monkeypatch):
    from src.config import _env_or

    monkeypatch.delenv("FOO_TEST", raising=False)
    assert _env_or("FOO_TEST", "default") == "default"


def test_env_or_returns_value_when_set(monkeypatch):
    from src.config import _env_or

    monkeypatch.setenv("FOO_TEST", "override")
    assert _env_or("FOO_TEST", "default") == "override"


def test_write_json_uses_temp_file(tmp_path: Path):
    """Atomic write: tmp file must be cleaned up; only final file remains."""
    target = tmp_path / "data.json"
    write_json(target, {"a": 1})
    # Final file present
    assert target.exists()
    assert read_json(target) == {"a": 1}
    # No leftover .tmp files in the dir
    leftovers = list(tmp_path.glob(".*.tmp"))
    assert leftovers == [], f"leftover temp files: {leftovers}"


def test_write_json_overwrite_is_atomic(tmp_path: Path):
    """Re-writing an existing file must not leave partial state."""
    target = tmp_path / "data.json"
    write_json(target, {"v": 1})
    write_json(target, {"v": 2})
    assert read_json(target) == {"v": 2}


def test_write_json_failure_cleans_up_tmp(tmp_path: Path, monkeypatch):
    """If os.replace fails, no orphan .tmp should remain."""
    import os

    target = tmp_path / "data.json"

    def failing_replace(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr("src.utils.os.replace", failing_replace)
    with pytest.raises(OSError):
        write_json(target, {"v": 1})
    # No tmp leftovers
    leftovers = list(tmp_path.glob(".*.tmp"))
    assert leftovers == []


def test_setup_logging_respects_log_level_env(monkeypatch):
    """LOG_LEVEL env must change the root logger's effective level."""
    import logging

    # Reset the global init flag so setup_logging actually runs again
    import src.utils as utils_mod
    monkeypatch.setattr(utils_mod, "_LOG_CONFIGURED", False)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    # Clear any prior basicConfig effect by replacing handlers
    logging.getLogger().handlers.clear()

    utils_mod.setup_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logging_unknown_level_defaults_to_info(monkeypatch):
    import logging

    import src.utils as utils_mod
    monkeypatch.setattr(utils_mod, "_LOG_CONFIGURED", False)
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE_SOMETHING")
    logging.getLogger().handlers.clear()

    utils_mod.setup_logging()
    assert logging.getLogger().level == logging.INFO
