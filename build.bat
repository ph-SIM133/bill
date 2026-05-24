@echo off
echo === Starting Build Process (Portable Python ONEDIR Mode) ===

REM ==============================================================================
REM 【自動起点同期】実行場所をこのバッチファイルがある「現在のフォルダ」へ強制移動
REM ==============================================================================
cd /d "%~dp0"

REM --- 【堅牢化】ポータブルPythonの相対パスを定義 ---
set PYTHON_EXE=..\..\WPy64-31150\python-3.11.5.amd64\python.exe

REM 1. PyInstallerの確認と自動インストール
echo Checking PyInstaller in portable environment...
%PYTHON_EXE% -m pip install pyinstaller

REM ==============================================================================
REM constantsファイルからバージョン文字列を自動で引っこ抜く
REM ==============================================================================
echo Extracting version info from lib_constants.py...
set VER_RAW=unknown
for /f "tokens=2 delims==" %%a in ('findstr "CURRENT_VERSION" lib_constants.py') do set VER_RAW=%%a

REM ★防弾仕様：構文エラーを防ぐため、括弧を使わずにスペースとクォーテーションを剥ぎ取る
set VER_RAW=%VER_RAW: =%
set VER_RAW=%VER_RAW:"=%

set SYS_VERSION=%VER_RAW%
set RELEASE_DIR=BarcodeSystem_Release_v%SYS_VERSION%
echo === Success! Target Version detected as: [ v%SYS_VERSION% ] ===


REM 2. 過去のビルドキャッシュをクリーンアップ
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%RELEASE_DIR%" rmdir /s /q "%RELEASE_DIR%"

REM 3. 各プログラムのコンパイル（現場で爆速起動する --onedir 展開モード）
REM ★【堅牢化：改修】クリーン環境でのモジュール欠落クラッシュを防ぐためHidden Importを明示
echo [1/4] Building Barcode Monitor...
%PYTHON_EXE% -m PyInstaller --noconfirm --onedir --noconsole  ^
    --hidden-import="pystray._win32" ^
    --hidden-import="win32timezone" ^
    --hidden-import="PIL.PdfImagePlugin" ^
    --hidden-import="PIL.BmpImagePlugin" ^
    "barcode_monitor.py"

echo [2/4] Building DB Bulk Registration...
%PYTHON_EXE% -m PyInstaller --noconfirm --onedir ^
    --hidden-import="win32timezone" ^
    "db_bulk_register.py"

echo [3/4] Building Config Editor (GUI)...
%PYTHON_EXE% -m PyInstaller --noconfirm --onedir --noconsole ^
    --hidden-import="win32timezone" ^
    --hidden-import="PIL.PdfImagePlugin" ^
    --hidden-import="PIL.BmpImagePlugin" ^
    "config_editor_v2.py"

echo [4/4] Building Reissue Tool (GUI)...
%PYTHON_EXE% -m PyInstaller --noconfirm --onedir --noconsole ^
    --hidden-import="win32timezone" ^
    --hidden-import="PIL.PdfImagePlugin" ^
    --hidden-import="PIL.BmpImagePlugin" ^
    --hidden-import="babel.numbers" ^
    "reissue_tool_pro.py"

REM 4. 配布用フォルダ構造の作成（バージョン名自動連動）
echo Creating Release Folders for v%SYS_VERSION%...
mkdir "%RELEASE_DIR%\system_v%SYS_VERSION%"
mkdir "%RELEASE_DIR%\history"
mkdir "%RELEASE_DIR%\log"
mkdir "%RELEASE_DIR%\facility_csv"

REM 5. 各成果物のフォルダ中身を共通フォルダへ美しく集約（マージ）配置
echo Merging assets to Release system folder...
xcopy /e /y "dist\barcode_monitor\*" "%RELEASE_DIR%\system_v%SYS_VERSION%\"
xcopy /e /y "dist\db_bulk_register\*" "%RELEASE_DIR%\system_v%SYS_VERSION%\"
xcopy /e /y "dist\config_editor_v2\*" "%RELEASE_DIR%\system_v%SYS_VERSION%\"
xcopy /e /y "dist\reissue_tool_pro\*" "%RELEASE_DIR%\system_v%SYS_VERSION%\"

REM 設定ファイルのコピー
copy "barcode_setting.ini" "%RELEASE_DIR%\system_v%SYS_VERSION%\"

REM 6. 後片付け
rmdir /s /q build
rmdir /s /q dist
del /q *.spec

echo === Build Completed Successfully! ===
echo Output Folder: [ %RELEASE_DIR% ]
pause