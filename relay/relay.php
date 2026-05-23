<?php
declare(strict_types=1);

/**
 * Salon Board リレー - Japan IP 経由の HTTP 転送
 *
 * GitHub Actions（米国 IP）から salonboard.com への POST が Akamai に
 * silent drop されるため、Japan IP の お名前.com レンタルサーバ上に置く
 * 本スクリプトを経由させる。
 *
 * セキュリティ:
 *   - X-RELAY-SECRET ヘッダで認証
 *   - 転送先は salonboard.com 以下のみ許可
 *   - クッキー jar は /tmp/sb_relay_<セッション>.cookies に保存
 *
 * デプロイ:
 *   1. 下記の $RELAY_SECRET を 32文字以上のランダム文字列に変更
 *   2. お名前.com ファイルマネージャーで public_html/<サブドメイン>/ にアップロード
 *   3. アクセス URL: https://<サブドメイン>/relay.php
 *   4. 同じ $RELAY_SECRET を GitHub Secrets の RELAY_SECRET にも登録
 */

// ============================================================================
// 設定 (USER MUST EDIT)
// ============================================================================
// この値を 32 文字以上のランダム文字列に必ず変更してください。
// 例: openssl rand -hex 32 で生成
$RELAY_SECRET = 'REPLACE_ME_WITH_A_RANDOM_32CHAR_SECRET_BEFORE_UPLOAD';

// 転送先として許可するホスト
$ALLOWED_HOST_SUFFIX = 'salonboard.com';

// ============================================================================
// ヘルパー
// ============================================================================
function jsonOut(array $data, int $status = 200): void {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function logErr(string $msg): void {
    error_log("[sb-relay] $msg");
}

// ============================================================================
// 認証
// ============================================================================
$got_secret = $_SERVER['HTTP_X_RELAY_SECRET'] ?? '';
if (!hash_equals($RELAY_SECRET, (string)$got_secret)) {
    logErr('Bad secret (header: ' . substr((string)$got_secret, 0, 8) . '...)');
    jsonOut(['ok' => false, 'error' => 'Forbidden'], 403);
}

// ============================================================================
// リクエスト解析
// ============================================================================
$raw = file_get_contents('php://input');
$req = json_decode($raw, true);
if (!is_array($req)) {
    jsonOut(['ok' => false, 'error' => 'Body must be JSON object'], 400);
}

$method = strtoupper((string)($req['method'] ?? 'GET'));
$url = (string)($req['url'] ?? '');
$session_id = (string)($req['session_id'] ?? '');
$data = $req['data'] ?? null;
$req_headers = $req['headers'] ?? [];
$files = $req['files'] ?? null;
$timeout = (int)($req['timeout'] ?? 30);
$follow_redirects = (bool)($req['follow_redirects'] ?? false);

// URL バリデーション
$parts = parse_url($url);
if (
    !$parts
    || ($parts['scheme'] ?? '') !== 'https'
    || !preg_match('#(^|\.)' . preg_quote($ALLOWED_HOST_SUFFIX, '#') . '$#i', (string)($parts['host'] ?? ''))
) {
    jsonOut([
        'ok' => false,
        'error' => "URL must be https://*.{$ALLOWED_HOST_SUFFIX}/... (got: " . substr($url, 0, 80) . ")",
    ], 400);
}

// ============================================================================
// セッション cookie jar
// ============================================================================
if ($session_id === '') {
    $session_id = bin2hex(random_bytes(16));
}
$safe_session = preg_replace('/[^a-zA-Z0-9]/', '', $session_id);
if (strlen($safe_session) < 8) {
    jsonOut(['ok' => false, 'error' => 'Invalid session_id'], 400);
}
$jar = sys_get_temp_dir() . "/sb_relay_{$safe_session}.cookies";

// ============================================================================
// curl 実行
// ============================================================================
$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => $follow_redirects,
    CURLOPT_MAXREDIRS      => 5,
    CURLOPT_TIMEOUT        => max(5, min($timeout, 60)),
    CURLOPT_CONNECTTIMEOUT => 15,
    CURLOPT_HEADER         => true,
    CURLOPT_COOKIEJAR      => $jar,
    CURLOPT_COOKIEFILE     => $jar,
    CURLOPT_USERAGENT      => 'Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0',
    CURLOPT_SSL_VERIFYPEER => true,
    CURLOPT_SSL_VERIFYHOST => 2,
    CURLOPT_ENCODING       => '',  // accept all encodings, auto-decompress
]);

// カスタムヘッダ
$hdrs = [];
if (is_array($req_headers)) {
    foreach ($req_headers as $k => $v) {
        $hdrs[] = "$k: $v";
    }
}
if (!empty($hdrs)) {
    curl_setopt($ch, CURLOPT_HTTPHEADER, $hdrs);
}

// メソッド + body
$tmp_files = [];
if ($method === 'POST') {
    curl_setopt($ch, CURLOPT_POST, true);
    if ($files && is_array($files)) {
        // multipart/form-data with files
        $postfields = is_array($data) ? $data : [];
        foreach ($files as $field_name => $finfo) {
            if (!is_array($finfo) || !isset($finfo['content'])) continue;
            $tmp = tempnam(sys_get_temp_dir(), 'sb_relay_upload_');
            $tmp_files[] = $tmp;
            file_put_contents($tmp, base64_decode($finfo['content']));
            $postfields[$field_name] = new CURLFile(
                $tmp,
                (string)($finfo['mime'] ?? 'application/octet-stream'),
                (string)($finfo['filename'] ?? 'upload.bin')
            );
        }
        curl_setopt($ch, CURLOPT_POSTFIELDS, $postfields);
    } elseif (is_array($data)) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($data));
    } elseif (is_string($data)) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, $data);
    }
} elseif ($method !== 'GET') {
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    if (is_string($data)) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, $data);
    }
}

$start = microtime(true);
$resp = curl_exec($ch);
$elapsed = microtime(true) - $start;

$err = curl_error($ch);
$status_code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
$header_size = (int)curl_getinfo($ch, CURLINFO_HEADER_SIZE);
$final_url = (string)curl_getinfo($ch, CURLINFO_EFFECTIVE_URL);
curl_close($ch);

// upload 用の一時ファイル削除
foreach ($tmp_files as $tf) {
    @unlink($tf);
}

if ($resp === false || $err) {
    jsonOut([
        'ok' => false,
        'error' => "curl: " . substr($err, 0, 200),
        'session_id' => $session_id,
    ], 502);
}

$headers_text = (string)substr($resp, 0, $header_size);
$body = (string)substr($resp, $header_size);

// 現セッションの cookie 一覧を parse して返す
$cookies = [];
if (file_exists($jar)) {
    foreach (explode("\n", (string)file_get_contents($jar)) as $line) {
        if (strlen($line) === 0 || $line[0] === '#') continue;
        $cols = explode("\t", $line);
        if (count($cols) >= 7) {
            // [domain, flag, path, secure, expiration, name, value]
            $cookies[$cols[5]] = $cols[6];
        }
    }
}

jsonOut([
    'ok' => true,
    'session_id'     => $session_id,
    'status'         => $status_code,
    'final_url'      => $final_url,
    'headers'        => $headers_text,
    'body_base64'    => base64_encode($body),
    'body_length'    => strlen($body),
    'cookies'        => $cookies,
    'elapsed_seconds' => round($elapsed, 3),
]);
