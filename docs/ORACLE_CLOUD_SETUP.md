# Oracle Cloud Tokyo Always Free セットアップガイド

このシステムを **Oracle Cloud Tokyo Always Free Tier** で **完全無料・ブラウザのみ** で運用する手順です。

**所要時間**: 初回 1.5〜2時間／以降はゼロ
**コスト**: ¥0 ／月（永久無料枠を使用）
**PC への必要インストール**: 何もなし

---

## 0. 前提

- **クレジットカード**: 本人確認のため登録必須（**課金は発生しません**）
- ブラウザ: Chrome / Edge / Firefox / Safari いずれか
- メールアドレス: アカウント認証用

---

## 1. Oracle Cloud アカウント作成

1. https://www.oracle.com/jp/cloud/free/ にアクセス
2. 「**無料で始める**」をクリック
3. メールアドレス・国（日本）・氏名を入力 → メール認証
4. 認証メールのリンクをクリック → 続きの登録画面へ

### 重要：ホームリージョン選択

5. 登録画面で **「ホームリージョン」を必ず「Japan East (Tokyo)」を選択**
   - ⚠️ **ホームリージョンは後から変更できません**
   - 東京以外を選ぶと Geo Block 回避ができなくなります
6. 住所・電話番号入力
7. **クレジットカード情報入力**
   - 本人確認用、無料枠内では課金されません
   - 念のため後述する「課金されない設定」を確認
8. 規約同意 → 登録完了

アカウントが有効化されるまで数分〜数時間かかることがあります。完了メールが届くまで待ちます。

---

## 2. Always Free 対象を確認

ログイン後、ブラウザ右下に「**Always Free Eligible**」のバッジが付いているリソースのみ無料です。
今回作る Compute Instance も Always Free Eligible のシェイプを選びます。

### 無料枠（東京リージョンで使えるもの）

| リソース | 無料枠 |
|---------|--------|
| Compute (ARM Ampere A1) | 4 OCPU + 24 GB RAM まで（**今回使う**） |
| Compute (AMD VM.E2.1.Micro) | 2 インスタンスまで |
| Block Storage | 200 GB |
| Outbound データ転送 | 10 TB/月 |

---

## 3. VM（Compute Instance）作成

### 3-1. メニュー → Compute → Instances

1. 左上ハンバーガーメニュー → **Compute** → **Instances**
2. 上部の「**Create Instance**」ボタン

### 3-2. インスタンス設定

| 項目 | 設定値 |
|------|--------|
| Name | `hpb-blog-vm`（任意） |
| Compartment | デフォルト（root） |
| Placement | 任意の Availability Domain。**「Out of host capacity」** になったら他の AD で再試行 |
| Image | **「Edit」→「Change image」→ Canonical Ubuntu 24.04** |
| Shape | **「Edit」→「Change shape」→ Ampere → VM.Standard.A1.Flex** → 2 OCPU + 12 GB RAM に設定 |
| Networking | VCN: **新規作成** をそのまま採用（Auto-create new VCN） |
| Public IPv4 | **Assign a public IPv4 address** にチェック |
| SSH keys | **次節で説明**（Cloud Shell で生成） |
| Boot volume | デフォルト（50 GB） |

### 3-3. SSH キーを Cloud Shell で生成

PC にダウンロードせず、**ブラウザの Cloud Shell 内で SSH キーを生成・保管** します。

1. **画面右上 `>_` アイコン（Cloud Shell）** をクリック → ブラウザ内ターミナルが開く
2. Cloud Shell で以下を実行:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/hpb-blog -N ""
cat ~/.ssh/hpb-blog.pub
```

3. 表示された **公開鍵（ssh-ed25519 で始まる行）を全てコピー**
4. インスタンス作成画面に戻り、SSH キー欄で **「Paste public keys」** を選択 → 貼り付け
5. 「**Create**」ボタンでインスタンス作成開始

### 3-4. インスタンスが Running になるまで待つ（約 2 分）

ステータスが緑の「**Running**」になったら、画面の **Public IPv4 address** を控えます（例: `132.226.XX.XX`）。

---

## 4. Cloud Shell から VM に SSH 接続

Cloud Shell に戻り、控えた IP で接続:

```bash
ssh -i ~/.ssh/hpb-blog ubuntu@<Public_IPv4>
```

初回接続時は `yes` で続行。`ubuntu@hpb-blog-vm:~$` のようなプロンプトに変わったら成功。

---

## 5. ワンショット・インストール

VM 内のシェルで以下を貼り付けて Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/tanukichiyamaguchi/HPB-Blog/main/scripts/install_on_vps.sh | bash
```

これで以下が自動実行されます（合計5〜10分）:

| ステップ | 内容 |
|---------|------|
| 1/6 | Python 3.11 / git / Chromium 依存ライブラリ |
| 2/6 | リポジトリを `/opt/hpb-blog/` にクローン |
| 3/6 | venv + Python 依存ライブラリ |
| 4/6 | Playwright Chromium（ARM版）DL |
| 5/6 | `.env` テンプレート作成 |
| 6/6 | daily cron 自動登録（JST 22:15） |

`[SUCCESS] Installation complete.` で完了。

---

## 6. シークレット設定

```bash
nano /opt/hpb-blog/.env
```

エディタで各値を記入:

```
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
SALON_BOARD_ID=CE44570
SALON_BOARD_PASSWORD=...
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...  # 任意
```

保存: `Ctrl+O` → Enter、終了: `Ctrl+X`

### 各 API キーの取得（ブラウザで別タブを開く）

| サービス | URL |
|---------|------|
| Anthropic (Claude) | https://console.anthropic.com/ |
| Google Gemini | https://aistudio.google.com/apikey |
| Slack Webhook | https://api.slack.com/messaging/webhooks |

---

## 7. 動作確認（3段階）

### 7-1. AI 生成だけテスト（Salon Board 触らず）

```bash
cd /opt/hpb-blog
RUN_SALON_BOARD_POST=skip .venv/bin/python -m src.main
```

1〜2分後、`output/$(date +%Y-%m-%d)/` に各ファイルが生成されれば OK:

```bash
ls -la /opt/hpb-blog/output/$(date +%Y-%m-%d)/
cat /opt/hpb-blog/output/$(date +%Y-%m-%d)/blog.txt
```

### 7-2. Salon Board 下書き保存テスト

```bash
cd /opt/hpb-blog
RUN_SALON_BOARD_POST=draft .venv/bin/python -m src.main
```

完了後、サロンボードの **ブログ一覧 → 下書き** に新しい行があるか確認。

セレクタが当たらず途中で止まった場合、スクリーンショットを確認:

```bash
ls -lt /opt/hpb-blog/screenshots/ | head -20
```

スクリーンショットを Cloud Shell からダウンロード（ブラウザでファイル選択）して
報告してくれれば、セレクタを修正します。

### 7-3. Salon Board 予約投稿テスト

```bash
cd /opt/hpb-blog
RUN_SALON_BOARD_POST=schedule .venv/bin/python -m src.main
```

サロンボードで翌朝 **8:15 公開予約** として登録されていれば成功。

---

## 8. 本番運用開始

すでに cron が登録済みです:

```bash
crontab -l
# 15 13 * * * (= JST 22:15)
```

毎日 JST 22:15 に自動実行、翌朝 8:15 公開予約として 1 日 1 件投稿。
**ユーザの操作は不要**、365日自動運転です。

---

## 課金されないことの最終確認

Oracle Cloud にログイン後、**右上のお金マーク → Cost Analysis** で `$0.00` であることを確認できます。

万が一課金が発生する場合は、以下を確認:
- Compute Shape が `VM.Standard.A1.Flex` で OCPU ≤ 4 / RAM ≤ 24GB
- Block Volume 合計 ≤ 200GB
- 異なるリージョンに別リソースが起動していないか

念のため、**Budget Alert** を設定しておくと安心:
- 左メニュー → Budgets → Create budget
- Monthly threshold: ¥100（実際 $0 で稼働するので、何か誤って課金が始まったらすぐ気づける）

---

## トラブルシューティング

### 「Out of host capacity」で VM 作成できない

Tokyo region の Ampere A1 は無料ユーザに人気で、時間帯によりキャパシティ不足になります。
- 別の Availability Domain を試す（AD-1 → AD-2 → AD-3）
- 数時間〜1日後に再試行
- どうしても取れない場合は **VM.Standard.E2.1.Micro（AMD 1GB RAM）** で代替（無料枠あり）
  - 1GB RAM は Playwright Chromium にはギリギリ。動くが、スワップを使うため遅め

### Cloud Shell でファイルをダウンロード/アップロードしたい

Cloud Shell の右上 **「⋮」メニュー** → **「Download」/「Upload」** でファイル転送可能（ブラウザ内）。

### SSH 接続が切れる

長時間放置で切れる場合は、Cloud Shell でもう一度:
```bash
ssh -i ~/.ssh/hpb-blog ubuntu@<Public_IPv4>
```

### VM を再起動したい

Oracle Cloud Console → Instances → 該当インスタンス → **「Restart」** ボタン。

---

## 補足: 30日無操作の削除リスク軽減

Oracle Cloud は「**完全に使われていない**」リソースを削除することがあります。
今回の構成では:
- **cron が毎日動く**（22:15 に CPU/RAM/Network 使用）
- ログ書き込みでディスク使用

→ アイドルではないため、削除対象になりません。

念のため:
- 月1回 Oracle Cloud にログインしてリソース確認
- Cost Analysis を眺める

---

## まとめ

- セットアップ後は **完全自動**（ユーザ操作ゼロ）
- 月額 **¥0**
- 日本IP（東京リージョン）で Geo Block 回避
- PC への一切のインストール不要（全てブラウザの Oracle Cloud Console + Cloud Shell）
