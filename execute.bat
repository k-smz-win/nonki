@echo off
REM 目的: scrape → report → gitpush の順に実行。各処理が終了してから次を実行する。
REM 前提: このバッチはリポジトリ直下で実行すること。scrape.py は Edge ドライバ、gitpush.py は Git リポジトリと report.html を要求する。
REM 入力: なし（各スクリプトは定数・環境変数・既存ファイルを参照する）。
REM 例外: いずれかが errorlevel 1 を返したらそこで打ち切り、exit /b 1 で終了。後続の scrape/report/gitpush は実行しない。
REM なぜこの順: scrape で CSV → report で HTML → gitpush で GitHub Pages に公開、というデータの流れのため。
chcp 65001 >nul
setlocal

echo ========================================
echo 1/3 scrape.py
echo ========================================
python scrape.py
if errorlevel 1 (
    echo エラー: scrape.py が失敗しました。
    exit /b 1
)
echo.

echo ========================================
echo 2/3 report.py
echo ========================================
python report.py
if errorlevel 1 (
    echo エラー: report.py が失敗しました。
    exit /b 1
)
echo.

echo ========================================
echo 3/3 gitpush.py
echo ========================================
python gitpush.py
if errorlevel 1 (
    echo エラー: gitpush.py が失敗しました。
    exit /b 1
)
echo.

echo ========================================
echo すべて完了
echo ========================================
exit /b 0
