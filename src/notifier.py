"""Slack notification via Incoming Webhook."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.config import API_RETRY_ATTEMPTS, API_RETRY_BASE_DELAY_SEC
from src.utils import retry

log = logging.getLogger(__name__)


SLACK_WEBHOOK_TIMEOUT_S = int(os.environ.get("SLACK_WEBHOOK_TIMEOUT_S", "10"))


@dataclass
class NotificationResult:
    sent: bool
    status_code: int | None = None
    error: str | None = None


def _post_to_slack(webhook_url: str, payload: dict[str, Any]) -> NotificationResult:
    def _call() -> requests.Response:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=SLACK_WEBHOOK_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp

    try:
        resp = retry(
            _call,
            max_attempts=API_RETRY_ATTEMPTS,
            base_delay=API_RETRY_BASE_DELAY_SEC,
            logger=log,
        )
        return NotificationResult(sent=True, status_code=resp.status_code)
    except Exception as e:  # noqa: BLE001
        log.exception("Slack notification failed")
        return NotificationResult(sent=False, error=f"{type(e).__name__}: {e}")


def build_success_payload(
    title: str,
    theme: str,
    menu_focus: str,
    image_path: Path | None = None,
    image_url: str | None = None,
    final_url: str | None = None,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "✅ HPBブログ自動投稿 成功"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*タイトル:*\n{title}"},
                {"type": "mrkdwn", "text": f"*テーマ:*\n{theme}"},
                {"type": "mrkdwn", "text": f"*メニュー軸:*\n{menu_focus}"},
            ],
        },
    ]
    if final_url:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*投稿URL:* <{final_url}>"},
        })
    if image_url:
        blocks.append({
            "type": "image",
            "image_url": image_url,
            "alt_text": title,
        })
    elif image_path:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*画像:* `{image_path}` (Artifacts 参照)"},
            ],
        })
    return {"blocks": blocks, "text": f"HPBブログ自動投稿 成功: {title}"}


def build_failure_payload(
    error_message: str,
    stage: str,
    screenshots: list[Path] | None = None,
    run_url: str | None = None,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 HPBブログ自動投稿 失敗"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*失敗フェーズ:*\n{stage}"},
                {"type": "mrkdwn", "text": f"*エラー:*\n```{error_message[:1500]}```"},
            ],
        },
    ]
    if run_url:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*GitHub Actions Run:* <{run_url}>"},
        })
    if screenshots:
        names = "\n".join(f"• `{p.name}`" for p in screenshots[:10])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*スクリーンショット (Artifacts):*\n{names}"},
        })
    return {"blocks": blocks, "text": f"HPBブログ自動投稿 失敗: {stage}"}


def notify_success(
    title: str,
    theme: str,
    menu_focus: str,
    image_path: Path | None = None,
    image_url: str | None = None,
    final_url: str | None = None,
    webhook_url: str | None = None,
) -> NotificationResult:
    url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        log.warning("SLACK_WEBHOOK_URL not set; skipping success notification")
        return NotificationResult(sent=False, error="SLACK_WEBHOOK_URL not set")
    payload = build_success_payload(
        title=title,
        theme=theme,
        menu_focus=menu_focus,
        image_path=image_path,
        image_url=image_url,
        final_url=final_url,
    )
    return _post_to_slack(url, payload)


def notify_failure(
    error_message: str,
    stage: str,
    screenshots: list[Path] | None = None,
    run_url: str | None = None,
    webhook_url: str | None = None,
) -> NotificationResult:
    url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        log.warning("SLACK_WEBHOOK_URL not set; skipping failure notification")
        return NotificationResult(sent=False, error="SLACK_WEBHOOK_URL not set")
    payload = build_failure_payload(
        error_message=error_message,
        stage=stage,
        screenshots=screenshots,
        run_url=run_url,
    )
    return _post_to_slack(url, payload)
