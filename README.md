# HPB ブログ自動投稿システム

ホットペッパービューティー（HPB）のサロンボード管理画面に対して、毎日自動でブログを投稿するシステム。

対象サロン: **KATEstageLASH（ケイトステージラッシュ）** 蒲田駅西口店

---

## 運用モード

GitHub Actions ランナーは米国IPで salonboard.com への接続が遮断されるため、
サロンボードへの自動投稿は **日本IPから実行する必要** があります。

### 🔵 推奨: Oracle Cloud Tokyo Always Free（**永久無料・ブラウザのみ**）

完全無料の Oracle Cloud Tokyo Always Free Tier 上に Ubuntu VM を立て、
**Linux cron が JST 22:15 に毎日自動実行** → 翌朝8:15 公開予約。
セットアップから運用まで **ブラウザのみ**（PCへのインストール一切不要）。

セットアップは **[docs/ORACLE_CLOUD_SETUP.md](docs/ORACLE_CLOUD_SETUP.md)** を参照（所要1.5〜2時間）。

VM 内ブラウザ Cloud Shell で以下を貼るだけで自動セットアップ:
```bash
curl -fsSL https://raw.githubusercontent.com/tanukichiyamaguchi/HPB-Blog/main/scripts/install_on_vps.sh | bash
```

### 🟡 代替: 有料VPS（日本リージョン）

お名前.com VPS（¥520〜）/ さくらのVPS（¥683〜）/ Conoha VPS（¥483〜）。
セットアップ手順は **[docs/VPS_SETUP.md](docs/VPS_SETUP.md)**。
Oracle Cloud で Out of Capacity が続く場合や安定運用を求める場合の選択肢。

### 🟢 代替: Windows PC で週次バッチ

サロン PC で **週1回 `run_weekly.bat` をダブルクリック**、**7日分の予約投稿**
を一括で登録する方式。コスト無料だが PC への Python 環境構築が必要。
セットアップは **[docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md)**。

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

## トラブルシューティング

### Salon Board に接続できない（GitHub Actions ランナーから）

GitHub Actions の公式ホストランナー（Microsoft Azure 米国 IP）からは
`salonboard.com` への TCP 接続が遮断されます（リクルート社側の WAF 等
による地理的ブロックと推定）。HTTP プリフライトログに
`Read timed out` が出る場合がこれです。

回避策（いずれも **日本IPからの実行** が必要）:

1. **GitHub Actions self-hosted runner（推奨）**
   日本国内の PC / VPS / クラウド VM 上に
   [GitHub Actions runner](https://docs.github.com/actions/hosting-your-own-runners)
   をインストールし、`daily-blog.yml` の `runs-on:` を
   `self-hosted` または専用ラベルに変更。
2. **日本リージョンの常時稼働 VM**（Oracle Cloud Tokyo Free Tier、
   さくらのVPS、Conoha、AWS Lightsail Tokyo 等）に runner を配置。
3. **日本IP出口プロキシ**（Bright Data の Japan residential 等、有料）を
   `SALON_BOARD_PROXY` env で指定（コード側で受け取り口は実装済み）。

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
