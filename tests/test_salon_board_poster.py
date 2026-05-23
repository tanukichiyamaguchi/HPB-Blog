from pathlib import Path

import pytest

from src.salon_board_poster import (
    PostResult,
    SalonBoardPoster,
    _safe_filename,
)


def test_safe_filename_strips_unsafe_chars():
    assert _safe_filename("login page") == "login_page"
    assert _safe_filename("step/1") == "step_1"
    assert _safe_filename("ok-name_2") == "ok-name_2"
    assert _safe_filename("title:value") == "title_value"


def test_safe_filename_empty_returns_default():
    assert _safe_filename("") == "step"
    assert _safe_filename("///") == "___"  # not empty, slashes become underscores


def test_post_result_to_dict_serializes_paths():
    p = PostResult(
        success=True,
        final_url="https://example/x",
        screenshots=[Path("/tmp/a.png"), Path("/tmp/b.png")],
    )
    d = p.to_dict()
    assert d["success"] is True
    assert d["final_url"] == "https://example/x"
    assert d["error"] is None
    assert d["screenshots"] == ["/tmp/a.png", "/tmp/b.png"]


def test_post_result_failure_to_dict():
    p = PostResult(success=False, error="boom")
    d = p.to_dict()
    assert d["success"] is False
    assert d["error"] == "boom"
    assert d["screenshots"] == []


def test_poster_requires_credentials():
    with pytest.raises(ValueError):
        SalonBoardPoster("", "pw")
    with pytest.raises(ValueError):
        SalonBoardPoster("id", "")


def test_poster_stores_config(tmp_path: Path):
    p = SalonBoardPoster(
        "uid",
        "pw",
        headless=False,
        screenshots_dir=tmp_path / "shots",
        timeout_ms=12345,
        per_selector_timeout_ms=600,
    )
    assert p.user_id == "uid"
    assert p.password == "pw"
    assert p.headless is False
    assert p.screenshots_dir == tmp_path / "shots"
    assert p.timeout_ms == 12345
    assert p.per_selector_timeout_ms == 600
    assert p._step == 0
    assert p._screenshots == []


def test_post_as_draft_rejects_empty_inputs(tmp_path: Path):
    image = tmp_path / "img.png"
    image.write_bytes(b"\x89PNG")
    p = SalonBoardPoster("uid", "pw")
    with pytest.raises(ValueError):
        p.post_as_draft("", "body", image)
    with pytest.raises(ValueError):
        p.post_as_draft("title", "", image)


def test_post_as_draft_rejects_missing_image(tmp_path: Path):
    p = SalonBoardPoster("uid", "pw")
    with pytest.raises(FileNotFoundError):
        p.post_as_draft("title", "body", tmp_path / "missing.png")


def test_post_as_scheduled_rejects_empty_inputs(tmp_path: Path):
    from datetime import datetime

    image = tmp_path / "img.png"
    image.write_bytes(b"\x89PNG")
    p = SalonBoardPoster("uid", "pw")
    dt = datetime(2026, 6, 16, 8, 15)
    with pytest.raises(ValueError):
        p.post_as_scheduled("", "body", image, dt)
    with pytest.raises(ValueError):
        p.post_as_scheduled("title", "", image, dt)


def test_post_as_scheduled_rejects_missing_image(tmp_path: Path):
    from datetime import datetime

    p = SalonBoardPoster("uid", "pw")
    with pytest.raises(FileNotFoundError):
        p.post_as_scheduled(
            "title", "body", tmp_path / "missing.png",
            datetime(2026, 6, 16, 8, 15),
        )
