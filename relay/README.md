# Salon Board リレースクリプト

GitHub Actions（米国 IP）から salonboard.com への POST が Akamai に
silent drop されるのを回避するため、お名前.com レンタルサーバ（Japan IP）
上で動作する HTTP 中継スクリプトです。

## 構成

```
[GitHub Actions (US)] → POST + 共有秘密鍵 → [お名前.com PHP relay (Japan IP)]
                                                  ↓
                                            [salonboard.com]
                                            (Japan IP からなので通過)
```

## デプロイ手順（初回のみ・所要 5 分）

### Step 1: ランダムな秘密鍵を生成

ブラウザの開発者ツール（F12 → Console）で以下を実行、出てきた文字列をコピー：

```javascript
crypto.randomUUID() + crypto.randomUUID()
```

または手元のターミナルで:

```bash
openssl rand -hex 32
```

例（**実際のものではなく必ず自分で生成すること**）:
```
a9f3c2b1d8e7f6a5c4b3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1
```

### Step 2: `relay.php` の中の `$RELAY_SECRET` を編集

`relay.php` の以下の行を見つけ、`REPLACE_ME_WITH_A_RANDOM_32CHAR_SECRET_BEFORE_UPLOAD`
の部分を Step 1 で生成した秘密鍵に置き換える:

```php
$RELAY_SECRET = 'REPLACE_ME_WITH_A_RANDOM_32CHAR_SECRET_BEFORE_UPLOAD';
                  ↓ 置換
$RELAY_SECRET = 'a9f3c2b1d8e7f6a5c4b3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1';
```

### Step 3: お名前.com ファイルマネージャーにアップロード

1. お名前.com Navi → レンタルサーバー → 「ログイン」ボタン
2. コントロールパネル → ファイルマネージャー
3. `public_html/<自動サブドメイン>.onamaeweb.jp/` フォルダを開く
4. 編集済みの `relay.php` をアップロード

### Step 4: 動作確認

ブラウザで以下にアクセス（**`<自動サブドメイン>` の部分は自分のものに**）:

```
https://<自動サブドメイン>.onamaeweb.jp/relay.php
```

正常応答（秘密鍵なしでアクセスしたので 403 になる）:
```json
{"ok":false,"error":"Forbidden"}
```

この応答が返れば、PHP スクリプトは動作しています ✅

### Step 5: GitHub Secrets に秘密鍵と URL を登録

GitHub リポジトリ → Settings → Secrets and variables → Actions → New repository secret

| Name | Value |
|------|-------|
| `RELAY_URL` | `https://<自動サブドメイン>.onamaeweb.jp/relay.php` |
| `RELAY_SECRET` | Step 1 で生成した秘密鍵（`$RELAY_SECRET` と同じ値）|

## セキュリティ

- 秘密鍵を知らないと使えない（403 を返す）
- 転送先は `*.salonboard.com` 以下のみ許可（他サイトには使えない）
- HTTPS のみ（http:// は弾く）
- cookie jar はサーバの `/tmp/` に保存、リクエスト終了後も残るが任意の他人には見えない

### 秘密鍵が漏れた疑いがあるとき

1. `relay.php` の `$RELAY_SECRET` を新しい値に変更してアップロードし直す
2. GitHub Secrets の `RELAY_SECRET` も同じ新しい値に更新

## トラブルシュート

### Forbidden が返らず、404 が返る場合

- ファイルマネージャーで `public_html/<サブドメイン>/relay.php` に置けていない
- ファイル名が `relay.php.txt` 等になっていないか確認

### Forbidden が返らず、500 が返る場合

- PHP 構文エラーの可能性
- ファイルマネージャーで `relay.php` を開いて編集タブで構文確認
- `$RELAY_SECRET` の値を変更しただけのつもりが、シングルクォートを壊していないか

### 動作はするがログインが失敗する

- このスクリプトは認証情報を保管しない
- 認証情報は GitHub Secrets の SALON_BOARD_ID / SALON_BOARD_PASSWORD 経由でリクエストごとに渡される
- ログ確認: お名前.com コントロールパネル → エラーログ

## API（開発者向け）

リクエスト形式（JSON）:

```json
{
  "method": "POST",
  "url": "https://salonboard.com/CNC/login/doLogin/",
  "session_id": "",
  "data": {"userId": "...", "password": "..."},
  "headers": {"Referer": "https://salonboard.com/login/"},
  "files": null,
  "timeout": 30,
  "follow_redirects": false
}
```

レスポンス:

```json
{
  "ok": true,
  "session_id": "abc123...",
  "status": 302,
  "final_url": "https://salonboard.com/CNC/login/doLogin/",
  "headers": "HTTP/2 302\r\nLocation: /KLP/...\r\n...",
  "body_base64": "...",
  "body_length": 0,
  "cookies": {"JSESSIONID": "...", "_abck": "..."},
  "elapsed_seconds": 1.234
}
```

ファイルアップロード時の `files` 形式:

```json
{
  "files": {
    "image": {
      "content": "<base64-encoded bytes>",
      "filename": "image.jpg",
      "mime": "image/jpeg"
    }
  }
}
```
