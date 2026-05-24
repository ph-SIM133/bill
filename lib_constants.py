# -*- coding: cp932 -*-
import os
import sys
import datetime

CURRENT_VERSION = "3.0.1"

if getattr(sys, 'frozen', False):
    # ==============================================================================
    # 一時解凍先(_MEIPASS)への誤侵入を完全ブロック
    # ==============================================================================
    # PyInstaller環境下では、sys.executable(物理EXEの位置)の親ディレクトリを絶対的0地点とする
    LOCAL_DIR = os.path.normpath(os.path.dirname(sys.executable))
else:
    # 通常のPython実行時は、実行スクリプトの配置フォルダを起点とする
    LOCAL_DIR = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))

# 以降の設定ファイル(INI)や履歴ファイル(JSON)へのアクセスは、すべてこのLOCAL_DIRを起点として結合する

# ==============================================================================
# 2. 道先案内ファイル（root_path.txt）の自動パース
# ==============================================================================
ROOT_PATH_FILE = os.path.normpath(os.path.join(LOCAL_DIR, "root_path.txt"))
SHARED_BASE_DIR = LOCAL_DIR  # デフォルトはローカル（フォールバック用）
IS_NETWORK_MODE = False      # 画面表示用の状態フラグ

if os.path.exists(ROOT_PATH_FILE):
    _parsed_successfully = False
    # ユーザーがメモ帳でUTF-8保存してもエラーでクラッシュしないよう複数エンコードを試行
    for enc in ['cp932', 'utf-8-sig']:
        try:
            with open(ROOT_PATH_FILE, "r", encoding=enc) as f:
                for line in f:
                    # パスを "" や '' で囲んでしまった場合のパース異常（パスが見つからない）を完全防止
                    clean_line = line.strip().strip('\'"')
                    
                    # 空行や、# または ; で始まるコメント行はスキップ
                    if not clean_line or clean_line.startswith('#') or clean_line.startswith(';'):
                        continue
                    
                    target_path = os.path.normpath(clean_line)
                    target_ini = os.path.normpath(os.path.join(target_path, "barcode_setting.ini"))
                    
                    # ★指定された共有先に、本当にINIファイルが存在するか確認
                    if os.path.exists(target_ini):
                        SHARED_BASE_DIR = target_path
                        IS_NETWORK_MODE = True
                    else:
                        #  --noconsole 時の print() は IOError(Bad file descriptor) でシステムを即死させるため、安全に緊急ログファイルへ書き出す
                        msg = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 【警告】共有先({target_path})にINIがありません。ローカルモードで起動します。\n"
                        try:
                            if sys.stdout: sys.stdout.write(msg)
                        except: pass
                        try:
                            with open(os.path.join(LOCAL_DIR, "startup_fallback.log"), "a", encoding="cp932") as log_f:
                                log_f.write(msg)
                        except: pass
                        
                    _parsed_successfully = True
                    break  # 有効なパスを1行処理したら終了
                    
        except UnicodeDecodeError:
            continue # 次の文字コード（UTF-8等）へ切り替えて再挑戦
        except Exception as e:
            # なぜパースに失敗したのかを起動階層に物理ファイルとして刻印する
            try:
                import datetime
                with open(os.path.join(LOCAL_DIR, "startup_fallback.log"), "a", encoding="cp932") as log_f:
                    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    log_f.write(f"[{now_str}] root_path.txt解析エラー({enc}): {e}\n")
            except: pass
            
        if _parsed_successfully:
            break

# ==============================================================================
# 3. 定数の「集約（共有）」と「隔離（ローカル）」の自動マッピング
# ==============================================================================

# --- 共通の基盤位置の確定 ---
BASE_DIR            = SHARED_BASE_DIR  # システム全体の共通の0地点
INI_FILE            = os.path.normpath(os.path.join(SHARED_BASE_DIR, "barcode_setting.ini"))

# バッチ側を直したくないため、ロックファイル名をバッチの消去対象「process_running.lock」へ完全同期
LOCK_FILE_BASE = os.path.dirname(LOCAL_DIR) if getattr(sys, 'frozen', False) else LOCAL_DIR
LOCK_FILE      = os.path.normpath(os.path.join(LOCK_FILE_BASE, "process_running.lock"))

if IS_NETWORK_MODE:
    #  【ネットワークモード】
    # 共有データ（頭脳・記憶）は、一個上に漏れ出させず Bill フォルダの中に美しく密閉収容
    LOG_DIR     = os.path.normpath(os.path.join(BASE_DIR, "log"))
    HISTORY_DIR = os.path.normpath(os.path.join(BASE_DIR, "history"))
    
    # 作業エリア（隔離机）は、各PCの足元へ強制隔離
    WORK_DIR    = os.path.normpath(os.path.join(LOCAL_DIR, "..", "work_dir"))
    PDF_DIR     = os.path.normpath(os.path.join(LOCAL_DIR, "..", "pdf_output"))
else:
    #  【ローカルモード】
    # 既存のローカル稼働時の挙動・相対配置を1ミリも変えず、100%完全に無傷で維持
    LOG_DIR     = os.path.normpath(os.path.join(BASE_DIR, "..", "log"))
    HISTORY_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "history"))
    WORK_DIR    = os.path.normpath(os.path.join(BASE_DIR, "..", "work_dir"))
    PDF_DIR     = os.path.normpath(os.path.join(BASE_DIR, "..", "pdf_output"))

# 【DB一括スクリプト階層ズレ完全解消】
MASTER_HISTORY_FILE = os.path.normpath(os.path.join(HISTORY_DIR, "master_history.json"))


def get_editor_log_path() -> str:
    """設定ツール等のログファイル名を動的生成（集約ログフォルダ内へ）"""
    import datetime
    now_str = datetime.datetime.now().strftime('%Y%m')
    return os.path.normpath(os.path.join(LOG_DIR, f"editor_{now_str}.txt"))

def get_log_path():
    """その日のsystem_YYYYMMDD.txtの絶対パスを返す"""
    log_filename = f"system_{datetime.datetime.now().strftime('%Y%m%d')}.txt"
    return os.path.normpath(os.path.join(LOG_DIR, log_filename))
    
# Copyright (c) 2026 ph-SIM133
# All rights reserved.
# This software is for non-commercial use only.