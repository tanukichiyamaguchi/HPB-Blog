"""Unit tests for src/salon_board_relay.

Only tests the pure / parser helpers; network-touching code paths are exercised
in CI by the ``test-relay-login.yml`` and ``test-relay-submit.yml`` workflows.
"""
from datetime import date, datetime

import pytest

from src.salon_board_relay import (
    BLOG_CATEGORY_CODES,
    DEFAULT_CATEGORY_LABEL,
    POSTER_ROTATION,
    STAFF_IDS,
    PostResult,
    _format_body_html,
    _hidden_field,
    _parse_form_tokens,
    _parse_login_state,
    _select_options,
    get_poster_for_date,
    post_blog_scheduled,
)


# ---- form token parser ----


def test_parse_form_tokens_name_then_value():
    body = (
        '<input type="hidden" name="org.apache.struts.taglib.html.TOKEN" value="abc123"/>'
        '<input type="hidden" name="storeIdForMultipleTabCheck" value="xyz789"/>'
    )
    csrf, tab = _parse_form_tokens(body)
    assert csrf == "abc123"
    assert tab == "xyz789"


def test_parse_form_tokens_value_then_name():
    body = (
        "<input value='abc123' name='org.apache.struts.taglib.html.TOKEN'/>"
        "<input value='xyz789' name='storeIdForMultipleTabCheck'/>"
    )
    csrf, tab = _parse_form_tokens(body)
    assert csrf == "abc123"
    assert tab == "xyz789"


def test_parse_form_tokens_raises_on_missing():
    with pytest.raises(RuntimeError, match="Failed to parse form tokens"):
        _parse_form_tokens("<html>no tokens here</html>")


def test_hidden_field_returns_none_for_missing():
    assert _hidden_field("<html></html>", "anything") is None


# ---- <select> option parser ----


def test_select_options_extracts_visible_values_only():
    body = (
        '<select name="rsvTokoDate">'
        '<option value="">--</option>'
        '<option value="20260524">2026年05月24日</option>'
        '<option value="20260606">2026年06月06日</option>'
        "</select>"
    )
    assert _select_options(body, "rsvTokoDate") == ["20260524", "20260606"]


def test_select_options_returns_empty_when_select_missing():
    assert _select_options("<form></form>", "rsvTokoDate") == []


# ---- login-state parser ----


def test_parse_login_state_extracts_storeid_userid():
    body = (
        "<script>sc_data = {'userid':'CE12345', 'storeid':'H000797013'};</script>"
    )
    storeid, userid = _parse_login_state(body)
    assert storeid == "H000797013"
    assert userid == "CE12345"


def test_parse_login_state_empty_when_absent():
    assert _parse_login_state("<html>no js</html>") == ("", "")


# ---- body HTML formatter ----


def test_format_body_html_wraps_each_paragraph():
    out = _format_body_html("para1\n\npara2")
    assert out == "<p>para1</p>\n<p>para2</p>"


def test_format_body_html_converts_inline_newlines_to_br():
    out = _format_body_html("line1\nline2")
    assert out == "<p>line1<br />line2</p>"


def test_format_body_html_returns_empty_for_blank_input():
    assert _format_body_html("") == ""
    assert _format_body_html("   \n  \n") == ""


def test_format_body_html_handles_crlf():
    out = _format_body_html("a\r\n\r\nb")
    assert out == "<p>a</p>\n<p>b</p>"


# ---- poster rotation ----


def test_get_poster_for_date_deterministic():
    d1 = date(2026, 5, 23)
    assert get_poster_for_date(d1) == get_poster_for_date(d1)


def test_get_poster_for_date_rotates_daily():
    d1 = date(2026, 5, 23)
    d2 = date(2026, 5, 24)
    d3 = date(2026, 5, 25)
    assert {
        get_poster_for_date(d1),
        get_poster_for_date(d2),
        get_poster_for_date(d3),
    } == set(POSTER_ROTATION)


def test_poster_rotation_excludes_pome():
    assert "pome" not in POSTER_ROTATION
    assert "pome(非掲載)" not in POSTER_ROTATION


def test_poster_rotation_names_all_resolve_to_staff_ids():
    for name in POSTER_ROTATION:
        assert name in STAFF_IDS, f"POSTER_ROTATION name {name!r} not in STAFF_IDS"


def test_default_category_label_resolves():
    assert DEFAULT_CATEGORY_LABEL in BLOG_CATEGORY_CODES


# ---- high-level post_blog_scheduled validation ----


def test_post_blog_scheduled_rejects_unknown_poster():
    result = post_blog_scheduled(
        title="t", body="b", image_path=None,
        publish_at=datetime(2026, 5, 30, 8, 15),
        poster="not_a_real_staff",
    )
    assert isinstance(result, PostResult)
    assert result.success is False
    assert "Unknown poster" in (result.error or "")


def test_post_blog_scheduled_rejects_unknown_category():
    result = post_blog_scheduled(
        title="t", body="b", image_path=None,
        publish_at=datetime(2026, 5, 30, 8, 15),
        poster="momo",
        category="存在しないカテゴリ",
    )
    assert result.success is False
    assert "Unknown category" in (result.error or "")


def test_post_blog_scheduled_returns_post_result_on_missing_env(monkeypatch):
    """Without RELAY_URL/RELAY_SECRET/SB_ID/SB_PASSWORD, returns PostResult(error=...)."""
    for var in ("RELAY_URL", "RELAY_SECRET", "SALON_BOARD_ID", "SALON_BOARD_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    result = post_blog_scheduled(
        title="t", body="b", image_path=None,
        publish_at=datetime(2026, 5, 30, 8, 15),
        poster="momo",
    )
    assert result.success is False
    assert result.error  # carries a useful message


# ---- PostResult ----


def test_post_result_to_dict():
    r = PostResult(success=True, final_url="https://example.com/done")
    assert r.to_dict() == {
        "success": True,
        "final_url": "https://example.com/done",
        "error": None,
    }


def test_post_result_failure_to_dict():
    r = PostResult(success=False, error="boom")
    assert r.to_dict() == {
        "success": False,
        "final_url": None,
        "error": "boom",
    }
