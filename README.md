# HPB ブログ自動投稿システム

ホットペッパービューティー（HPB）のサロンボード管理画面に対して、毎日自動でブログを投稿するシステム。

対象サロン: **KATEstageLASH（ケイトステージラッシュ）** 蒲田駅西口店

---

## 運用モード

### 🔵 推奨: GitHub Actions で完全クラウド運用（**永久無料・追加環境不要**）

salonboard.com の Akamai Bot Manager は **Chromium 系の TLS フィンガープリント
のみ** を選択的に遮断していることが診断で判明（Firefox/WebKit は通過）。
Playwright を **Firefox エンジン** で動かすことで、米国 GitHub Actions ランナー
（US Azure IP）からそのままアクセス可能。

| 項目 | 状況 |
|------|------|
| 月額費用 | **¥0**（GitHub Actions 無料枠内）|
| VPS / PC 設定 | **不要** |
| 設定変更 | Repo Variables `ENABLE_DAILY_CRON=true` をセットするだけ |

cron は `.github/workflows/daily-blog.yml` が **JST 22:15** に自動起動 →
翌朝 8:15 公開予約として 1 日 1 件を Salon Board に登録。

### 🟡 代替: Windows PC で週次バッチ

サロン PC で **週1回 `run_weekly.bat` をダブルクリック**、**7日分の予約投稿**
を一括で登録する方式。セットアップ手順は **[docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md)**。

### 🟡 代替: 日本VPS / Oracle Cloud Tokyo Free（GitHub Actions が将来塞がれた場合）

将来 Akamai が Firefox TLS も遮断したり、運用上の理由で日本IP出口が必要に
なった場合のバックアップ。詳細は **[docs/VPS_SETUP.md](docs/VPS_SETUP.md)** /
**[docs/ORACLE_CLOUD_SETUP.md](docs/ORACLE_CLOUD_SETUP.md)**。

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

`.github/workflows/daily-blog.yml` の cron が **JST 22:15**（UTC 13:15）に自動起動します。
ジョブの進行は Actions タブで確認、Slack 通知でも結果が届きます。

### 手動テスト

`.github/workflows/manual-test.yml` を Actions タブから「Run workflow」で任意のタイミングで実行できます。生成物（本文 .txt と画像 .png、デバッグスクリーンショット）は Artifacts として取得可能です。

### スケジュール変更

`daily-blog.yml` の cron 値を編集してください（UTC 表記）。
例：JST 22:15 = UTC 13:15 → `cron: '15 13 * * *'`

## ネットワーク診断

GitHub Actions IPからサロンボードへの到達性を確認するための診断ワークフロー。

実行方法：
1. GitHub リポジトリの **Actions** タブを開く
2. 左メニューから **Network Diagnostic** を選択
3. **Run workflow** ボタンをクリック
4. 実行完了後、ログを確認

## トラブルシューティング

### Salon Board に接続できない（タイムアウトする）

**原因**: salonboard.com の Akamai Bot Manager は **Chromium 系の TLS
フィンガープリント** を選択的に遮断します。Playwright の `chromium.launch()`
や Python の `requests` / `curl` はこれに該当し、TLS ハンドシェイクは通るが
HTTP レスポンスが返らず 30 秒で timeout します。

**対応**: 本リポジトリでは `firefox.launch()` を使用しており、診断で
Firefox / WebKit / curl_cffi の Safari17・Firefox133 フィンガープリントが
HTTP 200 で通ることを確認済（2026年1月時点）。コードを Chromium ベースに
書き戻すと再発します。

将来 Firefox も遮断された場合のバックアップ:
- 日本 IP の VPS / Oracle Cloud → **[docs/VPS_SETUP.md](docs/VPS_SETUP.md)**
- 日本 IP の self-hosted runner → `daily-blog.yml` の `runs-on:` を
  `self-hosted` に変更

診断ワークフロー：`.github/workflows/network-diagnostic.yml` /
`extra-diagnostics.yml` で `salonboard.com` への到達性を任意に検証可能。

### モデルが見つからない（404 / model_not_found）

`CLAUDE_MODEL` / `GEMINI_IMAGE_MODEL` を Repo Variables で
別の既知モデル名に上書き可能：

- 例: `GEMINI_IMAGE_MODEL=gemini-2.5-flash-image-preview`

### `LOG_LEVEL=DEBUG` でログを詳細化

Repo Variables もしくは workflow_dispatch input で `LOG_LEVEL=DEBUG` を
指定すると、セレクタ試行・各 retry の詳細などが出力されます。

## 制約事項

1. **APIキー・パスワードはハードコード禁止**。すべて環境変数経由で読み込みます。
2. **サロンボードへのアクセスは 1 日 1 回厳守**。高頻度アクセスは行いません。
3. **画像生成プロンプトに「ビフォーアフター」「術前」「術後」を含めない**。
4. **薬機法配慮**。効果効能の断定表現を避けます。
5. **GitHub Actions の遅延**を見込み、公開時刻には十分な余裕を確保しています。

## 動作モード（環境変数）

`src/main.py` は環境変数で挙動を切り替えます。

| 環境変数 | 値 | 効果 |
|---------|----|----|
| `RUN_SALON_BOARD_POST` | `skip`（既定） | AI生成のみ。サロンボード操作なし。 |
| `RUN_SALON_BOARD_POST` | `draft` | サロンボードへ下書き保存（Phase 3 検証用） |
| `RUN_SALON_BOARD_POST` | `schedule` | 翌朝 8:15 JST の予約投稿として保存（本番用） |
| `UPDATE_THEME_HISTORY` | `true` | サロンボード成功後に `data/theme_history.json` へ追記 |
| `SLACK_WEBHOOK_URL` | URL | 成功/失敗で Slack 通知（未設定なら通知スキップ） |
| `GITHUB_RUN_URL` | URL | 失敗通知に Actions Run URL を載せる |

## 本番 cron 有効化手順

`.github/workflows/daily-blog.yml` は安全ガードあり：

1. cron スケジュールは `15 13 * * *`（JST 22:15）で記載済み
2. ただし job-level `if` で **`vars.ENABLE_DAILY_CRON == 'true'`** をチェック
3. 本番稼働には GitHub Repo `Settings → Variables → Actions` で `ENABLE_DAILY_CRON=true` を登録
4. 未登録の間、cron が起動してもジョブはスキップされる

`workflow_dispatch`（手動 trigger）は常に動作するため、Phase 6 の1週間検証はそれで実施できます。

## 開発ステータス

- [x] Phase 1: 基本セットアップ
- [x] Phase 2: AI生成パート（テーマ／本文／画像）
- [x] Phase 3: サロンボード自動操作（下書き保存）
- [x] Phase 4: Slack通知統合
- [x] Phase 5: 本番ワークフロー統合
- [ ] Phase 6: 本番運用前の最終確認（実機での selector 検証＋1週間稼働）
