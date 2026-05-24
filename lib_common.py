# -*- coding: cp932 -*-
import os
import sys
import datetime
import configparser
import win32ui
import win32print
import win32con
from PIL import ImageWin
import lib_constants as const
from typing import Any, Dict
import logging
REPORTED_CONFIG_ERRORS = set()

# BarcodeSystem配下のサブロガーとして定義（統合管理用）
logger = logging.getLogger("BarcodeSystem.common")

def safe_int(value: Any, default_val: int, name: str) -> int:
    try:
        if value is None: return default_val
        return int(str(value).strip())
    except (ValueError, TypeError):
        err_id = f"int_{name}_{value}"
        if err_id not in REPORTED_CONFIG_ERRORS:
            # print を logger.warning に変更
            logger.warning(f"設定エラー：{name} の値 '{value}' は数値ではありません。デフォルト値 {default_val} を使用します。")
            REPORTED_CONFIG_ERRORS.add(err_id)
        return default_val

def write_log(log_dir: str, message: str) -> None:
    """
    操作ログをテキストに記録し、かつ標準ロギングシステムへもブリッジする
    """
    
    now = datetime.datetime.now()
    
    # メッセージの内容によってレベルを自動判定
    if "!" in message or "エラー" in message or "失敗" in message:
        logger.error(message)
    else:
        logger.info(message)
    
    # ログディレクトリが存在しない場合は強制生成し、openエラーによる無言のクラッシュを防ぐ
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception as md_e:
            logger.error(f"ログディレクトリの自動生成に失敗: {md_e}")
            
    log_path = os.path.join(log_dir, f"operation_{now.strftime('%Y%m%d')}.txt")
    line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        if not os.path.exists(log_dir): os.makedirs(log_dir, exist_ok=True)
        with open(log_path, "a", encoding="cp932", errors="replace") as f:
            f.write(line + "\n")
    except Exception as e:
        # 完全に隔離されたエラーとして詳細パス付きで出力
        logger.error(f"ログファイル書込失敗 (対象パス: {log_path}): {e}")
    
    # GUIへの表示ブリッジ（実行環境のモジュール名依存を完全解消）
    try:
        import __main__
        # パターン1: スクリプトが直接実行されている場合 (__main__)
        if hasattr(__main__, 'update_gui_display'):
            __main__.update_gui_display(message)
        # パターン2: 外部からモジュールとしてインポートされている場合
        elif 'barcode_monitor' in sys.modules:
            monitor_mod = sys.modules['barcode_monitor']
            if hasattr(monitor_mod, 'update_gui_display'):
                monitor_mod.update_gui_display(message)
    except Exception:
        # GUIブリッジの失敗はメインの動作を妨げないようサイレントに処理
        pass

    
def purge_old_files(target_dir: str, days: int, pattern: str = "*") -> None:
    """指定日数経過したファイルを物理削除する防弾ロジック"""
    import time
    import glob
    
    threshold = time.time() - (days * 86400)
    for f in glob.glob(os.path.join(target_dir, pattern)):
        try:
            if os.path.isfile(f) and os.path.getmtime(f) < threshold:
                os.remove(f)
                logger.info(f"期限切れファイルを削除しました: {os.path.basename(f)}")
        except Exception as e:
            logger.warning(f"ファイル削除失敗 ({f}): {e}")            
            

def initialize_directories(cfg: configparser.ConfigParser) -> Dict[str, str]:
    """サテライトディレクトリの確定と生成（すべてBASE_DIRの一つ上の階層に固定）[cite: 38]"""
    log_dir_base = os.path.normpath(os.path.join(const.BASE_DIR, "..", "log"))
    if cfg.has_section('Paths'):
        if cfg.has_option('Paths', 'LogDir'):
            ld_raw = cfg.get('Paths', 'LogDir').strip()
            if ld_raw:
                if os.path.isabs(ld_raw):
                    log_dir_base = os.path.normpath(ld_raw)
                else:
                    log_dir_base = os.path.normpath(os.path.join(const.BASE_DIR, ld_raw))
    
    paths = {
        'log': log_dir_base,
        'work': os.path.normpath(os.path.join(const.BASE_DIR, "..", "work_dir")),
        'pdf': os.path.normpath(os.path.join(const.BASE_DIR, "..", "pdf_output")),
        'history': os.path.normpath(os.path.join(const.BASE_DIR, "..", "history"))
    }
    
    for p in paths.values():
        try:
            if not os.path.exists(p):
                os.makedirs(p, exist_ok=True)
                logger.info(f"ディレクトリを作成しました: {p}")
        except Exception as e:
            # 作成に失敗した場合は、起動を止めるべき重大なエラーとしてログに残す
            logger.error(f"ディレクトリ準備失敗({p}): {e}")
            raise # 上位に投げてシステムを停止させる
            
    return paths

def print_image_directly(img, printer_name: str) -> None:
    """プリンタへ直接画像を送る（外科手術：DC管理の堅牢化）"""
    # --- "non" の場合は印刷せず即座にリターン（テスト・検証用） ---
    if printer_name.lower() == "non":
        print("DEBUG: 出力先が 'non' のため、物理印刷をバイパスしました。")
        return
    # win32uiのCreateDCはオブジェクトを生成した瞬間から管理が必要    
    #  プリンタ名の大文字・小文字揺れを完全吸収する
    # 呼び出し元で .lower() されて渡されるバグを吸収するため、OSの登録名と照合・補正する
    actual_printer_name = printer_name
    try:
        printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
        for p in printers:
            if p[2].lower() == printer_name.lower():
                actual_printer_name = p[2]  # OSが認識している正確な名称で上書き
                break
    except Exception as e:
        logger.debug(f"プリンタ名の補正中にエラー（無視して続行可能）: {e}") # debugレベルで十分

    # 3. デバイスコンテキスト（DC）の作成
    hDC = win32ui.CreateDC()
    try:
        hDC.CreatePrinterDC(actual_printer_name)
    except Exception as e:
        # 万が一それでも失敗した場合は、エラーの原因を明確にUIへ返す
        raise RuntimeError(f"プリンタ '{actual_printer_name}' への接続に失敗しました。\nプリンタがオフラインか、名称が異なります。\n詳細: {e}")


    logger.info(f"[印刷要求] Windowsスプーラへジョブを投入します (出力先プリンタ: '{actual_printer_name}')")

    try:
        hDC.StartDoc('Barcode_Reissue')
        hDC.StartPage()
        logger.debug("Windows Print-DC: StartDoc/StartPage 処理開始")

        # 画像の描画処理
        dib = ImageWin.Dib(img)  # 引数の img を使用
        img_w, img_h = img.size
        
        # プリンタドライバで設定されている用紙の「印字可能ピクセル数」を取得
        prn_w = hDC.GetDeviceCaps(win32con.HORZRES)
        prn_h = hDC.GetDeviceCaps(win32con.VERTRES)
        
        # 現場インフラの解像度ミスマッチを追跡するため、DPIとピクセル数をログに確保
        logger.info(f"[DPI解析] 元画像サイズ: {img_w}x{img_h} -> プリンタ印字可能領域: {prn_w}x{prn_h}")
        
        # はみ出さないように比率を自動補正
        ratio = min(prn_w / img_w, prn_h / img_h)
        draw_w = int(img_w * ratio)
        draw_h = int(img_h * ratio)
        
        # 描画実行
        dib.draw(hDC.GetHandleOutput(), (0, 0, draw_w, draw_h))
        logger.debug(f"Windows Print-DC: 画像ビットマップ転送成功 (描画サイズ: {draw_w}x{draw_h})")
        
        # 正常終了シーケンス
        hDC.EndPage()
        hDC.EndDoc()
        # ここが刻印されればプログラム側の責任は100%全うされた証明になります
        logger.info(f"[印刷成功] スプーラへの転送が完全完了しました。これ以降の不調はWindowsシステムまたはハードウェアに起因します。")
        
        
        
    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        # 現場でのLANプリンタ切断、ドライバオフラインなどを詳細スタックトレースで捕捉
        logger.error(f"[印刷失敗] Windows Spoolerへのジョブ転送中に致命的エラーが発生しました: {e}\n{err_detail}")
        print(f"Printing process interrupted: {e}")
        try:
            logger.warning("[印刷リカバリ] 印刷ジョブの破棄（AbortDoc）を試みます...")
            hDC.AbortDoc() # ジョブの中断
            logger.info("[印刷リカバリ] 印刷ジョブは正常に破棄されました。")
        except Exception as abort_e:
            logger.error(f"[印刷中断エラー] AbortDocに失敗しました (プリンタとの通信物理切断など): {abort_e}")
            print(f"[印刷中断エラー] AbortDoc失敗: {abort_e}", file=sys.stderr)
        raise e # エラーを上位（ReissueToolPro）へ投げてメッセージボックスを表示させる
    
    finally:
        # 明示的にDCを削除
        # EndDoc/AbortDoc が正しく呼ばれていれば、通常は自動で閉じられますが
        # 現場での長期稼働における deleteDC failed 対策として try-except で保護
        try:
            hDC.DeleteDC()
        except:
            pass
        del hDC




def load_full_config() -> Dict[str, Any]:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str
    
    # ここで const.INI_FILE (lib_constantsで定義) を見に行っているか
    if not os.path.exists(const.INI_FILE):
        logger.error(f"INIファイルが見つかりません: {const.INI_FILE}")
        return {}
    
    content = ""
    # どのエンコーディングで成功/失敗したのか状態を追跡する
    success_enc = None
    for enc in ['cp932', 'utf-8-sig']:
        try:
            with open(const.INI_FILE, 'r', encoding=enc) as f:
                content = f.read().strip().lstrip('\ufeff')
                success_enc = enc
                break
        except Exception as e:
            # ここは debug に留め、本当に全て失敗した場合のみ error にする
            logger.debug(f"設定ファイル({enc})での読み込みをスキップ: {e}")
            
    if success_enc is None:
        logger.error(f"設定ファイル({const.INI_FILE})の読み込みに完全失敗しました。文字コードが不正(cp932/utf-8-sig以外)です。")
        return {}

    if not content:
        logger.error(f"設定ファイル({const.INI_FILE})が空です。中身を確認してください。")
        return {}
        
    try: 
        cfg.read_string(content)
    except Exception as e:
        logger.error(f"設定ファイルの構文解析に失敗しました: {e}")
        return {}
        
    settings = {}
    
    def get_safe_path(section, key):
        raw_p = cfg.get(section, key, fallback='').strip()
        if not raw_p: return ""
        if os.path.isabs(raw_p) or raw_p.startswith('\\\\'):
            return os.path.normpath(raw_p)
        return os.path.normpath(os.path.join(const.BASE_DIR, raw_p))

    # パス設定の集約
    settings['watch_dir'] = get_safe_path('Paths', 'WatchDir')
    settings['index_dir'] = get_safe_path('Paths', 'IndexDir')
    f_dir_raw = get_safe_path('Paths', 'FacilityDir')
    settings['facility_dir'] = f_dir_raw if f_dir_raw else os.path.normpath(os.path.join(const.BASE_DIR, "..", "facility_csv"))
    
    # 数値・テキスト設定の集約
    settings['printer'] = cfg.get('Printer', 'PrinterName', fallback='non').strip()
    settings['paper_size'] = cfg.get('Printer', 'PaperSize', fallback='A6').upper()
    settings['csv_col'] = safe_int(cfg.get('Settings', 'CsvPatientIdColumn', fallback='2'), 2, 'CsvPatientIdColumn')
    settings['target_days'] = safe_int(cfg.get('Settings', 'TargetDays', fallback='365'), 365, 'TargetDays')
    settings['maint_time'] = cfg.get('Settings', 'MaintenanceTime', fallback='0').strip()
    settings['total_amount_col'] = safe_int(cfg.get('NSIPS_Assign', 'TotalAmountColumn', fallback='19'), 19, 'TotalAmountColumn')  # ★'NSIPS_Assign'へ変更
    settings['notice_text'] = cfg.get('Settings', 'NoticeText', fallback='').strip()
    settings['notice_text2'] = cfg.get('Settings', 'NoticeText2', fallback='').strip()
    settings['notice_text3'] = cfg.get('Settings', 'NoticeText3', fallback='').strip()
    settings['notice_text4'] = cfg.get('Settings', 'NoticeText4', fallback='※【返品】ボタン')
    settings['notice_text5'] = cfg.get('Settings', 'NoticeText5', fallback=' 先に押すこと!!')
    settings['qr_line_break'] = cfg.get('Settings', 'QrLineBreak', fallback='CRLF').strip()
    settings['enable_delay_billing'] = int(cfg.get('Settings', 'EnableDelayBilling', fallback='0'))
    settings['delay_symbol_billing'] = cfg.get('Settings', 'DelaySymbolBilling', fallback='none').strip()
    settings['enable_delay_refund'] = int(cfg.get('Settings', 'EnableDelayRefund', fallback='0'))
    settings['delay_symbol_refund'] = cfg.get('Settings', 'DelaySymbolRefund', fallback='none').strip()
    settings['print_1d_barcode'] = int(cfg.get('Settings', 'Print1DBarcode', fallback='1'))
    settings['prescription_item_index'] = int(cfg.get('NSIPS_Assign', 'PrescriptionItemIndex', fallback='1'))  # ★'NSIPS_Assign'へ変更
    settings['selfpay_item_index'] = int(cfg.get('NSIPS_Assign', 'SelfPayItemIndex', fallback='4'))  # ★'NSIPS_Assign'へ変更
    # --- NSIPS会計レコード（Index 16-20）の動的アサイン設定をロード ---
    settings['nsips_assign'] = {}
    sec_ns = 'NSIPS_Assign'
    settings['nsips_assign']['prev_unpaid']  = safe_int(cfg.get(sec_ns, 'prev_unpaid', fallback='16'), 16, 'prev_unpaid')
    settings['nsips_assign']['presc']        = safe_int(cfg.get(sec_ns, 'presc', fallback='17'), 17, 'presc')
    settings['nsips_assign']['already_paid'] = safe_int(cfg.get(sec_ns, 'already_paid', fallback='18'), 18, 'already_paid')
    settings['nsips_assign']['receipt']      = safe_int(cfg.get(sec_ns, 'receipt', fallback='19'), 19, 'receipt')
    settings['nsips_assign']['curr_unpaid']  = safe_int(cfg.get(sec_ns, 'curr_unpaid', fallback='20'), 20, 'curr_unpaid')
    
    settings['LogRetentionDays'] = safe_int(cfg.get('Settings', 'LogRetentionDays', fallback='365'), 365, 'LogRetentionDays')
    try:
        settings['scale_rate'] = float(cfg.get('Printer', 'ScaleRate', fallback='1.0').strip())
    except ValueError:
        settings['scale_rate'] = 1.0
    
    settings['print_items'] = []
    if cfg.has_section('PrintItems'):
        for i in range(1, 7):
            v_raw = cfg.get('PrintItems', f"Item{i}", fallback="")
            if v_raw:
                parts = [x.strip() for x in v_raw.split(',')]
                if len(parts) >= 7:
                    settings['print_items'].append({
                        'name': parts[0], 
                        'prefix': parts[1], 
                        'code': parts[2], 
                        'record_type': parts[3], 
                        'col_idx': safe_int(parts[4], 1, f'Item{i}_ColIdx') - 1,
                        'pos_x': safe_int(parts[5], 0, f'Item{i}_PosX'),
                        'pos_y': safe_int(parts[6], 0, f'Item{i}_PosY'),
                        'status': safe_int(parts[7], 1, f'Item{i}_Status') if len(parts) >= 8 else 1
                    })
    
    sec_layout = f"Layout_{settings['paper_size']}"
    settings['layout'] = {}
    if cfg.has_section(sec_layout):
        for k, v in cfg.items(sec_layout):
            if v.lstrip('-').isdigit():
                settings['layout'][k] = int(v)
        
    return settings
    
# Copyright (c) 2026 ph-SIM133
# All rights reserved.
# This software is for non-commercial use only.

