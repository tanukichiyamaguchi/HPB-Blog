from datetime import date
from pathlib import Path

import pytest

from src.salon_board_poster import (
    DEFAULT_CATEGORY,
    POSTER_ROTATION,
    PostResult,
    SalonBoardPoster,
    _safe_filename,
    get_poster_for_date,
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


def test_poster_rotation_constants():
    """User spec: rotate momo / aoi / ケイト 蒲田西口店, exclude pome(非掲載)."""
    assert POSTER_ROTATION == ("momo", "aoi", "ケイト 蒲田西口店")


def test_default_category_is_recommended_menu():
    """User said 'カテゴリは最適な内容を選択' — for menu-focused content, おすすめメニュー."""
    assert DEFAULT_CATEGORY == "おすすめメニュー"


def test_get_poster_for_date_returns_known_poster():
    """Output must always come from POSTER_ROTATION."""
    for d in (date(2026, 5, 23), date(2026, 5, 24), date(2026, 5, 25), date(2026, 12, 31)):
        assert get_poster_for_date(d) in POSTER_ROTATION


def test_get_poster_for_date_rotates_daily():
    """Three consecutive days must yield three distinct posters (full cycle)."""
    d1 = date(2026, 5, 23)
    d2 = date(2026, 5, 24)
    d3 = date(2026, 5, 25)
    p1 = get_poster_for_date(d1)
    p2 = get_poster_for_date(d2)
    p3 = get_poster_for_date(d3)
    assert {p1, p2, p3} == set(POSTER_ROTATION)


def test_get_poster_for_date_repeats_every_three_days():
    """Day N and Day N+3 must be the same poster."""
    d = date(2026, 5, 23)
    same = date(2026, 5, 26)
    assert get_poster_for_date(d) == get_poster_for_date(same)


def test_get_poster_for_date_is_deterministic():
    """Calling twice with the same date returns the same poster."""
    d = date(2026, 5, 23)
    assert get_poster_for_date(d) == get_poster_for_date(d)
