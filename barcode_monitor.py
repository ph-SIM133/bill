
# -*- coding: cp932 -*-
#必ずshift-jisにて保存を！！

import os
import sys
import threading
import lib_constants as const
sys.path.insert(0, const.BASE_DIR)
import time
import datetime
import subprocess
import shutil
import configparser
import win32print
import win32ui
import json
import re
import msvcrt
from PIL import ImageWin
from PIL import Image, ImageDraw
import tkinter as tk
from tkinter import ttk
from typing import List, Dict, Any, Set, Optional
import pystray
from pystray import MenuItem as item
import lib_barcode_parser as parser
import lib_barcode_drawer as drawer
import lib_common as common
import logging
import traceback
import ctypes

# ---パス定義を外部ライブラリへ委譲し、重複を排除 ---
BASE_DIR = const.BASE_DIR
INI_FILE = const.INI_FILE
LOCK_FILE = const.LOCK_FILE
MASTER_HISTORY_FILE = const.MASTER_HISTORY_FILE
CURRENT_VERSION = const.CURRENT_VERSION

# --- 【常駐化】グローバル制御フラグとアイコンオブジェクト ---
is_running = True
tray_icon = None
status_win = None  # グローバルで安全に管理
is_gui_ready = False

# トレイメニューから「画面開いて！」と合図を送るためのフラグ
gui_open_requested = False 
gui_lock = threading.Lock() # ログ配列への同時アクセスを守る盾

# ★【新設】状態に応じたトレイアイコンの動的生成ロジック
def create_state_icon_image(is_online: bool) -> Image.Image:
    """Pillowを使って1Dバーコードの上に、オンライン(緑)/オフライン(赤)のランプを合成する"""
    # 土台となる白地のキャンバス (64x64)
    base_img = Image.new('RGB', (64, 64), color='white')
    d_canvas = ImageDraw.Draw(base_img)
    
    # 従来の1Dバーコード風の縦線をシャープに描画
    for x_line in range(8, 56, 4):
        d_canvas.line((x_line, 14, x_line, 50), fill='black', width=2)
        
    # 右下にステータス丸印をオーバーレイ
    # オンライン：緑色（丸枠） / オフライン：赤色（警告色）
    if is_online:
        # 緑色の丸
        d_canvas.ellipse((42, 42, 58, 58), fill='#22C55E', outline='#16A34A', width=1)
    else:
        # 赤色の丸
        d_canvas.ellipse((42, 42, 58, 58), fill='#EF4444', outline='#DC2626', width=1)
        # 簡易的な「！」マークを白線で中心に描画して視認性を強化
        d_canvas.line((50, 45, 50, 51), fill='white', width=2)
        d_canvas.point((50, 54), fill='white')
        
    return base_img

# トレイメニューから「画面開いて！」と合図を送るためのフラグ
gui_open_requested = False 
gui_lock = threading.Lock() # ログ配列への同時アクセスを守る盾

# グローバル例外フックの定義自体は外に置いてOK
def global_exception_hook(exctype, value, tb):
    """キャッチされなかったすべての致命的エラーを消滅直前にログへ強制書き込みする"""
    err_msg = "".join(traceback.format_exception(exctype, value, tb))
    logging.critical(f"[GLOBAL UNCAUGHT EXCEPTION]\n{err_msg}")
    sys.__excepthook__(exctype, value, tb)


def startup_cleanup(paths: Dict[str, str], conf: Dict[str, Any]):
    """システム起動時に実行する環境整備"""
    
    # 1. PDFフォルダの全クリア
    pdf_dir = paths['pdf']
    if os.path.exists(pdf_dir):
        for f in os.listdir(pdf_dir):
            file_path = os.path.join(pdf_dir, f)
            try:
                # フォルダ誤削除防止のため、ファイルであることを確認
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except OSError as e:
                # 削除失敗の具体的な理由をログに残す
                # 例：ファイルが別アプリ（PDFビューア等）で開かれている場合はここを通る
                logging.warning(f"PDFファイルの削除に失敗しました: {f} - {e}")
    
    # 2. work_dir/staging のクリーンアップ（journal以外）
    stage_dir = os.path.join(paths['work'], "staging")
    if os.path.exists(stage_dir):
        today_str = datetime.datetime.now().strftime('%Y%m%d')
        for item in os.listdir(stage_dir):
            item_path = os.path.join(stage_dir, item)
            # journalフォルダは停電リカバリ用なので絶対削除しない（保護）
            if os.path.isdir(item_path) and item == "journal":
                continue
            # ファイルで、かつ今日の日付を含まないものは古い残骸とみなす
            if os.path.isfile(item_path) and today_str not in item:
                try:
                    os.remove(item_path)
                except OSError as e:
                    logging.warning(f"stagingのクリーンアップ失敗: {item_path} - {e}")

    # 3. ログのパージ
    retention = conf.get('LogRetentionDays', 365) # デフォルト365日
    common.purge_old_files(paths['log'], retention, "system_*.txt")


def initialize_system() -> None:
    """
    ★【防弾隔離壁】環境を変化させる初期化処理を一括内包。
    他のツールからimportされた時はこの中身が一切実行されないため、漏れ出しを完全根絶する。
    """
    today_log_name = f"system_{datetime.datetime.now().strftime('%Y%m%d')}.txt"
    log_path = os.path.join(const.LOG_DIR, today_log_name)
    
    
    #ロギング初期化（FileHandlerをこのタイミングで安全に確立）
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_path, encoding='cp932', errors='replace'),
            logging.StreamHandler()
        ]
    )
    
    # 1 隔離フォルダ群（常にPCローカル足元の同じ場所を安全に指す）
    for ld in [const.WORK_DIR, const.PDF_DIR]:
        try:
            if not os.path.exists(ld):
                os.makedirs(ld)
        except Exception as e:
            logging.error(f"隔離フォルダ群の生成に失敗しました ({ld}): {e}", exc_info=True)

    # 2 記録フォルダ群（ローカル時は足元、ネットワーク時はNAS内）
    for sd in [const.LOG_DIR, const.HISTORY_DIR]:
        try:
            if sd.startswith('\\\\') and not os.path.exists(os.path.dirname(sd)):
                continue
            if not os.path.exists(sd):
                os.makedirs(sd, exist_ok=True)
        except Exception as e:
            # 起動は続行するが、何が原因でフォルダ生成に失敗したかをフライトレコーダーに記録
            logging.warning(f"記録フォルダ群の先行生成に失敗しました (生成対象パス: {sd}): {e}", exc_info=True)

    

    # 3 例外フックの有効化
    sys.excepthook = global_exception_hook
    threading.excepthook = lambda args: global_exception_hook(args.exc_type, args.exc_value, args.exc_traceback)

    
    
    
def maintenance_task(target_days: int, log_dir: str) -> None:
    """親階層の履歴ファイルから期限切れデータを削除"""
    if not os.path.exists(MASTER_HISTORY_FILE):
        return
        
    common.write_log(log_dir, "【保守】履歴ファイルの自己補修メンテナンスを実行します...")
    for retry in range(3): # メンテナンス時もリトライ    
        try:
            # 'r+'モードで開き、読み書きを一括ロックで完結
            with open(MASTER_HISTORY_FILE, 'r+', encoding='utf-8') as f:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 0x7FFFFFFF)
                try:
                    history = json.load(f)
                    initial_len = len(history)
                    
                    # --- 日付判定ロジック ---
                    days_int = int(target_days)
                    limit_date_dt = datetime.datetime.now() - datetime.timedelta(days=days_int)
                    limit_date = limit_date_dt.strftime('%Y%m%d')
                    
                    new_history = []
                    old_count = 0
                    invalid_count = 0
                    
                    for fn in history:
                        name_only, _ = os.path.splitext(fn)
                        if len(name_only) == 21:
                            if name_only[0].upper() in ['A', 'U']:
                                file_date = name_only[3:11]
                                if file_date.isdigit():
                                    if file_date >= limit_date:
                                        new_history.append(fn)
                                    else:
                                        old_count += 1
                                else:
                                    invalid_count += 1
                            else:
                                invalid_count += 1
                        else:
                            invalid_count += 1
                        pass
                    if len(new_history) != initial_len:
                        f.seek(0)
                        json.dump(new_history, f, indent=4)
                        f.truncate()
                        common.write_log(log_dir, f"【保守】履歴更新: 削除={old_count+invalid_count}, 残存={len(new_history)}")
                    else:
                        common.write_log(log_dir, "【保守】削除対象の履歴はありませんでした。")
                    break # ここまで無事に終わればリトライループを抜ける（成功）
                finally:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 0x7FFFFFFF)
        except (PermissionError, BlockingIOError):
            if retry < 2:
                time.sleep(2.0)
                continue
            common.write_log(log_dir, "! メンテナンス失敗: 履歴ファイルがロックされています")
            logging.error("履歴ファイルの自己補修メンテナンス中にロック競合が発生し、制限回数を超えました。", exc_info=True)
        except Exception as e:
            common.write_log(log_dir, f"! メンテナンスエラー: {e}")
            logging.error(f"履歴ファイルの自己補修メンテナンス中に予期せぬ例外が発生しました: {e}", exc_info=True)
            break # ロック以外の致命的なエラーはリトライせず終了

def is_pid_alive(pid: int) -> bool:
    """Windowsのタスクマネージャーに、指定されたPIDが本当に存在するか確認する"""
    try:
        # Windows標準のtasklistコマンドを使い、PIDを指定して生存を狙い撃ち確認
        output = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True, text=True, encoding='cp932')
        return str(pid) in output
    except Exception as e:
        # 現場の特殊なセキュリティ環境でコマンドが弾かれた場合、理由をログと画面に即座に報告
        print(f"[WARNING] Windowsタスクリストの取得に失敗しました(多重起動チェックを安全側に倒します): {e}")
        logging.warning(f"Windowsタスクリストの取得に失敗しました（PID: {pid} の生存確認不可）: {e}")
        # 安全側に倒すため、生存確認に失敗した場合は「生きている（True）」とみなして多重起動をガッチリ阻止する
        return True


def fast_monitor_loop() -> None:
    """★【シングルループ・完全防弾】監視メインループ（ネットワーク全断・自動復旧完全合流版）"""
    global is_running
 
    try:
        with open(LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logging.warning(f"ロックファイルへのPID書き込みに失敗しました(稼働自体は続行します): {e}", exc_info=True)

    # 状態の「変化」を捉えるための前状態記録変数
    last_network_status = True
    is_network_online = True 
    needs_reinit = True
    last_maint_day = datetime.datetime.now().day
    master_history = []
    last_history_mtime = 0
    last_index_mtime = 0
    size_warning_done = False

    paths = {
        'log': const.LOG_DIR,
        'work': const.WORK_DIR,
        'pdf': const.PDF_DIR
    }
    stage_dir = os.path.normpath(os.path.join(paths['work'], "nsips_staging"))

    # ★二重ループを廃止し、フラットな一本のループ構造に集約
    while is_running:
        try:
            conf = common.load_full_config()
            watch_d = conf.get('watch_dir', '')
            index_d = conf.get('index_dir', '')
            
            if not watch_d or not index_d:
                print("致命的エラー: INIファイルに WatchDir または IndexDir が設定されていません。")
                time.sleep(10)
                continue

            # ①【死活監視】ネットワーク切断時のエスケープ・不貞寝モード
            if watch_d.startswith('\\\\') and not os.path.exists(watch_d):
                is_network_online = False
                
                # 【新設】オンライン ? オフラインに切り替わった瞬間だけ通知＆アイコン赤変
                if last_network_status == True:
                    logging.warning("ネットワーク切断を検知。待機モードへ移行します。")
                    update_status_log("【警告】ネットワーク切断。再接続を待機中...")
                    
                    if tray_icon:
                        tray_icon.icon = create_state_icon_image(is_online=False)
                        tray_icon.update_menu()
                        # Windowsトレイのバルーン通知をキック
                        tray_icon.notify(
                            "レセコン共有フォルダとの通信が切断されました。\nシステムを一時待機モードに移行します。",
                            title="【警告】ネットワーク切断"
                        )
                    last_network_status = False

                try: subprocess.run(f'net use "{watch_d}" >nul 2>&1', shell=True, timeout=5)
                except: pass
                time.sleep(10) 
                continue
            
            # ②【環境自動復旧】切断状態からの復帰、または初回起動時の初期化
            if not is_network_online or needs_reinit:
                is_network_online = True
                
                # 【新設】オフライン ? オンラインに復帰した瞬間だけ通知＆アイコン緑復帰
                if last_network_status == False or needs_reinit:
                    logging.info("ネットワーク接続を確認。システム監視を準備します。")
                    update_status_log("ネットワーク接続復旧。通常監視を開始します。")
                    
                    if tray_icon:
                        tray_icon.icon = create_state_icon_image(is_online=True)
                        tray_icon.update_menu()
                        if not needs_reinit: # 初回起動時以外の、"復帰"の時だけバルーンを出す
                            tray_icon.notify(
                                "レセコンとのネットワーク接続が復旧しました。\n自動発行監視を再開します。",
                                title="【復旧】オンライン接続完了"
                            )
                    last_network_status = True

                startup_cleanup(paths, conf)
                needs_reinit = False
                
                for sd in [const.LOG_DIR, const.HISTORY_DIR, stage_dir]:
                    os.makedirs(sd, exist_ok=True)
                
                # 前回異常終了時のリカバリ
                print("＝＝フォルダの掃除機能を開始します＝＝")
                recovery_count = 0
                if os.path.exists(watch_d):
                    for f_name in os.listdir(watch_d):
                        if f_name.startswith("processing_"):
                            old_path = os.path.join(watch_d, f_name)
                            new_name = f_name.replace("processing_", "")
                            new_path = os.path.join(watch_d, new_name)
                            try:
                                if os.path.exists(new_path):
                                    os.remove(old_path)
                                else:
                                    os.rename(old_path, new_path)
                                    common.write_log(paths['log'], f"【リカバリ】未処理ファイルを復旧: {new_name}")
                                    recovery_count += 1
                            except Exception as ex:
                                common.write_log(paths['log'], f"!! 自動リカバリ失敗({f_name}): {ex}")
                
                if recovery_count > 0:
                    common.write_log(paths['log'], f"＝＝掃除機能完了（{recovery_count}件復旧）＝＝")
                else:
                    print("＝＝掃除機能完了（対象なし）＝＝")
                
                if conf.get('maint_time', '0') == '0':
                    maintenance_task(conf['target_days'], paths['log'])
                    time.sleep(1)
                
                sys_name = conf.get('systemname', '領収明細票（薬局控）')
                logging.info(f"--- {sys_name} 監視稼働開始 (バージョン: {CURRENT_VERSION}) ---")
                common.write_log(paths['log'], f"--- システム常駐監視サイクル開始 ---")

            # ③【通常スケジュール保守判定】
            now = datetime.datetime.now()
            if conf.get('maint_time', '0') != '0' and now.day != last_maint_day:
                if now.strftime('%H%M') >= conf['maint_time']:
                    maintenance_task(conf['target_days'], paths['log'])
                    last_maint_day = now.day

            # ④【ファイル監視・本処理セクション】
            has_written_history = False
            should_reload = False
            load_success = True
            
            if not os.path.exists(MASTER_HISTORY_FILE):
                master_history = []
            else:
                current_size = os.path.getsize(MASTER_HISTORY_FILE)
                if current_size > 512000 and not size_warning_done:
                    common.write_log(paths['log'], f"!! 警告：履歴ファイルが500KBを超えています({current_size//1024}KB)。")
                    size_warning_done = True
                current_mtime = os.path.getmtime(MASTER_HISTORY_FILE)
                if current_mtime != last_history_mtime:
                    should_reload = True
                    load_success = False

            if should_reload:
                for retry in range(3):
                    try:
                        with open(MASTER_HISTORY_FILE, 'r+', encoding='utf-8') as f:
                            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 0x7FFFFFFF)
                            try:                        
                                master_history = json.load(f)
                                load_success = True
                                break
                            finally:
                                f.seek(0)
                                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 0x7FFFFFFF)
                    except (PermissionError, BlockingIOError) as e:
                        if retry < 2:
                            time.sleep(1.0)
                            common.write_log(paths['log'], f"!! 警告：履歴ファイル読込競合(リトライ {retry+1}/3)...")
                            continue
                        common.write_log(paths['log'], f"!! 致命的：履歴ロック解除不能。3秒待機...")
                        time.sleep(3)
                        break
                    except json.JSONDecodeError as e:
                        bak_file = MASTER_HISTORY_FILE + ".bak"
                        if os.path.exists(bak_file):
                            common.write_log(paths['log'], "!! 履歴破損検知：バックアップから安全に同期修復します...")
                            try:
                                with open(bak_file, 'r', encoding='utf-8') as f_bak:
                                    bak_data = json.load(f_bak)
                                f.seek(0)
                                json.dump(bak_data, f, indent=4)
                                f.truncate()
                                f.flush()
                                master_history = bak_data
                                common.write_log(paths['log'], "【復旧成功】破損履歴の自己修復に成功しました。")
                                load_success = True
                                break
                            except Exception as bak_e: 
                                common.write_log(paths['log'], f"!! 致命的：バックアップ修復失敗: {bak_e}")
                                logging.critical(f"MASTER_HISTORYのバックアップ修復プロセスで致命的崩壊: {bak_e}", exc_info=True)
                                
                        corrupt_bk = f"{MASTER_HISTORY_FILE}.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.corrupt"
                        try: 
                            shutil.move(MASTER_HISTORY_FILE, corrupt_bk)
                            common.write_log(paths['log'], f"!! 履歴破損のためファイルを隔離しました: {corrupt_bk}")
                        except Exception as move_e:
                            # 隔離の失敗（ファイルロック居座り）を絶対に見逃さない
                            logging.error(f"破損履歴ファイルの隔離移動（退避）に失敗しました: {move_e}", exc_info=True)
                        master_history = []
                        load_success = True
                        break

            if not load_success:
                time.sleep(3)
                continue
                
            try:
                current_index_mtime = os.path.getmtime(conf['index_dir'])
                if current_index_mtime == last_index_mtime:
                    time.sleep(1.5)
                    continue
                last_index_mtime = current_index_mtime
                index_files = os.listdir(conf['index_dir'])
            except Exception as e:
                common.write_log(paths['log'], f"!! 索引フォルダアクセスエラー: {e}")
                logging.error(f"索引フォルダへのMtime取得・リスト展開に失敗: {e}", exc_info=True)
                time.sleep(5)
                continue
            
            new_found = False
            today_limit = (now - datetime.timedelta(days=conf['target_days'])).strftime('%Y%m%d')
            facility_ids = parser.get_facility_patient_ids(conf['facility_dir'], conf['csv_col'])
            
            for fname in index_files:
                name_only, ext = os.path.splitext(fname)
                if ext.upper() in ['.TXT', '.DAT'] and len(name_only) == 21:
                    if name_only[0].upper() in ['A', 'U']:
                        file_date = name_only[3:11]
                        if file_date >= today_limit:
                            if fname not in master_history:
                                watch_path = os.path.normpath(os.path.join(conf['watch_dir'], fname))
                                if os.path.exists(watch_path):
                                    stage_path = os.path.normpath(os.path.join(stage_dir, fname))
                                    tmp_proc = os.path.normpath(os.path.join(paths['work'], "proc_" + fname))
                                    
                                    try:
                                        shutil.copy2(watch_path, stage_path)
                                        shutil.copy2(stage_path, tmp_proc)
                                        
                                        res = parser.process_nsips(tmp_proc, conf['watch_dir'], facility_ids, False, conf)
                                        
                                        if res == "SKIP_ZERO_RECEIPT":
                                            msg_zero = f"【スキップ】領収額0円: {fname}"
                                            common.write_log(paths['log'], msg_zero)
                                            update_status_log(msg_zero)
                                            master_history.append(fname)
                                            new_found = True
                                        elif isinstance(res, dict):
                                            p_name_log = res.get('patient_name', '不明')
                                            msg_ok = f">> 発行成功: {p_name_log} 様の明細票"
                                            
                                            common.write_log(paths['log'], f">> 発行: {fname} ({p_name_log}様)")
                                            update_status_log(msg_ok)
                                            
                                            raw_ini = configparser.ConfigParser(interpolation=None)
                                            raw_ini.optionxform = str
                                            raw_ini.read(INI_FILE, encoding='cp932')
                                            
                                            conf['paper_size'] = raw_ini.get('Printer', 'PaperSize', fallback='A6')
                                            conf['barcode_mode'] = raw_ini.get('Printer', 'BarcodeMode', fallback='1D')
                                            
                                            target_sec = "Layout_80mm" if conf['paper_size'] == "80mm" else "Layout_A6"
                                            conf['layout'] = dict(raw_ini[target_sec]) if target_sec in raw_ini else {}
                                            
                                            drawer.generate_pdf_logic(res, conf, paths['work'], paths['pdf'], common.print_image_directly)
                                            master_history.append(fname)
                                            new_found = True
                                        elif res in ["SKIP_AMOUNT", "SKIP_FACILITY"]:
                                            if res == "SKIP_AMOUNT":
                                                common.write_log(paths['log'], f"【キャンセル】金額変動なし(重複抑制): {fname}")
                                            master_history.append(fname)
                                            new_found = True
                                            
                                        if os.path.exists(stage_path):
                                            try: os.remove(stage_path)
                                            except Exception as rm_se: logging.warning(f"ステージングファイル削除失敗: {rm_se}", exc_info=True)
                                            
                                    except Exception as e:
                                        common.write_log(paths['log'], f"! 処理エラー({fname}): {e}")
                                        logging.error(f"ファイル解析・PDF生成フロー内で例外検知({fname}): {e}", exc_info=True)
                                    finally:
                                        if os.path.exists(tmp_proc):
                                            try:
                                                os.remove(tmp_proc)
                                            except Exception as rm_pe:
                                                # finally内の二次クラッシュを防ぎつつログに残す
                                                logging.error(f"一時ファイル proc_ の削除に失敗しました（ロックの解放漏れ可能性有り）: {rm_pe}", exc_info=True)

            if new_found:
                try: 
                    for retry in range(3):
                        try:
                            with open(MASTER_HISTORY_FILE, 'r+', encoding='utf-8') as f:
                                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 0x7FFFFFFF)
                                try:
                                    f.seek(0)
                                    json.dump(master_history, f, indent=4)
                                    f.truncate()
                                    f.flush()   
                                    os.fsync(f.fileno()) 
                                finally:
                                    f.seek(0)
                                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 0x7FFFFFFF)    
                            
                            # 【ノーガードパージ】ロックを抜けた直後のバックアップ生成も安全に保護
                            try:
                                shutil.copy2(MASTER_HISTORY_FILE, MASTER_HISTORY_FILE + ".bak")
                            except Exception as bak_copy_e:
                                logging.error(f"履歴バックアップ(.bak)の自動更新に失敗しました: {bak_copy_e}", exc_info=True)

                            last_history_mtime = os.path.getmtime(MASTER_HISTORY_FILE)
                            has_written_history = True
                            break
                        except (PermissionError, BlockingIOError):
                            if retry < 2:
                                time.sleep(1.5) 
                                continue
                            raise 
                except Exception as e:
                    common.write_log(common.load_full_config().get('log_dir', const.LOG_DIR), f"! 履歴保存失敗: {e}")
                    logging.error(f"履歴ファイルへのJSON確定上書きプロセスで致命的失敗: {e}", exc_info=True)
                    
            sleep_time = 2.5 if has_written_history else 1.5
            time.sleep(sleep_time)
            
        except Exception as loop_e:
            #ここでメインループの大物例外をキャッチし、詳細なスタックトレースを出力！
            logging.error("監視メインループ内で予期せぬ致命的エラーが発生しました。システム維持のため5秒後に自動再試行します。", exc_info=True)
            time.sleep(5)
            
    # whileループの外側。常駐終了時にロックファイルを安全に消し去る
    if os.path.exists(LOCK_FILE):
        try: 
            os.remove(LOCK_FILE)
        except Exception as lock_rm_e:
            logging.warning(f"終了時のロックファイル削除に失敗しました(実害はありません): {lock_rm_e}", exc_info=True)

# ==============================================================================
# 【タスクトレイ常駐化コントロール】UI連携・スレッド制御ロジック
# ==============================================================================
# --- GUI小窓用のメッセージ・時間保持変数 ---
last_status_time = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
status_log_lines = [
    "システムを起動しました。ファイルの出現を監視しています...",
    "", "", "", "" # 空行で埋めておく
]

def update_status_log(new_text: str) -> None:
    global last_status_time, status_log_lines
    with gui_lock: # サブスレッドからの同時書き込みを完全に保護
        last_status_time = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        status_log_lines.append(new_text)
        if len(status_log_lines) > 5:
            status_log_lines.pop(0)

def process_gui_requests():
    """
    メインスレッド側（トレイのループ内）から500ms周期で呼ばれ、
    画面の表示要求（deiconify）を安全に処理する監視塔
    """
    global gui_open_requested, status_win
    if gui_open_requested:
        gui_open_requested = False
        if status_win and status_win.winfo_exists():
            status_win.deiconify()
            status_win.attributes("-topmost", True)
            # 1.5秒後に最前面を解除して通常窓にする
            status_win.after(1500, lambda: status_win.attributes("-topmost", False))
            
    if status_win and status_win.winfo_exists():
        status_win.after(500, process_gui_requests)

def create_status_window_stub():
    """システム起動時、メインスレッド側で『あらかじめ1個だけ窓を作って隠しておく』関数"""
    global status_win, log_labels, lbl_time, is_gui_ready
    
    # --- 不要なStatusWindow()の残骸を削除し、直接Tkインスタンスを生成 ---
    status_win = tk.Tk()
    status_win.title("稼働ステータス")
    
    win_w, win_h = 480, 280
    try:
        scr_w = status_win.winfo_screenwidth()
        scr_h = status_win.winfo_screenheight()
        status_win.geometry(f"{win_w}x{win_h}+{scr_w - win_w - 20}+{scr_h - win_h - 60}")
    except:
        status_win.geometry(f"{win_w}x{win_h}")
        
    status_win.resizable(False, False)
    
    frame = ttk.Frame(status_win, padding=15)
    frame.pack(fill=tk.BOTH, expand=True)
    
    tk.Label(frame, text="■ システム稼働状況 （正常動作中）", font=("", 11, "bold"), fg="darkgreen").pack(anchor=tk.W, pady=(0,5))
    lbl_time = tk.Label(frame, text=f"最終同期日時: {last_status_time}", font=("", 10), fg="gray")
    lbl_time.pack(anchor=tk.W, pady=(0,5))
    
    log_frame = tk.LabelFrame(frame, text=" 直近の出来事・print報告 ", font=("", 9))
    log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
    
    log_labels = []
    for i in range(5):
        lbl = tk.Label(log_frame, text=status_log_lines[i], font=("MS Gothic", 10), anchor=tk.W, justify=tk.LEFT)
        lbl.pack(fill=tk.X, padx=8, pady=2)
        log_labels.append(lbl)
        
    # UIの土台が完成したので、準備完了フラグを立てる
    is_gui_ready = True
        
    def refresh_status():
        if status_win.winfo_exists() and status_win.wm_state() == "normal":
            try:
                lbl_time.config(text=f"最終同期日時: {last_status_time}")
                with gui_lock: # スレッド間の競合を防ぐ
                    for j in range(5):
                        log_labels[j].config(text=status_log_lines[j])
            except:
                pass
        if status_win.winfo_exists():
            status_win.after(500, refresh_status)
                
    refresh_status()
    
    # ×ボタンが押されたら消さずに「隠す」だけに倒す防弾仕様
    status_win.protocol("WM_DELETE_WINDOW", lambda: status_win.withdraw())
    status_win.withdraw() # 起動時は隠しておく
    
    # 外部からの deiconify 要求の受付を開始
    process_gui_requests()
    
    # 【防弾・フリーズ即死完全回避】メインスレッドをここで維持し、Tkinterのキューを常時回転させる
    status_win.mainloop()
    
# ==============================================================================
# 【タスクトレイ常駐化コントロール】UI連携・スレッド制御ロジック
# ==============================================================================

def open_config_tool():
    """右クリックメニュー：設定ツールを別プロセスで安全にキック（環境不一致の完全解消）"""
    try:
        if getattr(sys, 'frozen', False):
            # PyInstaller環境（ONEDIRモード）: 同じフォルダ内の「config_editor_v2.exe」を直接キック
            exe_path = os.path.normpath(os.path.join(const.BASE_DIR, "..", "config_editor_v2", "config_editor_v2.exe"))
            if not os.path.exists(exe_path): # フォールバック（同階層にある場合）
                exe_path = os.path.normpath(os.path.join(const.BASE_DIR, "config_editor_v2.exe"))
            subprocess.Popen([exe_path], shell=False, cwd=os.path.dirname(exe_path))
        else:
            # 開発（Python）環境: 従来のスクリプト起動
            script_path = os.path.join(const.BASE_DIR, "config_editor_v2.py")
            subprocess.Popen([sys.executable, script_path], shell=False, cwd=const.BASE_DIR)
    except Exception as e:
        logging.error(f"設定ツールのプロセス起動に失敗しました (CWD: {const.BASE_DIR}): {e}", exc_info=True)

def open_reissue_tool():
    """右クリックメニュー：履歴閲覧・再発行ツールを別プロセスで安全にキック（環境不一致の完全解消）"""
    try:
        if getattr(sys, 'frozen', False):
            # PyInstaller環境（ONEDIRモード）: 同じフォルダ内の「reissue_tool_pro.exe」を直接キック
            exe_path = os.path.normpath(os.path.join(const.BASE_DIR, "..", "reissue_tool_pro", "reissue_tool_pro.exe"))
            if not os.path.exists(exe_path): # フォールバック
                exe_path = os.path.normpath(os.path.join(const.BASE_DIR, "reissue_tool_pro.exe"))
            subprocess.Popen([exe_path], shell=False, cwd=os.path.dirname(exe_path))
        else:
            # 開発（Python）環境: 従来のスクリプト起動
            script_path = os.path.join(const.BASE_DIR, "reissue_tool_pro.py")
            subprocess.Popen([sys.executable, script_path], shell=False, cwd=const.BASE_DIR)
    except Exception as e:
        logging.error(f"再発行ツールのプロセス起動に失敗しました (CWD: {const.BASE_DIR}): {e}", exc_info=True)
        


def open_current_log():
    """右クリックメニュー：シェルを経由せず、安全かつ確実にメモ帳をポップアップさせる"""
    """タスクトレイから現在のシステムログをメモ帳で開く安全な実装"""
    try:
        #constライブラリ経由で最新のログパスを動的取得する
        log_path = const.get_log_path() 
        
        if os.path.exists(log_path):
            os.startfile(log_path)
            logging.info(f"ログファイルを開きました: {log_path}")
        else:
            logging.warning(f"ログファイルが存在しません: {log_path}")
            # もしファイルが空なら作成して開くか、エラーを出す
            with open(log_path, 'a', encoding='cp932') as f:
                f.write(f"{datetime.datetime.now()} [INFO] 新規ログ作成\n")
            os.startfile(log_path)
    except Exception as e:
        logging.error(f"ログファイル展開エラー（メモ帳の起動に失敗しました）: {e}", exc_info=True)
        
def restart_system(icon, item):
    global app_mutex
    try:
        common.write_log(const.LOG_DIR, "【システム操作】ユーザー要求によりシステムを再起動します...")
        icon.stop()
        
        # 【防弾仕様】OSへ鍵を明示的に返却し、次世代プロセスとの衝突を完全回避
        if 'app_mutex' in globals() and app_mutex:
            ctypes.windll.kernel32.CloseHandle(app_mutex)
            
        os.execl(sys.executable, sys.executable, *sys.argv)
    except Exception as e:
        # 【丁寧な出力】
        logging.critical(f"ユーザー要求によるシステム再起動処理に失敗しました: {e}", exc_info=True)
        
#終了本体。先に定義する
def execute_shutdown(icon):
    """実際の終了処理。終了ダイアログのスレッドから呼ばれる"""
    global is_running
    logging.info("システム終了が承認されました。")
    is_running = False
    
    # pystrayの停止
    if icon:
        icon.stop()
        
    # ロックファイル削除
    if os.path.exists(LOCK_FILE):
        try: os.remove(LOCK_FILE)
        except: pass
        
    # プロセスの全終了
    import multiprocessing
    for p in multiprocessing.active_children():
        p.terminate()
        
    os._exit(0)

#メニュー操作系
def quit_system(icon, item):
    """トレイから呼ばれる終了要求を、安全なメインスレッドへ引き渡す"""
    # 終了要求をフラグ立ててpystrayスレッドから抜けるのが正攻法
    # ここではMessageBoxを直接呼ばず、安全に終了処理へ誘導する
    
    # 別スレッドで終了ダイアログを出す（これでデッドロックを防ぐ）
    threading.Thread(target=confirm_and_quit, args=(icon,), daemon=True).start()

def confirm_and_quit(icon):
    """ダイアログを表示する専用スレッド"""
    # MessageBoxは単体スレッドで動かすことで、メインのイベントループと競合させない
    res = ctypes.windll.user32.MessageBoxW(
        0, 
        "領収明細票監視システムを完全に終了してもよろしいですか？\n\n※終了すると、レセコン連動によるバーコードの自動発行・自動印刷がすべて停止します。", 
        "システム完全終了の確認", 
        0x4 | 0x30 | 0x40000
    )
    
    if res == 6: # IDYES
        # メインの終了処理を呼ぶ
        execute_shutdown(icon)


def setup_tray_and_run():
    """メインスレッド：黒い画面を隠し、タスクトレイアイコンを構築して常駐する"""
    global tray_icon
    
    # 1. 【Windows API】起動した瞬間に自分自身の「黒いコンソール画面」を完全に非表示にする隠れ身の術
    try:
        import ctypes
        whnd = ctypes.windll.kernel32.GetConsoleWindow()
        if whnd != 0:
            ctypes.windll.user32.ShowWindow(whnd, 0) # 0 = SW_HIDE (非表示)
    except Exception as e:
        print(f"ウィンドウ非表示化エラー: {e}")

    # 2. トレイアイコンの見た目を初期状態「オンライン(緑)」で動的生成
    icon_image = create_state_icon_image(is_online=True)

    # 3. 【メニューのネスト階層化】日常業務で絶対触らないシステム制御系をサブメニューへ隔離
    # pystray.Menu の引数の中にさらに pystray.Menu をネストすることで、現場での誤クリックを完全防御
    admin_menu = pystray.Menu(
        item('>> システムを再起動', restart_system),
        item('>> システムを完全終了', quit_system)
    )

    tray_menu = pystray.Menu(
        item('[状況] 現在の稼働状況を確認', lambda icon, item: request_gui_open()),
        pystray.Menu.SEPARATOR,
        item('[設定] 設定管理ツールを開く', open_config_tool),
        item('[履歴] 再発行画面を開く', open_reissue_tool), 
        item('[ログ] 過去の全履歴を直接確認', lambda icon, item: open_current_log()),
        pystray.Menu.SEPARATOR,
        item('システム管理', admin_menu) # サブメニューをマッピング
    )
    
    # 4. アイコンオブジェクトの生成
    tray_icon = pystray.Icon(
        "BarcodeMonitor", 
        icon_image, 
        title=f"領収明細票 監視システム ({CURRENT_VERSION})", 
        menu=tray_menu
    )
    
    # 5. 【マルチスレッド隔離】ファイル監視ループを別動隊(サブ)へ委譲
    monitor_thread = threading.Thread(target=fast_monitor_loop, daemon=True)
    monitor_thread.start()
    
    # 6. 【トレイ非同期駆動】pystrayをサブスレッドへ逃がし、Windowsのトレイ通知領域に常駐させる
    tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
    tray_thread.start()
    
    # 7. メインスレッドの特権を使い、Tkinterの土台をフリーズなく安全に起動
    create_status_window_stub()

def request_gui_open():
    global gui_open_requested
    gui_open_requested = True

def update_gui_display(message: str):
    """ログブリッジから呼ばれ、GUI上のステータス表示用配列を更新する"""
    # 既にスレッドセーフな `update_status_log` があるため、それに丸投げするだけでOK！
    update_status_log(message)

if __name__ == "__main__":
    MUTEX_NAME = "Pharmacy_BarcodeSystem_Monitor_Mutex_v2"
    app_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(0, "領収明細票監視システムは既にバックグラウンドで起動しています。\n多重起動を防ぐため終了します。", "多重起動エラー", 0x10)
        sys.exit(0)
        
    try:
        # ★多重起動がない「本物の実行プロセス」であることを確認してから初めて環境を汚す
        initialize_system() 
        
        setup_tray_and_run()
    except Exception as e:
        # 初期化関数が走った後なので、ここでの例外も安全にFileHandlerへ書き込まれます
        logging.critical(f"トレイ常駐プロセスで予期せぬ致命的エラー: {e}", exc_info=True)

# Copyright (c) 2026 ph-SIM133
# All rights reserved.
# This software is for non-commercial use only.