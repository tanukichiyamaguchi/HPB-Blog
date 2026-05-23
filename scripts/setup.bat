@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0\.."

echo ===============================================
echo HPB Blog Auto-Post: Initial Setup
echo ===============================================
echo.

REM 1. Python check
where python > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。
    echo Python 3.11 以上を https://www.python.org/downloads/ からインストールしてください。
    echo インストール時は "Add Python to PATH" にチェックを入れてください。
    pause
    exit /b 1
)
python --version

REM 2. Create venv
if not exist ".venv" (
    echo [INFO] 仮想環境を作成しています...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] 仮想環境の作成に失敗しました。
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

REM 3. Install dependencies
echo [INFO] 依存ライブラリをインストール中...
python -m pip install --upgrade pip > nul
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] 依存ライブラリのインストールに失敗しました。
    pause
    exit /b 1
)

REM 4. Install Playwright browser
echo [INFO] Playwright Chromium をインストール中（初回は数分かかります）...
python -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] Playwright ブラウザのインストールに失敗しました。
    pause
    exit /b 1
)

REM 5. Create .env if not exists
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" > nul
        echo [INFO] .env.example をコピーして .env を作成しました。
    )
)

echo.
echo ===============================================
echo [SUCCESS] セットアップ完了
echo ===============================================
echo.
echo 次の手順:
echo   1. .env ファイルを Notepad など で開き、以下の値を記入してください:
echo      - ANTHROPIC_API_KEY
echo      - GEMINI_API_KEY
echo      - SALON_BOARD_ID
echo      - SALON_BOARD_PASSWORD
echo      - SLACK_WEBHOOK_URL （任意）
echo.
echo   2. scripts\run_weekly.bat をダブルクリックすると 7日分の予約投稿が
echo      サロンボードに登録されます。
echo.
pause
endlocal
