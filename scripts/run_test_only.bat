@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0\.."

echo ===============================================
echo HPB Blog Auto-Post: テスト実行（AI生成のみ）
echo ===============================================
echo.
echo  - AI でテーマ・本文・画像を 1 件生成します。
echo  - サロンボードには投稿しません（安全に動作確認できます）。
echo  - 約 1〜2 分で完了します。
echo.

where python > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。setup.bat を先に実行してください。
    pause
    exit /b 1
)

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

if not exist ".env" (
    echo [ERROR] .env ファイルが見つかりません。
    pause
    exit /b 1
)

set "RUN_SALON_BOARD_POST=skip"

python -m src.main
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ===============================================
if "%EXIT_CODE%"=="0" (
    echo [SUCCESS] AI 生成が完了しました。
    echo output\YYYY-MM-DD\ の中身を確認してください:
    echo   - blog.txt    : 本文
    echo   - title.txt   : タイトル
    echo   - image.jpg   : アイキャッチ画像
    echo   - meta.json   : メタ情報
) else (
    echo [FAILED] エラーが発生しました^(exit code: %EXIT_CODE%^)。
)
echo ===============================================
echo.
pause
endlocal
exit /b %EXIT_CODE%
