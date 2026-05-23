# Windows セットアップガイド

このシステムをサロンの Windows PC で動作させる手順です。**所要時間：初回 30 分程度／2回目以降は数分**。

---

## 1. 必要なソフトウェアのインストール

### 1-1. Python 3.11 以上

1. https://www.python.org/downloads/ にアクセス
2. 「Download Python 3.x.x」ボタンをクリックしてインストーラをダウンロード
3. インストーラを実行する際、**「Add Python to PATH」に必ずチェック** を入れてから「Install Now」
4. インストール完了後、コマンドプロンプトで `python --version` を実行して `Python 3.11.x` 等が表示されればOK

### 1-2. Git for Windows（任意）

GitHub からコードを取得するために使います（ZIP ダウンロードでも可）。

1. https://git-scm.com/download/win からインストーラをダウンロード
2. デフォルト設定のままインストール

> ZIP で取得する場合: GitHub の本リポジトリページ → 「Code」→「Download ZIP」→ 解凍

---

## 2. リポジトリの取得

任意の場所（例：`C:\Users\<ユーザ名>\Documents\hpb-blog`）に配置します。

### Git を使う場合
```cmd
cd C:\Users\<ユーザ名>\Documents
git clone https://github.com/tanukichiyamaguchi/HPB-Blog.git hpb-blog
cd hpb-blog
```

### ZIP の場合
1. ダウンロードした ZIP を解凍
2. 解凍されたフォルダの名前を `hpb-blog` 等に変更
3. 適切な場所（例：`C:\Users\<ユーザ名>\Documents\hpb-blog`）に移動

---

## 3. 初期セットアップ

リポジトリのフォルダを **エクスプローラで開いて**、`scripts\setup.bat` を **ダブルクリック** します。

初回は以下が自動で実行されます（5〜10 分）:
- 仮想環境の作成
- 依存ライブラリのインストール
- Playwright Chromium ブラウザのダウンロード（約 150MB）
- `.env` テンプレートの作成

ウィンドウに `[SUCCESS] セットアップ完了` と出れば成功です。

---

## 4. シークレット情報の設定

リポジトリ直下に作成された `.env` ファイルを **メモ帳** で開き、各項目に値を記入します。

```
# Claude API キー（テーマ・本文生成用）
ANTHROPIC_API_KEY=sk-ant-...

# Gemini API キー（画像生成用）
GEMINI_API_KEY=...

# サロンボードのログイン情報
SALON_BOARD_ID=CE44570
SALON_BOARD_PASSWORD=...

# Slack 通知用（任意。設定しなくても動作）
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### 各 API キーの取得方法

| サービス | 取得URL | 備考 |
|---------|--------|------|
| Anthropic (Claude) | https://console.anthropic.com/ | クレジットカード登録必要、月数ドル程度 |
| Google Gemini | https://aistudio.google.com/apikey | 無料枠あり |
| Slack Webhook | https://api.slack.com/messaging/webhooks | Slack 利用者のみ |

`.env` ファイルを保存して閉じます。

---

## 5. 動作確認（テスト実行）

サロンボードに投稿せず、AI 生成だけテストします。

`scripts\run_test_only.bat` を **ダブルクリック**。

1〜2 分後、`output\YYYY-MM-DD\` フォルダに以下が生成されます:
- `blog.txt` — 本文
- `title.txt` — タイトル
- `image.jpg` — アイキャッチ画像
- `meta.json` — メタ情報

開いて内容を確認してください。問題なければ次へ。

---

## 6. 週次バッチ実行（本番）

`scripts\run_weekly.bat` を **ダブルクリック**。

以下が自動で実行されます（10〜15 分）:

1. **7 日分のテーマを AI で生成**（重複しないよう自動調整）
2. 各日の本文と画像を生成
3. サロンボードに **1回ログイン**
4. 7 件の投稿を 翌日〜7日後の **8:15 公開予約** として登録
5. Slack に完了通知

ウィンドウに `[SUCCESS] 7日分の予約投稿が完了しました` と出れば成功。

### 完了後の確認

- サロンボード（https://salonboard.com/login/）にログインし、ブログ一覧で 7 件の予約投稿が登録されていることを確認
- 投稿者がローテーション（momo / aoi / ケイト 蒲田西口店）されていることを確認

---

## 7. 運用フロー

**毎週、サロン PC が起動しているタイミングで `run_weekly.bat` をダブルクリック** するだけです。

### おすすめ運用

- **毎週日曜の夜** や **定休日に作業として組み込む** など、固定の曜日にすると忘れにくいです
- Windows の「タスク スケジューラ」で自動実行することも可能（PC 起動時間が固定なら）

---

## トラブルシューティング

### `.bat をダブルクリックしたら一瞬で閉じる`
コマンドプロンプトを開いてから `scripts\setup.bat` の絶対パスを入力して実行するとエラーが残ります。スクリーンショットを送ってください。

### `Python が見つかりません` のエラー
インストール時に「Add Python to PATH」のチェックを入れていない可能性があります。Python を **再インストール** し、必ずチェックを入れてください。

### サロンボードのログインで止まる
- `.env` の `SALON_BOARD_ID` と `SALON_BOARD_PASSWORD` を再確認
- `screenshots\` フォルダ内の画像を見て、どのステップで止まっているか確認

### API 利用料が高くなる
- 7日分一括生成で 1 回あたり Claude + Gemini で約 $0.5〜1（月 $2〜4 程度）
- 上振れする場合は Anthropic / Google AI Studio の利用ダッシュボードで内訳を確認

### 投稿が失敗した日がある
- 一部失敗（例：5日目のみ失敗）の場合、`output\YYYY-MM-DD\salon_board_result.json` の `success: false` のディレクトリを削除して `run_weekly.bat` を再実行すれば、その日だけ再投稿されます

---

## 補足: シークレットの取り扱い

- `.env` ファイルは **絶対に他人に渡さない**（GitHub にも .gitignore で送られない設定済み）
- API キーが漏れた場合は速やかに該当サービスでローテーション
