# -*- coding: cp932 -*-
#必ずshift-jisで保存を！！
import os
import re
import csv
import glob
import math
from typing import Set, Dict, Any, Union
import logging
logger = logging.getLogger("BarcodeSystem.parser") 

def get_facility_patient_ids(facility_dir: str, col_idx_1based: int) -> Set[str]:
    """除外患者IDリストの取得（型安全・パス正規化版）"""
    skip_ids = set()
    # 型安全：設定値を確実に整数へキャスト
    c_idx_int = int(col_idx_1based)
    col_idx = c_idx_int - 1
    if col_idx < 0:
        col_idx = 0
    target_dir = os.path.normpath(facility_dir)
    if not os.path.exists(target_dir):
        return skip_ids
    for file_path in glob.glob(os.path.join(target_dir, "*.csv")):
        for enc in ['cp932', 'utf-8-sig']:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    reader = csv.reader(f)
                    try:
                        next(reader)
                    except StopIteration:
                        continue
                    for row in reader:
                        if row and len(row) > col_idx:
                            val = row[col_idx].strip()
                            if val:
                                skip_ids.add(val.lstrip('0'))
                    break 
            except Exception as e:
                logger.warning(f"施設CSV読込試行失敗({os.path.basename(file_path)} - {enc}): {e}")
                continue
    
    return skip_ids

def process_nsips(filepath: str, watch_dir: str, facility_ids: Set[str], is_comparing: bool = False, conf: Dict[str, Any] = None) -> Union[str, Dict[str, Any]]:
    """NSIPS解析エンジン（A/U・差額/再発行 4モード完全分離版）"""
    norm_filepath = os.path.normpath(filepath)
    norm_watch_dir = os.path.normpath(watch_dir)
    filename = os.path.basename(norm_filepath)
    
    clean_name = filename.replace("processing_", "").replace("reissue_", "").replace("proc_", "")
    
    try:
        with open(norm_filepath, 'r', encoding='cp932') as f:
            lines = [line.strip().split(',') for line in f if line.strip()]
    except Exception as e:
        logger.error(f"NSIPSファイル読込失敗({filename}): {e}")
        return "READ_ERROR"

    row1 = next((r for r in lines if len(r) > 1 and r[0] == '1'), None)
    row2 = next((r for r in lines if len(r) > 1 and r[0] == '2'), None)
    row5 = next((r for r in lines if len(r) > 1 and r[0] == '5'), None)
    
    row5_normalized = [x.strip() for x in row5] if row5 else []
    nsips_rev_flag = row2[3].upper() if row2 and len(row2) > 3 else "A"
    status_code = nsips_rev_flag
    nsips_receipt_no = str(row2[2]).strip() if row2 and len(row2) > 2 else "----"

    if not row5 or not row1:
        return "NO_DATA_ROW"

    patient_id = row1[1].lstrip('0') if len(row1) > 1 else ""
    if patient_id in facility_ids:
        return "SKIP_FACILITY"

    # ====================================================================
    # 空文字や記号が入った際に int() で即死するのを防ぐ安全なキャスト関数
    def safe_int(row_data, idx, default=0):
        if not row_data or len(row_data) <= idx:
            return default
        val_str = str(row_data[idx]).strip()
        if not val_str: # 空文字の場合は即死させずに0を返す
            return default
        try:
            return int(val_str)
        except ValueError:
            logger.warning(f"数値変換エラー: インデックス {idx} の値 '{val_str}' を {default} として扱います。")
            return default
    # ====================================================================

    # 設定値と生データの取得（安全なキャスト関数を経由させる）
    ns_cfg = {k.lower(): v for k, v in (conf.get('NSIPS_Assign', {}) if conf else {}).items()}
    
    val16 = safe_int(row5, int(ns_cfg.get('prev_unpaid', 16)))
    val17 = safe_int(row5, int(ns_cfg.get('presc', 17)))
    val18 = safe_int(row5, int(ns_cfg.get('already_paid', 18)))
    val19 = safe_int(row5, int(ns_cfg.get('receipt', 19)))
    val20 = safe_int(row5, int(ns_cfg.get('curr_unpaid', 20)))
    burden_rate = safe_int(row1, 19)

    if not is_comparing and val19 == 0 and val16 == 0:
        return "SKIP_ZERO_RECEIPT"

    # ====================================================================
    # 4モードの数学的切り分けとフラグ完全分離
    # ====================================================================
    # 患者視点の差額（正=追金, 負=返金）
    adj_val = -(val16 - val20) 
    
    # 前回未収または今回未収が1円でもあれば、全モード共通で詳細印字を強制ON
    is_detail_display = (val16 != 0 or val20 != 0)
    is_mask_enabled = False

    # 前回未収が0円なのに、今回未収だけが単独で発生している「送りのみ」判定
    is_only_forwarding = (val16 == 0 and val20 > 0)

    if status_code == 'U' and adj_val != 0 and not is_only_forwarding:
        # モード③【U差額精算】
        is_mask_enabled = True
        final_bal = adj_val
    elif status_code == 'A':
        # モード①・②【A0新規発行】
        is_mask_enabled = False
        final_bal = val19
    else:
        # モード④【U再発行】
        is_mask_enabled = False
        final_bal = val19

    # 表示ラベルの定義
    rl_cfg = conf.get('ReceiptLabels', {}) if conf else {}
    receipt_labels = {
        'presc': rl_cfg.get('presc_label', '請求金額'),
        'paid': rl_cfg.get('paid_label', '前回領収済'),
        'prev_unpaid': rl_cfg.get('prev_unpaid_label', '前回未収金'),
        'curr_unpaid': rl_cfg.get('curr_unpaid_label', '今回未収金')
    }
    receipt_labels['prev_unpaid_disp'] = "前回過払い分(預り)" if (status_code == 'U' and val16 > 0) else receipt_labels['prev_unpaid']
    receipt_labels['curr_unpaid_disp'] = "今回過払い分(預り)" if (status_code == 'U' and val20 > 0) else receipt_labels['curr_unpaid']

    extracted_amounts = {}
    item_slots = {}
    if conf and 'print_items' in conf:
        # 容器代・選定療養の分離計算
        container_total = 0
        if burden_rate != 100:
            for r in lines:
                if len(r) > 24 and r[0] == '4' and str(r[3]) == '3':
                    try:
                        u_p, qty = float(r[24]), float(r[16])
                        container_total += (int(round(u_p * 1.1)) * int(qty))
                    except Exception as e:
                        # 計算不能な異常データ（単価や数量が空欄など）が混入した場合にログへ痕跡を残す
                        u_p_str = r[24] if len(r) > 24 else 'N/A'
                        qty_str = r[16] if len(r) > 16 else 'N/A'
                        logger.warning(f"容器代の計算をスキップしました (単価:'{u_p_str}', 数量:'{qty_str}'): {e}")
                        continue
                        
        # 選定療養も安全なキャスト関数(修正1で作成)で防弾化
        sentei_amt = max(0, safe_int(row5, 15) - container_total)
        
        s_idx = int(conf.get('selfpay_item_index', 4))
        p_idx = int(conf.get('prescription_item_index', 1))
        
        # 各項目のマッピング
        for idx, it in enumerate(conf['print_items'], 1):
            it_name = it['name']
            if "未収" in it_name or "追金" in it_name:
                if not is_mask_enabled:
                    val = val16 if val16 > 0 else 0
                else:
                    val = adj_val if adj_val > 0 else 0
            elif "返金" in it_name:
                if not is_mask_enabled:
                    val = abs(val16) if val16 < 0 else 0
                else:
                    val = abs(adj_val) if adj_val < 0 else 0
            elif "選定" in it_name:
                val = sentei_amt
            elif "容器" in it_name:
                val = container_total
            else:
                target_row = next((r for r in lines if r[0] == str(it['record_type'])), None)
                c_idx = int(it['col_idx'])
                # 空文字の int() 即死を防止し、safe_intに委譲
                val = safe_int(target_row, c_idx)
            
            # 処方と自費の排他判定
            if burden_rate != 100 and idx == s_idx:
                val = 0 
            
            item_slots[idx] = it_name
            extracted_amounts[it_name] = val
        
        # ====================================================================
        # 自費100%オーバーライド と マスク処理
        # ====================================================================
        if burden_rate == 100:
            s_name = item_slots.get(s_idx, "自費(100%)")
            for k in extracted_amounts.keys():
                extracted_amounts[k] = final_bal if k == s_name else 0
                
        elif is_mask_enabled:
            # U差額精算モード時のみ、基本バーコードを無効(0円)にする
            for k in extracted_amounts.keys():
                if not any(x in k for x in ["未収", "追金", "返金"]):
                    extracted_amounts[k] = 0
                    
    # エラー回避：拡張子を切り離してから数値変換
    name_body = os.path.splitext(clean_name)[0]
    try:
        rev_num_part = name_body[-5:] if len(name_body) >= 5 else "0"
        rev_int = int(rev_num_part)
    except ValueError as e:
        # 連番部分が数字でない特殊なファイル名を検知
        logger.warning(f"ファイル名末尾からの連番抽出に失敗したため、0として扱います (対象:{name_body}): {e}")
        rev_int = 0              
                    
    # 日付整形ロジック
    raw_date = row2[4] if row2 and len(row2) > 4 else ""
    fmt_date = f"{raw_date[0:4]}/{raw_date[4:6]}/{raw_date[6:8]}" if len(raw_date) == 8 else "----/--/--"

    current_data = {
        "dispensing_date": fmt_date,
        "patient_name": row1[3] if len(row1) > 3 else "不明",
        "patient_id": patient_id,
        "receipt_no": nsips_receipt_no,
        "amounts": extracted_amounts,
        "pure_presc": val17,
        "already_paid": val18,
        "prev_unpaid": val16,
        "curr_unpaid": val20,
        "total_amount": final_bal,
        "is_adjustment": is_detail_display, # drawerの詳細印字フラグに直結
        "is_additional": (final_bal > 0),
        "receipt_labels": receipt_labels,
        "row5_fingerprint": row5_normalized,
        "filename": name_body,
        "burden_rate": burden_rate,
        "rev_label": (
            f"A{rev_int}新規発行" if status_code == 'A' else 
            f"U{'X' if rev_int >= 10 else rev_int}{'差額精算' if is_mask_enabled else '再発行'}"
        ),
        "raw_rev": status_code
    }

    # ====================================================================
    # 重複判定ロジック（Uかつ変動なしなら発行抑制）
    # ====================================================================
    if not is_comparing and status_code == 'U':
        # ① 安全に拡張子（.txt）を切り離して変数化
        _, ext = os.path.splitext(clean_name)
        
        checks = ["A" + clean_name[1:]]
        m_seq = re.search(r'(\d{3})$', name_body)
        if m_seq and int(m_seq.group(1)) > 0:
            prev_seq = str(int(m_seq.group(1)) - 1).zfill(3)
            base = name_body[:m_seq.start()]
            checks.extend([f"U{base[1:]}{prev_seq}{ext}", f"A{base[1:]}{prev_seq}{ext}"])
        
        for cf in checks:
            # 素の名前のパス
            cp = os.path.normpath(os.path.join(norm_watch_dir, cf))
            # ② メインの監視に連動した processing_ 付きのパス
            cp_proc = os.path.normpath(os.path.join(norm_watch_dir, "processing_" + cf))
            
            # 看板（ファイル名）が掛け替えられていても100%捕捉する多層防衛策
            target_path = None
            if os.path.exists(cp):
                target_path = cp
            elif os.path.exists(cp_proc):
                target_path = cp_proc
                
            if target_path:
                pr = process_nsips(target_path, norm_watch_dir, facility_ids, is_comparing=True, conf=conf)
                # 5行目の金銭データ指紋（fingerprint）を厳密に比較
                if isinstance(pr, dict) and pr.get('row5_fingerprint') == current_data.get('row5_fingerprint'):
                    return "SKIP_AMOUNT"

    return current_data
# Copyright (c) 2026 ph-SIM133
# All rights reserved.
# This software is for non-commercial use only.