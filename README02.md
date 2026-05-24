### 実行ファイル（EXE化）のビルド (Build Instructions)
現場の事務端末など、Python環境がないPCで運用する場合は、同梱の `build.bat` を使用してスタンドアロンパッケージを自動生成できます。起動速度と安定性に優れる `--onedir`（フォルダ展開）モードを採用しています。

**【ビルドの前提条件】**
開発およびビルド環境をクリーンに保つため、**WinPython (Portable Python) 3.11系** の使用を前提としてバッチファイルが組まれています。
* 期待するPythonの相対パス: `..\..\WPy64-31150\python-3.11.5.amd64\python.exe`
* **注意:** クローンしたPCの環境に合わせて、必ず `build.bat` 内の `PYTHON_EXE` のパスをご自身のPython環境（または仮想環境）へ書き換えてから実行してください。
* 
**重要（ネットワーク制限について）:**
ビルド処理（build.bat の実行）には、PyInstaller等のパッケージを自動取得するためインターネット接続環境が必要です。手元の運用PCがホワイトリスト等による厳しいインターネット接続制限下にある場合、ダウンロードが遮断されてビルドに失敗する恐れがあります。
その場合は、予め外部のインターネットに接続できる開発用PC等でビルドを完了させ、生成された配布用フォルダをUSBメモリ等で運用PCへ移動（ポータブル配置）してください。

セキュリティ担保:
ビルドによって生成された成果物（各EXEファイル）は、起動後および常駐運用中にインターネットへの外部接続は一切行いません（完全なオフライン/院内LAN環境で安全に動作します）。
* 
```text
任意の親フォルダ/ (例: M: や USBメモリのルートなど)
 │
 ├─bill/                        ← 本リポジトリ（ソースコード一式）
 │  ├─barcode_monitor.py         # メイン監視サービス（常駐）
 │  ├─config_editor_v2.py        # GUI設定ツール
 │  ├─db_bulk_register.py        # 履歴一括登録ツール
 │  ├─reissue_tool_pro.py        # 履歴閲覧・再発行ツール
 │  ├─lib_barcode_drawer.py      # バーコード生成・描画ライブラリ
 │  ├─lib_barcode_parser.py      # NSIPS構文解析ライブラリ
 │  ├─lib_common.py              # 共通処理ライブラリ
 │  ├─lib_constants.py           # 共通定数・パス定義
 │  ├─barcode_setting.ini        # 設定ファイル
 │  └─build.bat                  # バッチファイル
 │
 └─WPy64-31150/                  ← 【自動ビルドの対象】ポータブルPython環境
     └─python-3.11.5.amd64/
         └─python.exe            # 参照されるPython本体
```
注意: 上記の位置関係（build.bat から見て ..\..\ にPython本体がある状態）を維持している限り、環境を汚さずポータブル環境のまま安全にビルドを完結できます。もし異なるパスにPythonを配置している場合は、環境に合わせて build.bat 内の PYTHON_EXE のパスを書き換えてから実行してください。


**【ビルド手順】**
1. `build.bat` を実行します。
2. `lib_constants.py` からバージョン情報が自動抽出され、PyInstallerによるコンパイルが開始されます。（※依存関係による実行時クラッシュを防ぐため、Pillowやwin32timezone等のHidden Importが自動付与されます）
3. 完了後、`BarcodeSystem_Release_vX.X.X` というフォルダが生成され、本番導入に必要なすべての実行ファイル（EXE）と設定ファイルが1つのフォルダにマージされます。このフォルダをそのまま現場のPCへ配置してください。

## ビルド後の成果物構成 (Release Package Structure)

`build.bat` の実行が成功すると、同一階層に本番配布用のパッケージフォルダ `BarcodeSystem_Release_v3.0.1` が自動生成されます。フォルダの内部構造は以下の通りです。
```text
BarcodeSystem_Release_v3.0.1/      # 現場の各端末へそのまま配布するフォルダ一式
 ├── system_v3.0.1/                # 実行バイナリ集約フォルダ
 │    ├── _internal/               # Pythonランタイムおよび依存ライブラリ群（隔離領域）
 │    ├── barcode_monitor.exe      # メイン監視サービス（タスクトレイ常駐）
 │    ├── config_editor_v2.exe     # GUI設定ツール
 │    ├── reissue_tool_pro.exe     # 履歴閲覧・再発行ツール
 │    ├── db_bulk_register.exe     # 履歴一括登録ツール
 │    └── barcode_setting.ini      # 各端末固有の設定ファイル
 │
 ├── facility_csv/                 # 【空フォルダ】施設・患者マスターCSVの配置エリア
 ├── history/                      # 【空フォルダ】過去の処方履歴（master_history.json）保存エリア
 ├── log/                          # 【空フォルダ】日別の稼働ログ（system_YYYYMMDD.txt）保存エリア
 ├── work_dir/                     # 【空フォルダ】システム内部処理用の一時作業領域
 └── pdf_output/                   # 【空フォルダ】印刷用PDFの一時出力バッファ領域
work_dir,pdf_outputはプログラム作成時に作成されます。
 ```
 運用のポイント:
実環境へ導入する際は、この BarcodeSystem_Release_v3.0.1 フォルダごと対象端末のローカルディスク（例: C:\ や M:\ 等）の任意の場所にコピーするだけで、レジストリを一切汚さずにポータブルに即時稼働可能です。
