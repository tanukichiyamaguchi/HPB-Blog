# VPS セットアップガイド（クラウド完結運用）

このシステムを **日本リージョンのVPS上で完全クラウド運用** する手順です。
PCには何もインストール不要、すべてブラウザの「Webコンソール」で操作します。

**所要時間**: 初回 60〜90分／以降はゼロ（cron が自動実行）

---

## 1. VPS プロバイダの選定

以下から1つ選んでください。どれも **日本リージョン・ブラウザコンソール対応・SSH 不要** です。

### 推奨①: お名前.com VPS（同じお名前.comアカウントで請求まとめ）

- **料金**: ¥520/月〜（1GB プラン）
- **OS**: Ubuntu 24.04 LTS を選択
- **申込**: https://www.onamae-server.com/vps/

### 推奨②: さくらのVPS（実績豊富・日本語サポート）

- **料金**: ¥683/月〜（512MB プラン）
- **OS**: Ubuntu 24.04 LTS を選択
- **申込**: https://vps.sakura.ad.jp/

### 推奨③: Conoha VPS（時間課金あり）

- **料金**: ¥483/月〜（1GB プラン）
- **OS**: Ubuntu 24.04 を選択
- **申込**: https://www.conoha.jp/vps/

### スペック要件（最小）

| 項目 | 必要 |
|------|------|
| メモリ | 1GB 以上（Playwright Chromium 用） |
| ストレージ | 10GB 以上 |
| OS | Ubuntu 22.04 / 24.04 LTS |
| リージョン | 日本 |

> 512MB プランでも動きますが、長期運用なら 1GB が安全です。

---

## 2. VPS 申込（例: お名前.com VPS）

1. https://www.onamae-server.com/vps/ にアクセス
2. プラン: 「1GB プラン（月額¥520）」を選択
3. OS: 「**Ubuntu 24.04 LTS**」 を選択
4. オプション: 不要（特に追加機能なし）
5. お名前.com アカウントでログイン → 申込完了
6. **管理コンソール** にログイン → VPS の **IPアドレス** と **root パスワード** を控える

---

## 3. Webコンソールでサーバに入る

1. お名前.com 管理画面 → VPS の **「コンソール」** ボタン
2. ブラウザに黒い画面（ターミナル）が表示される
3. ログイン画面で:
   ```
   login: root
   Password: （契約時に通知されたパスワード）
   ```

> 他のプロバイダの場合も同様。「Webコンソール」「コンソール」「リモートコンソール」等の名称で同等機能あり。

---

## 4. 一発インストール

ログイン後のターミナルに **以下の1行を貼り付けてEnter** を押します:

```bash
curl -fsSL https://raw.githubusercontent.com/tanukichiyamaguchi/HPB-Blog/main/scripts/install_on_vps.sh | bash
```

> ※ブランチが `main` でない場合は `claude/vibrant-faraday-Ig38K` 等に置き換え

これで以下が自動実行されます（合計5〜10分）:

| ステップ | 内容 |
|---------|------|
| 1/6 | Python 3.11 / git / Chromium 依存ライブラリ |
| 2/6 | リポジトリを `/opt/hpb-blog/` にクローン |
| 3/6 | Python 仮想環境 + 依存ライブラリ |
| 4/6 | Playwright Chromium ダウンロード（約150MB） |
| 5/6 | `.env` テンプレート作成 |
| 6/6 | **daily cron 自動登録**（JST 22:15 = UTC 13:15） |

最後に `[SUCCESS] Installation complete.` が出れば OK。

---

## 5. シークレットの設定

ターミナルに以下を貼り付け：

```bash
nano /opt/hpb-blog/.env
```

エディタが開きます。各行に値を記入してください：

```
ANTHROPIC_API_KEY=sk-ant-XXXXXXXX
GEMINI_API_KEY=XXXXXXXX
SALON_BOARD_ID=CE44570
SALON_BOARD_PASSWORD=XXXXXXXX
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX
```

### 操作:
- 矢印キーで移動
- 文字をタイプして書き込み
- 保存: **Ctrl+O → Enter**
- 終了: **Ctrl+X**

### 各 API キーの取得:

| サービス | URL |
|---------|------|
| Anthropic (Claude) | https://console.anthropic.com/ |
| Google Gemini | https://aistudio.google.com/apikey |
| Slack Webhook | https://api.slack.com/messaging/webhooks |

---

## 6. 動作確認

### 6-1. AI 生成テスト（Salon Board 触らず）

ターミナルに:
```bash
cd /opt/hpb-blog && RUN_SALON_BOARD_POST=skip .venv/bin/python -m src.main
```

1〜2分後、`/opt/hpb-blog/output/YYYY-MM-DD/` にファイルが生成されたら OK。

中身を確認するには:
```bash
ls -la /opt/hpb-blog/output/$(date +%Y-%m-%d)/
cat /opt/hpb-blog/output/$(date +%Y-%m-%d)/blog.txt
```

### 6-2. Salon Board 下書き保存テスト

```bash
cd /opt/hpb-blog && RUN_SALON_BOARD_POST=draft .venv/bin/python -m src.main
```

完了後、サロンボードのブログ一覧画面で下書きが作成されているか確認。

### 6-3. Salon Board 予約投稿テスト

```bash
cd /opt/hpb-blog && RUN_SALON_BOARD_POST=schedule .venv/bin/python -m src.main
```

サロンボードのブログ一覧画面で **翌朝 8:15 公開予約** として登録されているか確認。

---

## 7. 本番運用

すでに cron が登録済みです:

```cron
15 13 * * * (= JST 22:15 daily)
  → RUN_SALON_BOARD_POST=schedule で 1日1回自動実行
  → 翌朝 8:15 公開予約として Salon Board に登録
```

確認:
```bash
crontab -l
```

これで完了です。**以降ユーザの操作不要**で 365日自動投稿されます。

---

## 8. 運用監視

### Slack 通知

`.env` に `SLACK_WEBHOOK_URL` を設定しておけば、毎日成功/失敗が通知されます。

### ログ確認（Web コンソールから）

```bash
# 最新の cron 実行ログ
tail -100 /opt/hpb-blog/logs/cron-$(date +%Y%m).log

# 直近のスクリーンショット一覧
ls -la /opt/hpb-blog/screenshots/

# 直近の出力
ls -la /opt/hpb-blog/output/
```

### 設定変更（モデル/タイムアウト等）

```bash
nano /opt/hpb-blog/.env
```

変更後、次回 cron 実行から自動的に反映されます。

---

## トラブルシューティング

### 「installation failed」と出る

OS が Ubuntu 22.04/24.04 でない可能性。バージョン確認：
```bash
lsb_release -a
```

### cron が動かない

```bash
# crontab を確認
crontab -l

# cron daemon の状態
systemctl status cron

# 直近の cron 実行ログ
grep CRON /var/log/syslog | tail -20
```

### Salon Board 投稿が失敗する

```bash
# 直近のスクリーンショット
ls -lt /opt/hpb-blog/screenshots/ | head -20

# 直近のエラーログ
tail -200 /opt/hpb-blog/logs/cron-$(date +%Y%m).log
```

スクリーンショットを確認して、どのステップで止まったかを教えてください。
セレクタ調整のコミットを行います。

### API 料金を抑えたい

`.env` で安価なモデルに切替:
```
CLAUDE_MODEL=claude-haiku-4-5
GEMINI_IMAGE_MODEL=gemini-2.5-flash-image-preview
```

### コードを更新したい

```bash
cd /opt/hpb-blog
git pull --ff-only origin main
.venv/bin/pip install -r requirements.txt
```

---

## 補足: セキュリティ

- VPS の root パスワードを定期的に変更
- `.env` のパーミッションは 600 推奨:
  ```bash
  chmod 600 /opt/hpb-blog/.env
  ```
- 不要なポートはファイアウォール（ufw）で閉じる:
  ```bash
  sudo ufw allow OpenSSH
  sudo ufw enable
  ```
