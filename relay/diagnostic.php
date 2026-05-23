<?php
/**
 * Salon Board 接続診断スクリプト
 *
 * 用途:
 *   お名前.com レンタルサーバから salonboard.com に到達できるかを確認する。
 *   このファイルを katestagelash.jp 等にアップロードして、ブラウザで開けば
 *   診断結果が表示される。
 *
 * セキュリティ:
 *   このファイルは認証情報を扱わない。サーバ側の curl が salonboard.com
 *   と正常通信できるかを見るだけ。
 *
 * アップロード後の確認URL例:
 *   https://katestagelash.jp/diagnostic.php
 */

header('Content-Type: text/plain; charset=utf-8');

echo "=== Salon Board Connectivity Diagnostic ===\n";
echo "Time: " . date('c') . "\n";
echo "PHP version: " . PHP_VERSION . "\n";
echo "Server IP (this host): " . ($_SERVER['SERVER_ADDR'] ?? 'unknown') . "\n";
echo "Outbound test target: https://salonboard.com/login/\n";
echo "\n";

// curl is required for HTTPS outbound
if (!function_exists('curl_init')) {
    echo "❌ PHP curl extension not available. Cannot proceed.\n";
    exit;
}
echo "✅ PHP curl available (version: " . curl_version()['version'] . ")\n";
echo "\n";

// ----- Test 1: simple GET to salonboard.com/login/ -----
echo "--- Test 1: GET https://salonboard.com/login/ ---\n";
$ch = curl_init('https://salonboard.com/login/');
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
curl_setopt($ch, CURLOPT_TIMEOUT, 20);
curl_setopt($ch, CURLOPT_USERAGENT,
    'Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0');
curl_setopt($ch, CURLOPT_HTTPHEADER, [
    'Accept-Language: ja-JP,ja;q=0.9',
]);
curl_setopt($ch, CURLOPT_HEADER, true);
$start = microtime(true);
$response = curl_exec($ch);
$elapsed = microtime(true) - $start;
$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$err = curl_error($ch);
$header_size = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
curl_close($ch);

if ($err) {
    echo "❌ curl error: $err\n";
} else {
    $headers = substr($response, 0, $header_size);
    $body = substr($response, $header_size);
    echo "✅ HTTP $status, time={$elapsed}s, body_len=" . strlen($body) . "b\n";
    echo "First headers:\n";
    foreach (explode("\n", $headers) as $line) {
        if (preg_match('/^(HTTP|set-cookie|location|server|content-type):/i', trim($line))) {
            echo "  " . trim($line) . "\n";
        }
    }
    if (strpos($body, 'ログイン') !== false || strpos($body, 'idPasswordInputForm') !== false) {
        echo "  ✅ Body contains login form markup\n";
    } else {
        echo "  ⚠️  Body does NOT look like login form\n";
        echo "  Body preview: " . substr(htmlspecialchars($body), 0, 200) . "\n";
    }
}
echo "\n";

// ----- Test 2: POST attempt to /CNC/login/doLogin/ -----
echo "--- Test 2: POST https://salonboard.com/CNC/login/doLogin/ (with dummy creds) ---\n";
$ch = curl_init('https://salonboard.com/CNC/login/doLogin/');
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query([
    'userId' => 'connectivity_test',
    'password' => 'irrelevant',
]));
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
curl_setopt($ch, CURLOPT_TIMEOUT, 20);
curl_setopt($ch, CURLOPT_USERAGENT,
    'Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0');
curl_setopt($ch, CURLOPT_HTTPHEADER, [
    'Accept-Language: ja-JP,ja;q=0.9',
    'Origin: https://salonboard.com',
    'Referer: https://salonboard.com/login/',
    'Content-Type: application/x-www-form-urlencoded',
]);
$start = microtime(true);
$response = curl_exec($ch);
$elapsed = microtime(true) - $start;
$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$err = curl_error($ch);
curl_close($ch);

if ($err) {
    echo "❌ curl error: $err\n";
    echo "🚨 ATTENTION: This means even from お名前.com, POST to salonboard is blocked.\n";
} else if ($status == 0) {
    echo "❌ Timeout / no response from salonboard.com (status 0)\n";
    echo "🚨 ATTENTION: Same silent-drop issue exists even from お名前.com server.\n";
} else {
    echo "✅ HTTP $status, time={$elapsed}s, body_len=" . strlen($response) . "b\n";
    echo "🎉 SALON BOARD ACCEPTED POST FROM THIS SERVER!\n";
    echo "  (Body preview): " . substr(htmlspecialchars($response), 0, 300) . "\n";
}
echo "\n";

// ----- Test 3: Show server's outbound IP as seen by external services -----
echo "--- Test 3: This server's outbound IP (as seen by external services) ---\n";
$ch = curl_init('https://api.ipify.org');
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 10);
$ip = trim(curl_exec($ch) ?: '');
$err = curl_error($ch);
curl_close($ch);
echo "Outbound IP: " . ($ip ?: "(failed: $err)") . "\n";

$ch = curl_init('https://ipinfo.io/' . urlencode($ip) . '/json');
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 10);
$geo = curl_exec($ch);
curl_close($ch);
echo "Geo info: $geo\n";

echo "\n=== END ===\n";
