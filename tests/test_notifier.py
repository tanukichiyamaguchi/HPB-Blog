from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.notifier import (
    NotificationResult,
    _post_to_slack,
    build_failure_payload,
    build_success_payload,
    notify_failure,
    notify_success,
)


def test_build_success_payload_includes_title_and_theme():
    payload = build_success_payload(
        title="蒲田の眉メイク",
        theme="梅雨に映える眉",
        menu_focus="眉毛WAX",
    )
    assert payload["text"].startswith("HPBブログ自動投稿 成功")
    text_dump = str(payload)
    assert "蒲田の眉メイク" in text_dump
    assert "梅雨に映える眉" in text_dump
    assert "眉毛WAX" in text_dump


def test_build_success_payload_with_image_url():
    payload = build_success_payload(
        title="t",
        theme="t",
        menu_focus="m",
        image_url="https://example.com/img.png",
    )
    # Should have an image block
    blocks = payload["blocks"]
    image_blocks = [b for b in blocks if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"] == "https://example.com/img.png"


def test_build_success_payload_with_image_path_only():
    payload = build_success_payload(
        title="t",
        theme="t",
        menu_focus="m",
        image_path=Path("/tmp/x.png"),
    )
    blocks = payload["blocks"]
    context_blocks = [b for b in blocks if b.get("type") == "context"]
    assert any("Artifacts" in str(b) for b in context_blocks)


def test_build_success_payload_with_final_url():
    payload = build_success_payload(
        title="t",
        theme="t",
        menu_focus="m",
        final_url="https://salonboard.com/blog/123",
    )
    assert "https://salonboard.com/blog/123" in str(payload)


def test_build_failure_payload_includes_stage_and_error():
    payload = build_failure_payload(
        error_message="boom",
        stage="login",
    )
    assert payload["text"].startswith("HPBブログ自動投稿 失敗")
    assert "login" in str(payload)
    assert "boom" in str(payload)


def test_build_failure_payload_truncates_long_errors():
    long_msg = "x" * 5000
    payload = build_failure_payload(
        error_message=long_msg,
        stage="stage",
    )
    s = str(payload)
    # We trim to 1500 chars; total length should be well below the original
    assert long_msg not in s


def test_build_failure_payload_with_screenshots():
    payload = build_failure_payload(
        error_message="e",
        stage="s",
        screenshots=[Path("/x/01_login.png"), Path("/x/02_form.png")],
    )
    s = str(payload)
    assert "01_login.png" in s
    assert "02_form.png" in s


def test_build_failure_payload_with_run_url():
    payload = build_failure_payload(
        error_message="e",
        stage="s",
        run_url="https://github.com/owner/repo/actions/runs/1",
    )
    assert "https://github.com/owner/repo/actions/runs/1" in str(payload)


def test_post_to_slack_returns_success_on_200():
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status.return_value = None
    with patch("src.notifier.requests.post", return_value=mock_resp) as m:
        result = _post_to_slack("https://hooks.slack.com/x", {"text": "hi"})
    assert result.sent is True
    assert result.status_code == 200
    m.assert_called_once()


def test_post_to_slack_returns_failure_on_error():
    def raise_err(*a, **kw):
        raise requests.ConnectionError("nope")

    with patch("src.notifier.requests.post", side_effect=raise_err):
        result = _post_to_slack("https://hooks.slack.com/x", {"text": "hi"})
    assert result.sent is False
    assert "nope" in (result.error or "")


def test_notify_success_skips_when_no_webhook(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = notify_success(title="t", theme="t", menu_focus="m")
    assert result.sent is False
    assert "SLACK_WEBHOOK_URL" in (result.error or "")


def test_notify_failure_skips_when_no_webhook(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = notify_failure(error_message="boom", stage="x")
    assert result.sent is False


def test_notify_success_uses_provided_webhook():
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status.return_value = None
    with patch("src.notifier.requests.post", return_value=mock_resp) as m:
        result = notify_success(
            title="t", theme="t", menu_focus="m",
            webhook_url="https://hooks.slack.com/x",
        )
    assert result.sent is True
    # Verify POST URL
    assert m.call_args.args[0] == "https://hooks.slack.com/x"
