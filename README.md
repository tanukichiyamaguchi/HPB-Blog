# HPB ブログ自動投稿システム

ホットペッパービューティー（HPB）のサロンボード管理画面に対して、毎日自動でブログを投稿するシステム。GitHub Actions 上で完全クラウド完結で動作します。

対象サロン: **KATEstageLASH（ケイトステージラッシュ）** 蒲田駅西口店

---

## 概要

- 毎日 JST 22:00（UTC 13:00）に GitHub Actions が起動
- Claude API で本文生成、Gemini API でアイキャッチ画像生成
- Playwright でサロンボードに自動ログインし、翌朝 8:15 公開の予約投稿として保存
- Slack Incoming Webhook で成功／失敗を通知
- 365日毎日実行

## アーキテクチャ

```
[GitHub Actions cron 22:00 JST]
        │
        ▼
┌──────────────────────────────────────┐
│ src/main.py (エントリポイント)        │
└──────────────────────────────────────┘
        │
        ├─▶ theme_generator.py    (Claude / テーマ生成)
        ├─▶ blog_writer.py        (Claude / 本文生成)
        ├─▶ image_generator.py    (Gemini / 画像生成)
        ├─▶ salon_board_poster.py (Playwright / 投稿)
        └─▶ notifier.py           (Slack 通知)
                │
                ▼
        data/theme_history.json (履歴コミット)
```

## ディレクトリ構成

```
.
├── .github/workflows/
│   ├── daily-blog.yml      # 本番（毎日 22:00 JST 起動）
│   └── manual-test.yml     # 手動テスト用
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── theme_generator.py
│   ├── blog_writer.py
│   ├── image_generator.py
│   ├── salon_board_poster.py
│   └── notifier.py
├── data/
│   ├── theme_history.json
│   └── prompts/
│       ├── blog_prompt.md
│       └── theme_prompt.md
├── tests/
├── screenshots/            # ランタイム生成（.gitignore 対象）
├── output/                 # ランタイム生成（.gitignore 対象）
├── requirements.txt
├── .gitignore
└── README.md
```

## セットアップ手順

### 1. リポジトリ準備

```bash
git clone <this-repo>
cd HPB-Blog
```

### 2. GitHub Secrets の登録

リポジトリ設定 `Settings → Secrets and variables → Actions` で以下を登録します。

| Secret Name | 用途 |
|-------------|------|
| `ANTHROPIC_API_KEY` | Claude API（テーマ・本文生成） |
| `GEMINI_API_KEY` | Gemini API（画像生成） |
| `SALON_BOARD_ID` | サロンボードログインID |
| `SALON_BOARD_PASSWORD` | サロンボードログインパスワード |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

### 3. ローカル開発（任意）

ローカル実行は本番運用想定外ですが、デバッグ用に動作させる場合：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env  # シークレットを記入
python -m src.main
```

## 運用方法

### 本番運用

`.github/workflows/daily-blog.yml` の cron が JST 22:00 に自動起動します。
ジョブの進行は Actions タブで確認、Slack 通知でも結果が届きます。

### 手動テスト

`.github/workflows/manual-test.yml` を Actions タブから「Run workflow」で任意のタイミングで実行できます。生成物（本文 .txt と画像 .png、デバッグスクリーンショット）は Artifacts として取得可能です。

### スケジュール変更

`daily-blog.yml` の cron 値を編集してください（UTC 表記）。
例：JST 22:00 = UTC 13:00 → `cron: '0 13 * * *'`

## 制約事項

1. **APIキー・パスワードはハードコード禁止**。すべて環境変数経由で読み込みます。
2. **サロンボードへのアクセスは 1 日 1 回厳守**。高頻度アクセスは行いません。
3. **画像生成プロンプトに「ビフォーアフター」「術前」「術後」を含めない**。
4. **薬機法配慮**。効果効能の断定表現を避けます。
5. **GitHub Actions の遅延**を見込み、公開時刻には十分な余裕を確保しています。

## 開発ステータス

- [x] Phase 1: 基本セットアップ
- [ ] Phase 2: AI生成パート（テーマ／本文／画像）
- [ ] Phase 3: サロンボード自動操作
- [ ] Phase 4: Slack通知統合
- [ ] Phase 5: 本番ワークフロー統合
- [ ] Phase 6: 本番運用前の最終確認
