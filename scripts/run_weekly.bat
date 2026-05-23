@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0\.."

echo ===============================================
echo HPB Blog Auto-Post: 週次バッチ実行（7日分予約）
echo ===============================================
echo.
echo  - 約 10〜15 分で 7 日分の生成と予約投稿が完了します。
echo  - 途中で PC をスリープ・電源OFFしないでください。
echo  - 終了するまでこのウィンドウを閉じないでください。
echo.

REM 1. Python check
where python > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。setup.bat を先に実行してください。
    pause
    exit /b 1
)

REM 2. Activate venv
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo [WARNING] 仮想環境が見つかりません。setup.bat を実行することを推奨します。
)

REM 3. Check .env
if not exist ".env" (
    echo [ERROR] .env ファイルが見つかりません。
    echo .env.example をコピーして .env を作成し、必要な値を記入してください。
    pause
    exit /b 1
)

REM 4. Run the weekly batch (env vars are read by main.py via python-dotenv)
set "RUN_SALON_BOARD_POST=weekly"
set "WEEKLY_BATCH_DAYS=7"
set "UPDATE_THEME_HISTORY=true"

python -m src.main
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ===============================================
if "%EXIT_CODE%"=="0" (
    echo [SUCCESS] 7日分の予約投稿が完了しました。
    echo Salon Board のブログ一覧で予約状況をご確認ください。
) else (
    echo [FAILED] エラーが発生しました^(exit code: %EXIT_CODE%^)。
    echo screenshots\ フォルダのスクリーンショットを確認してください。
    echo Slack を設定している場合は通知も届きます。
)
echo ===============================================
echo.
pause
endlocal
exit /b %EXIT_CODE%
