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
    CouponInfo,
    PostResult,
    _format_body_for_salon_board,
    _hidden_field,
    _parse_coupons,
    _parse_form_tokens,
    _parse_login_state,
    _select_options,
    get_poster_for_date,
    post_blog_scheduled,
    select_coupon_for_theme,
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


# ---- body formatter (plain text — Salon Board escapes HTML in blogContents1) ----


def test_format_body_passes_through_plain_text_with_paragraphs():
    out = _format_body_for_salon_board("para1\n\npara2")
    assert out == "para1\n\npara2"


def test_format_body_preserves_inline_newlines():
    out = _format_body_for_salon_board("line1\nline2")
    assert out == "line1\nline2"


def test_format_body_returns_empty_for_blank_input():
    assert _format_body_for_salon_board("") == ""
    assert _format_body_for_salon_board("   \n  \n") == ""


def test_format_body_normalises_crlf_to_lf():
    assert _format_body_for_salon_board("a\r\nb") == "a\nb"
    assert _format_body_for_salon_board("a\rb") == "a\nb"


def test_format_body_strips_surrounding_whitespace():
    assert _format_body_for_salon_board("\n\nhello\n\n") == "hello"


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


# ---- coupon parser ----


# Cut-down version of the real netCouponList HTML, exercising the three
# couponLabelCT0X target classes and a mix of menu_focus matches.
_COUPON_HTML_SAMPLE = '''
<ul class="couponListArea jscCouponListArea">
  <li>
    <label class="db">
      <input type="hidden" value="CP00000012316921">
      <table>
        <tbody><tr>
          <td class="couponLabelCT02">新規</td>
          <td>
            <div class="couponListBox">
              <p class="jsc_SB_modal_coupon_text b couponText">OPEN記念☆【眉毛WAX】アイブロウ眉毛WAX 《間引き込》</p>
            </div>
          </td>
        </tr></tbody>
      </table>
    </label>
  </li>
  <li>
    <label class="db">
      <input type="hidden" value="CP00000012316878">
      <table>
        <tbody><tr>
          <td class="couponLabelCT02">新規</td>
          <td>
            <div class="couponListBox">
              <p class="jsc_SB_modal_coupon_text b couponText">OPEN記念☆【まつげパーマ】似合わせまつげパーマor次世代まつぱパリジェンヌ</p>
            </div>
          </td>
        </tr></tbody>
      </table>
    </label>
  </li>
  <li>
    <label class="db">
      <input type="hidden" value="CP00000012757522">
      <table>
        <tbody><tr>
          <td class="couponLabelCT01">全員</td>
          <td>
            <div class="couponListBox">
              <p class="jsc_SB_modal_coupon_text b couponText">【初めてで悩んでいる方へ】あなたに最適な似合うメニューをプロがご提案</p>
            </div>
          </td>
        </tr></tbody>
      </table>
    </label>
  </li>
  <li>
    <label class="db">
      <input type="hidden" value="CP00000012317180">
      <table>
        <tbody><tr>
          <td class="couponLabelCT01">全員</td>
          <td>
            <div class="couponListBox">
              <p class="jsc_SB_modal_coupon_text b couponText">【骨格に合う理想の眉に♪】アイブロウ眉毛WAX脱毛</p>
            </div>
          </td>
        </tr></tbody>
      </table>
    </label>
  </li>
  <li>
    <label class="db">
      <input type="hidden" value="CP00000012734401">
      <table>
        <tbody><tr>
          <td class="couponLabelCT03">再来</td>
          <td>
            <div class="couponListBox">
              <p class="jsc_SB_modal_coupon_text b couponText">【まつげ】※40日以内の次回来店 骨格似合せまつ毛パーマorパリジェンヌ</p>
            </div>
          </td>
        </tr></tbody>
      </table>
    </label>
  </li>
</ul>
'''


def test_parse_coupons_extracts_all_entries():
    coupons = _parse_coupons(_COUPON_HTML_SAMPLE)
    assert len(coupons) == 5
    ids = [c.coupon_id for c in coupons]
    assert ids == [
        "CP00000012316921", "CP00000012316878", "CP00000012757522",
        "CP00000012317180", "CP00000012734401",
    ]


def test_parse_coupons_captures_target_label():
    coupons = _parse_coupons(_COUPON_HTML_SAMPLE)
    targets = {c.coupon_id: c.target for c in coupons}
    assert targets["CP00000012316921"] == "新規"
    assert targets["CP00000012757522"] == "全員"
    assert targets["CP00000012734401"] == "再来"


def test_parse_coupons_strips_tags_from_title():
    coupons = _parse_coupons(_COUPON_HTML_SAMPLE)
    assert "<" not in coupons[0].title
    assert "眉毛WAX" in coupons[0].title


def test_parse_coupons_returns_empty_on_no_li():
    assert _parse_coupons("<html>no list</html>") == []


def test_parse_coupons_skips_malformed_li():
    body = "<ul><li>nothing useful</li></ul>"
    assert _parse_coupons(body) == []


# ---- coupon selector ----


def test_select_coupon_prefers_menu_focus_match():
    coupons = _parse_coupons(_COUPON_HTML_SAMPLE)
    sel = select_coupon_for_theme(coupons, "眉毛WAX")
    # Should prefer 新規 within direct matches (#1 is 新規, #4 is 全員)
    assert sel is not None
    assert sel.coupon_id == "CP00000012316921"


def test_select_coupon_falls_back_to_全員_when_no_match():
    coupons = _parse_coupons(_COUPON_HTML_SAMPLE)
    sel = select_coupon_for_theme(coupons, "存在しないメニュー")
    assert sel is not None
    assert sel.target == "全員"


def test_select_coupon_returns_none_for_empty_list():
    assert select_coupon_for_theme([], "眉毛WAX") is None


def test_select_coupon_case_insensitive_ascii():
    # Coupon title has "眉毛WAX"; user might pass "眉毛wax"
    coupons = _parse_coupons(_COUPON_HTML_SAMPLE)
    sel = select_coupon_for_theme(coupons, "眉毛wax")
    assert sel is not None
    assert sel.coupon_id == "CP00000012316921"


def test_select_coupon_matsuge_perma_prefers_新規():
    coupons = _parse_coupons(_COUPON_HTML_SAMPLE)
    sel = select_coupon_for_theme(coupons, "まつげパーマ")
    # #2 (CP...878) is 新規 → should be selected over the 全員/再来 options
    assert sel is not None
    assert sel.coupon_id == "CP00000012316878"


def test_select_coupon_uses_theme_keyword_fallback():
    # menu_focus doesn't match anything, but theme contains "パリジェンヌ"
    # which appears in coupon titles → should match.
    coupons = _parse_coupons(_COUPON_HTML_SAMPLE)
    sel = select_coupon_for_theme(coupons, "存在しない", theme="パリジェンヌでぱっちり目元")
    assert sel is not None
    assert "パリジェンヌ" in sel.title


def test_coupon_info_dataclass_fields():
    c = CouponInfo(coupon_id="CP000", title="t", target="新規")
    assert c.coupon_id == "CP000"
    assert c.title == "t"
    assert c.target == "新規"
