@echo off
REM scrape_main → report → htmlpush の順に実行。いずれか失敗で打ち切り。
REM 前提: リポジトリ直下で実行。scrape_main=Edge ドライバ、htmlpush=Git・docs/index.html 必要。
chcp 65001 >nul
setlocal

set LOGFILE=execute.log
set DATETIME=%date% %time:~0,8%

echo [%DATETIME%] 開始: scrape_main.py >> %LOGFILE%
set LOGFILE=execute.log
python scrape_main.py
if errorlevel 1 (
    set DATETIME=%date% %time:~0,8%
    echo [%DATETIME%] エラー: scrape_main.py 失敗 >> %LOGFILE%
    echo エラー: scrape_main.py が失敗しました。
    exit /b 1
)
set DATETIME=%date% %time:~0,8%
echo [%DATETIME%] 終了: scrape_main.py >> %LOGFILE%
echo.

echo [%DATETIME%] 開始: report_main.py >> %LOGFILE%
set LOGFILE=execute.log
python report_main.py
if errorlevel 1 (
    set DATETIME=%date% %time:~0,8%
    echo [%DATETIME%] エラー: report_main.py 失敗 >> %LOGFILE%
    echo エラー: report_main.py が失敗しました。
    exit /b 1
)
set DATETIME=%date% %time:~0,8%
echo [%DATETIME%] 終了: report_main.py >> %LOGFILE%
echo.

echo [%DATETIME%] 開始: htmlpush.py >> %LOGFILE%
set LOGFILE=run.log
python htmlpush.py
if errorlevel 1 (
    set DATETIME=%date% %time:~0,8%
    echo [%DATETIME%] エラー: htmlpush.py 失敗 >> %LOGFILE%
    echo エラー: htmlpush.py が失敗しました。
    exit /b 1
)
set DATETIME=%date% %time:~0,8%
echo [%DATETIME%] 終了: htmlpush.py >> %LOGFILE%
echo.
echo すべて完了
echo ========================================
exit /b 0
