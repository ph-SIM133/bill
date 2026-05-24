# -*- coding: cp932 -*-
#必ずshift-jisにて保存を！！
import os
import sys
import json
import datetime
import time
import shutil
import configparser
import re
import msvcrt
import traceback
import lib_constants as const
import lib_common as common
import logging

# --- ロガーインスタンスの取得 ---
logger = logging.getLogger("DB.BulkReg")

# ==============================================================================
# 1. 基本設定とパス（全ファイル共通の0地点 BASE_DIR に統一）
# ==============================================================================
# --- パス定義をライブラリへ一本化（重複と計算ミスを排除） ---
BASE_DIR = const.BASE_DIR
INI_FILE = const.INI_FILE
MASTER_HISTORY_FILE = const.MASTER_HISTORY_FILE
# --- safe_int, load_settings(load_full_config) は ---
# --- lib_common.py へ集約したため、このスクリプト内からは全削除します ---

# ==============================================================================
# 1. ロギング設定（system_YYYYMM.txtへの統合）
# ==============================================================================
# 【起動順序の依存を完全解消】
# ネットワーク上にまだフォルダがない初期状態でも絶対に落ちないよう、
# ログファイルを開く前に、親フォルダ（log）を自動で強制生成します。
os.makedirs(const.LOG_DIR, exist_ok=True)

log_filename = f"system_{datetime.datetime.now().strftime('%Y%m%d')}.txt"
log_path = os.path.join(const.LOG_DIR, log_filename)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(log_path, encoding='cp932', errors='replace'),
        logging.StreamHandler()
    ]
)



def run_bulk_registration():
    """DB一括登録のメインロジック：完全ログ追跡版"""
    #logger.info("=== DB一括登録処理を開始します ===")
    #print("=== DB一括登録処理を開始します ===")
    # --- ログディレクトリを特定（メインスクリプトと同期） ---
    try:
        conf = common.load_full_config()
        target_idx_dir = conf.get('index_dir', '')
        days_limit_val = int(conf.get('target_days', 365))
        
        # 【看板の同期】同じINIから名称を引っ張る
        sys_name = conf.get('systemname', '領収明細票（薬局控）')
        logger.info(f"=== {sys_name} 一括登録処理を開始します ===")
        #print(f"=== {sys_name} 一括登録処理を開始します ===")
        logger.info(f"設定確認: IndexDir={target_idx_dir}, 保持日数={days_limit_val}")
        
        if not target_idx_dir:
            logger.error(f"致命的エラー: 対象ディレクトリが無効です: {target_idx_dir}")
            return
            
        if not os.path.exists(target_idx_dir):
            logger.error(f"エラー: フォルダが見つかりません: {target_idx_dir}")
            return

        # 基準日の計算（型安全）
        now_dt = datetime.datetime.now()
        # --- 定義済みの days_limit_val を使用 ---
        delta_dt = datetime.timedelta(days=days_limit_val)
        limit_dt = now_dt - delta_dt
        limit_str = limit_dt.strftime('%Y%m%d')
        logger.info(f"基準日設定: {limit_str} 以降のA/Uファイルを走査します。")
        #【パスの可視化】現在どっちのファイルを見にいっているかをログに明記
        logger.info(f"履歴ファイル位置: {MASTER_HISTORY_FILE}")
        
        # --- ディレクトリ走査 ---
        all_found_files = os.listdir(target_idx_dir)
        logger.info(f"全ファイル数: {len(all_found_files)} 件を検知。")
            
        target_list = []
        
        for filename in all_found_files:
            name_part, ext_part = os.path.splitext(filename)
            ext_up = ext_part.upper()
            if ext_up in ['.TXT', '.DAT']:
                if len(name_part) == 21:
                    # 監視プログラムの仕様に合わせ、A または U のみを収集[cite: 41]
                    if name_part[0].upper() in ['A', 'U']:
                        f_date_str = name_part[3:11]
                        if f_date_str >= limit_str:
                            target_list.append(filename)
        logger.info(f"登録候補抽出完了: {len(target_list)} 件 (基準日以降のA/Uファイル)")
        if not target_list:
            logger.info("登録対象の新規ファイルはありません。処理を終了します。")
            return

        history_data_list = []
        parent_history_dir = os.path.dirname(MASTER_HISTORY_FILE)
        os.makedirs(parent_history_dir, exist_ok=True)
        
        for retry in range(3):
            try:
                # ファイル未存在時は空リストで初期化（'r+'オープンの前提条件）
                if not os.path.exists(MASTER_HISTORY_FILE):
                    with open(MASTER_HISTORY_FILE, 'w', encoding='cp932') as f_init:
                        json.dump([], f_init)
                    logger.info("履歴ファイルが新規作成されました。")
                    
                add_count = 0    
                with open(MASTER_HISTORY_FILE, 'r+', encoding='cp932') as f_io:
                    # 監査指摘に基づき、ロック範囲を最大値(0x7FFFFFFF)に拡張してファイル全域を完全隔離
                    msvcrt.locking(f_io.fileno(), msvcrt.LK_LOCK, 0x7FFFFFFF)
                    logger.debug(f"履歴ファイルをロックしました (試行:{retry+1})")
                    try:
                        history_data_list = json.load(f_io)
                        
                        # --- 登録ロジック実行（保護エリア内） ---
                        
                        existing_set = set(history_data_list)
                        for target_fn in target_list:
                            if target_fn not in existing_set:
                                history_data_list.append(target_fn)
                                existing_set.add(target_fn)
                                add_count += 1

                        if add_count > 0:
                            f_io.seek(0)
                            json.dump(history_data_list, f_io, indent=4)
                            f_io.truncate() # 書き込み後の残存データを切捨て
                            # ---同期シーケンスの徹底 ---
                            f_io.flush()
                            os.fsync(f_io.fileno())
                            logger.info(f"履歴保存成功: {add_count} 件の新規エントリを記録しました。")
                        else:
                            logger.info("重複チェック完了: すべて登録済みでした。")
                    finally:
                        f_io.seek(0)
                        msvcrt.locking(f_io.fileno(), msvcrt.LK_UNLCK, 0x7FFFFFFF) # 確実に解除
                        logger.debug("履歴ファイルのロックを解除しました。")
                    # --- ロック解除・ファイルクローズの完全に外側でバックアップと通知を実行 ---
                if add_count > 0:        
                    shutil.copy2(MASTER_HISTORY_FILE, MASTER_HISTORY_FILE + ".bak")
                    logger.info("バックアップファイル (.bak) を更新しました。")
                    logger.info("メディア同期待機中 (2.5s)...")
                    time.sleep(2.5)
                        
                logger.info("=== DB一括登録処理を正常終了しました ===")

                return # 成功したので終了
            except (PermissionError, BlockingIOError, OSError) as e:
                logger.warning(f"ファイルアクセス競合検知 (リトライ中...): {e}")
                if retry < 2:
                    time.sleep(1.5)
                    continue
                logger.error("致命的エラー: 他のプロセスが履歴ファイルを占有しています。")
                return
            
    except Exception as e:
        logger.critical(f"予期しない致命的エラーが発生しました:\n{traceback.format_exc()}")

if __name__ == "__main__":
    run_bulk_registration()
    
# Copyright (c) 2026 ph-SIM133
# All rights reserved.
# This software is for non-commercial use only.