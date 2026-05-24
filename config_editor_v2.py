# -*- coding: cp932 -*-
#必ずshift-jisで保存を！！
import os
import sys
import logging
import traceback
import datetime
# ==============================================================================
#0地点（BASE_DIR）をsys.pathの最優先に挿入し、参照先を強制固定
# ==============================================================================
import lib_constants as const
sys.path.insert(0, const.BASE_DIR)

import configparser
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from tkinter import filedialog
from PIL import Image
from PIL import ImageTk
import win32print
import lib_barcode_drawer as drawer
import barcode_monitor as main_script

class ConfigEditorV2:
    def __init__(self, root):
        self.root = root
        self.logger = self._setup_logger()
        
        # タイトルおよびウィンドウサイズの設定
        title_str = "領収明細票システム：設定管理ツール (80mm・2D一本化移行準備版)"
        self.root.title(title_str)
        self.root.geometry("1880x1000")
        
        # 定数定義(const)の0地点に強制同期 ---
        self.base_dir = const.BASE_DIR
        self.ini_file = main_script.INI_FILE
        
        #一時フォルダは本拠地（共有側）に引きずられず、各PCの足元へ完全隔離する
        self.work_dir = const.WORK_DIR  # ★constから直接、隔離パスを紐付け
        try:
            if not os.path.exists(self.work_dir):
                os.makedirs(self.work_dir)
        except Exception as folder_e:
            self.logger.error(f"足元隔離ワークフォルダの自動生成に失敗しました ({self.work_dir}): {folder_e}", exc_info=True)
            messagebox.showwarning("環境警告", f"ワークフォルダの自動生成に失敗しました。権限を確認してください。\nパス: {self.work_dir}")
            
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.optionxform = str
        
        # INIファイルの読み込み
        if os.path.exists(self.ini_file):
            try:
                with open(self.ini_file, 'r', encoding='cp932') as f:
                    content = f.read().strip().lstrip('\ufeff')
                    self.config.read_string(content)
            except Exception as ini_e:
                # 最初のパース失敗理由を確実にフライトレコーダーに残す
                self.logger.warning(f"INIファイルのプライマリ読み込み（read_string）で例外を検知したため、セカンダリ読み込みへフォールバックします: {ini_e}", exc_info=True)
                try:
                    self.config.read(self.ini_file, encoding='cp932')
                except Exception as ini_critical_e:
                    self.logger.critical(f"INIファイルの完全読み込みに失敗しました: {ini_critical_e}", exc_info=True)
                    
        self.img_tk = None
        # ==============================================================================
        # 1D/2D バーコード動的スイッチ用変数の初期化
        # ==============================================================================
        self.print_1d_barcode_var = tk.IntVar(value=1)
        self.barcode_mode_var = tk.StringVar(value="2D")
        
        # 0円項目非表示フラグの初期化（必ず create_widgets の前に置く）
        self.hide_zero_items_var = tk.IntVar(value=0)
        
        self.create_widgets()
        self.load_settings()
        
        
        # ウィンドウの「×」ボタンが押された際、裏で生き残っているすべてのスレッドを
        # 道連れにして、OSレベルでプロセスを完全に即時抹殺する
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def _setup_logger(self):
        """定数定義に基づき、親階層のlogフォルダにロガーを紐付ける"""
        # ログフォルダの生成失敗によるシステム即死を回避
        try:
            os.makedirs(const.LOG_DIR, exist_ok=True) 
        except Exception as log_dir_e:
            sys.stderr.write(f"[CRITICAL] ログフォルダの生成に失敗しました: {log_dir_e}\n")
            
        logger = logging.getLogger("ConfigEditor")
        logger.setLevel(logging.INFO)
         
        # 既存のハンドラがあればクリア（重複防止）
        if logger.hasHandlers():
            logger.handlers.clear()
        try:    
            log_path = const.get_editor_log_path()
            handler = logging.FileHandler(log_path, encoding='cp932')
        except Exception as file_lbl_e:
            # 万が一ファイルハンドラが作れなかった場合はストリームへエスケープ
            sys.stderr.write(f"[WARNING] ファイルロガーの確立に失敗。標準出力へ切り替えます: {file_lbl_e}\n")
            handler = logging.StreamHandler()
                  
        
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger
    
    
    def create_widgets(self):
        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True)
        
        left_f = ttk.Frame(main_pane)
        main_pane.add(left_f, weight=0)
        
        self.nb = ttk.Notebook(left_f)
        self.nb.pack(fill=tk.BOTH, expand=True)
        
        def _add_scrollable_tab(nb_obj, text):
            tab = ttk.Frame(nb_obj)
            nb_obj.add(tab, text=text)
            canv = tk.Canvas(tab, width=720)
            sb = ttk.Scrollbar(tab, orient="vertical", command=canv.yview)
            inner = ttk.Frame(canv, padding=(2, 2, 2, 2))
            inner.bind("<Configure>", lambda e: canv.configure(scrollregion=canv.bbox("all")))
            canv.create_window((0, 0), window=inner, anchor="nw")
            canv.configure(yscrollcommand=sb.set)
            canv.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            return inner

        self.tab1_inner = _add_scrollable_tab(self.nb, "基本設定") 
        self.tab1_2_inner = _add_scrollable_tab(self.nb, "NSIPSアサイン")
        self.tab2_inner = _add_scrollable_tab(self.nb, "レイアウト・詳細設定")
        self.tab3_inner = _add_scrollable_tab(self.nb, "システム設定")
        
        # --- タブ1: 基本設定（動作環境設定へ特化） ---
        self.scroll_frame = self.tab1_inner
        
        # --- システム表示名称設定（ユーザー自由変更用） ---
        self.add_section("【システム表示名称】")
        desc_sysname = " 監視プログラムや設定ツールのウインドウ、ログに表示されるシステム全体の名称を設定します。"
        ttk.Label(self.scroll_frame, text=desc_sysname, foreground="gray30", justify=tk.LEFT, padding=(5, 2)).pack(anchor="w")
        
        row_sysname = ttk.Frame(self.scroll_frame); row_sysname.pack(fill="x", pady=2)
        self.systemname_entry = self.add_e(row_sysname, "システム名称:", "領収明細票（薬局控）", 30)
        # --- 1-1-B. 複数台PC展開時における『Bill』フォルダ集約・隔離 ---
        self.add_section("【数台を用いてネットワーク運用とする場合のフォルダ配置の鉄則】")
        bill_desc = (
            " ・ ワークフォルダ（work_dir）やPDF出力先は、各PCの足元（ローカル）に自動で完全隔離されています。\n"
            "    これらは複数台のPCで『共有（使い回し）をしないでください』。\n"
            "    ファイルの奪い合いが発生してシステムがクラッシュする原因になります。\n\n"
            " ・ 【自動救修フェイルセーフ機構】\n"
            "    root_path.txtで共有フォルダが指定されていても、ネットワーク切断やファイルの欠落により\n"
            "    共有INIが読み込めない場合は、自動的に『各PCローカルのINI』を参照してシステムを安全に起動します。\n"
            "    （監視ファイルをメイン機に置き、再発行ツールを各端末に置く運用を行う場合にのみ、root_pathの指定が必要です。）"
        )
        ttk.Label(self.scroll_frame, text=bill_desc, foreground="gray25", justify=tk.LEFT, padding=(5, 2)).pack(anchor="w", fill="x")

        # --- 1-1-C. 現在の稼働モードを画面に明記するステータスパネル ---
        self.add_section("【現在の稼働ステータス】")
        status_frame = ttk.Frame(self.scroll_frame, padding=5)
        status_frame.pack(fill="x", anchor="w")
        if const.IS_NETWORK_MODE:
            status_lbl = "● ネットワーク共有モードで稼働中 （参照先: " + const.BASE_DIR + "）"
            status_color = "#006600" # 安全なグリーン
        else:
            status_lbl = "● ローカル単独モードで稼働中 （共有INIが見つからないため足元の設定を参照しています）"
            status_color = "#CC3300" # 警告のオレンジ/レッド
            
        ttk.Label(status_frame, text=status_lbl, font=("", 10, "bold"), foreground=status_color).pack(anchor="w")

        self.add_section("基本・ディレクトリ設定")
        self.watchdir = self.add_dir_row("監視Dir:")
        self.indexdir = self.add_dir_row("IndexDir:")
        self.facilitydir = self.add_dir_row("除外CSV Dir:")
        self.logdir = self.add_dir_row("ログ出力 Dir:")
        
        # --- 保持日設定（注意喚起付き） ---
        row_days = ttk.Frame(self.scroll_frame); row_days.pack(fill="x", pady=(4, 0))
        self.targetdays = self.add_e(row_days, "処方データ（再発行用）の保持日数:", "365", 4)
        ttk.Label(self.scroll_frame, text="  ※jsonの肥大化はシステム全体の重さに繋がります。出来るだけ保持日を最小にすることをお勧めします。", foreground="gray40").pack(anchor="w", pady=(0, 6))
        
        # --- 除外ID列設定（0始まりへの仕様変更 ＆ 注意喚起付き） ---
        row_csv_col = ttk.Frame(self.scroll_frame); row_csv_col.pack(fill="x", pady=(4, 0))
        self.csvpatientidcolumn = self.add_e(row_csv_col, "除外ID列(0始):", "1", 4)
        lbl_csv_desc = (
            "  ※除外CSV先頭項のタイトル捜尋には対応していません。該当する列番号を指定下さい。\n"
            "    次ページの患者ID列のNSIPSアサインと位置を合わせてください。"
        )
        ttk.Label(self.scroll_frame, text=lbl_csv_desc, foreground="gray40", justify=tk.LEFT).pack(anchor="w", pady=(0, 6))
        
        # --- メンテナンス時刻設定（解説付き） ---
        row_maint = ttk.Frame(self.scroll_frame); row_maint.pack(fill="x", pady=(4, 0))
        self.maintenancetime = self.add_e(row_maint, "メンテ時刻:", "0", 5)
        lbl_maint_desc = (
            "  ※稼働中において、メンテナンスを希望する場合は入れてください（昼休み中など）。\n"
            "    通常は 0（起動時に一発実行）。指定する場合は時分を4桁（例: 1300）で記入します。"
        )
        ttk.Label(self.scroll_frame, text=lbl_maint_desc, foreground="gray40", justify=tk.LEFT).pack(anchor="w", pady=(0, 10))
        self.add_action_buttons()
        
        # --- タブ1_2: NSIPSアサイン ---
        self.scroll_frame = self.tab1_2_inner
        self.add_section("【NSIPSデータ構造の基本ルール】")
        rule_txt = (
            "NSIPSのCSVファイルは、先頭の数字（0～7）によって行ごとの役割が決められています。\n"
            "各行の中身はカンマ（,）で区切られており、左端から「0, 1, 2...」と数えた位置（Index）で項目を指定します。\n\n"
            " ・ 行番号0：薬局の情報 \n"
            " ・ 行番号1：患者・保険情報\n"
            " ・ 行番号2：医療機関・処方基本情報\n"
            " ・ 行番号3：処方内容・用法\n"
            " ・ 行番号4：調剤・薬剤詳細情報\n"
            " ・ 行番号5：金額・会計情報\n"
            " ・ 行番号6：調剤料算定情報\n"
            " ・ 行番号7：基本料算定情報\n"
        )
        ttk.Label(self.scroll_frame, text=rule_txt, foreground="gray25", justify=tk.LEFT, padding=(5, 2)).pack(anchor="w", fill="x")
        
        # 1-2-B. 自費項目の判定仕様（完全自動・固定制御のため入力欄は非表示）
        self.add_section("【1行目レコード】仕様")
        desc_r2 = (
            " 【システムによる自費／保険の区分け仕様】\n"
            "　index 19 を参照し判定してます。\n"
            " Index=19(保険割合）が『100』となっているか否かだけをチェックして、\n"
            " 自費100%の処方箋であるかを自動判定しています。\n\n"
            " 【出力先の固定仕様】\n"
            " 『レイアウト・詳細設定タブ』の【印字項目設定】の上から数えて「４番目」に自費項目を割り当てています。\n"
            " ※この割り当て（4番目）を基準に、保険調剤時の自費マスクや、自費処方時の全額集約を完全自動で行います。\n"
            "    誤設定によるシステムクラッシュを防止するため、この項目はユーザーによる変更ができない固定仕様となっています。\n\n"
            " 【患者名の取得】\n"
            " index 5 を参照しています。\n"
            " 使用範囲は印刷・閲覧のみとなり、このシステムでは患者情報の収集・データベース化をしません。\n\n"
            " 【患者IDの取得】\n"
            " index 3 を参照しています。\n"
            "  使用範囲はfacilityデータとの照合のみとし、このシステムでは情報を収集しません。\n\n"
            " 【薬局・処方箋医療機関の項】\n"
            " このシステムでは、これらの値を収集しません。\n"
        )
        ttk.Label(self.scroll_frame, text=desc_r2, foreground="gray30", justify=tk.LEFT, padding=(5, 2)).pack(anchor="w")
        row_nsips_r2 = ttk.Frame(self.scroll_frame); row_nsips_r2.pack(fill="x", pady=2)

        # 1-2-C. 4行目設定（完全自動集計の解説へ昇華）
        self.add_section("【4行目レコード】調剤・特定品目詳細の自動探索仕様")
        desc_r4 = (
            " 4行目の薬剤・器材情報から、容器代やその他保険外自費の金額をシステムがバックグラウンドで自動抽出しています。\n"
            " ・[品目区分] index=3 の値から、容器（値:3）などの保険外項目を自動特定します。\n"
            " ・[計算ロジック] index=24（単価）に消費税1.1を掛け、index=16（数量）を掛け合わせて金額を自動集計します。\n"
            " ※この処理はプログラムが完全自動で行うため、列番号（Index）を指定する入力欄は必要ありません。"
        )
        ttk.Label(self.scroll_frame, text=desc_r4, foreground="gray30", justify=tk.LEFT, padding=(5, 2)).pack(anchor="w")
 
        # 1-2-D. 5行目設定
        self.add_section("【5行目レコード】金額・会計情報データ位置設定（0始まり）")
        desc_r5 = "会計情報のカンマ区切りの位置を指定します。レセコンのメーカーによって出力位置が異なる場合があります。\n※患者様に渡る「領収書（原本）」の金額の項目を見比べながら、対応する各項目のIndex（列番号）を特定してください。"
        ttk.Label(self.scroll_frame, text=desc_r5, foreground="gray30", justify=tk.LEFT, padding=(5, 2)).pack(anchor="w")
        
        row_nsips_r5_base = ttk.Frame(self.scroll_frame); row_nsips_r5_base.pack(fill="x", pady=2)
        self.totalamountcolumn = self.add_e(row_nsips_r5_base, "精算総額の参照列Index:", "19", 3)
        self.nsips_sentei = self.add_e(row_nsips_r5_base, "選定療養費位置Index:", "15", 4)
        
        row_nsips_r5_amt1 = ttk.Frame(self.scroll_frame); row_nsips_r5_amt1.pack(fill="x", pady=2)
        self.nsips_presc = self.add_e(row_nsips_r5_amt1, "今回処方額:", "17", 4)
        self.nsips_prev_unpaid = self.add_e(row_nsips_r5_amt1, "前回未収金:", "16", 4)
        self.nsips_already_paid = self.add_e(row_nsips_r5_amt1, "前回領収済:", "18", 4)
        
        row_nsips_r5_amt2 = ttk.Frame(self.scroll_frame); row_nsips_r5_amt2.pack(fill="x", pady=2)
        self.nsips_receipt = self.add_e(row_nsips_r5_amt2, "領収金額:", "19", 4)
        self.nsips_curr_unpaid = self.add_e(row_nsips_r5_amt2, "今回未収金:", "20", 4)
 
        self.add_action_buttons()

        # --- タブ2: レイアウト ---
        self.scroll_frame = self.tab2_inner
        self.add_section("印字項目設定")
        self.items_vars = []
        for i in range(6):
            r = ttk.Frame(self.scroll_frame); r.pack(fill="x", pady=0)
            st = tk.IntVar(value=1); tk.Checkbutton(r, variable=st).pack(side=tk.LEFT)
            vs = [tk.StringVar() for _ in range(7)]
            for idx, w in zip([0, 1, 2, 5, 6], [14, 4, 6, 5, 5]):
                # 80mm切替時にアイテム座標エントリを直接操作できるように参照紐付け
                e = ttk.Entry(r, textvariable=vs[idx], width=w)
                e.pack(side=tk.LEFT, padx=1)
                vs[idx]._entry = e
            self.items_vars.append({'status': st, 'vars': vs})

        self.add_section("共通レイアウト (X / Y / Size)")
        self.title_x, self.title_y, self.title_fontsize = self.add_3("タイトル:")
        self.revlabel_x, self.revlabel_y, self.revlabel_fontsize = self.add_3("新規更新ラベル:")
        self.printdate_x, self.printdate_y, self.printdate_fontsize = self.add_3("印刷日時:")
        self.date_x, self.date_y, self.dispdate_fontsize = self.add_3("来局日ラベル:")
        self.patient_x, self.patient_y, self.patient_fontsize = self.add_3("患者氏名:")
        self.totalamount_x, self.totalamount_y, self.totalamount_fontsize = self.add_3("精算額数値:")
        self.totallabel_x, self.totallabel_y_offset = self.add_2("合計ラベル X/Yオフセット:")
        self.qr_x, self.qr_y, self.qr_size = self.add_3("メインQR X/Y/S:")
        self.refundqr_x, self.refundqr_y, self.refundqr_size = self.add_3("返金専用QR X/Y/S:")
        self.burdenrate_x, self.burdenrate_y, self.burdenrate_fontsize = self.add_3("負担割合ラベル:")
        self.notice_x, self.notice_y, self.notice_fontsize = self.add_3("注釈1 (全体):")
        self.notice2_x, self.notice2_y, self.notice2_fontsize = self.add_3("注釈2 (請求QR):")
        self.notice3_x, self.notice3_y, self.notice3_fontsize = self.add_3("注釈3 (返金QR):")

        self.add_section("項目内レイアウト")
        self.item_name_fontsize = self.add_1("項目名称サイズ:")
        self.item_barcode_y_offset, self.item_barcode_w, self.item_barcode_h = self.add_3("バーコード Yオフ/W/H:")
        self.item_amount_x_offset, self.item_amount_y_offset, self.item_amount_fontsize = self.add_3("数値 Xオフ/Yオフ/S:")
        self.item_yen_x_offset, self.item_yen_y_offset, self.item_yen_fontsize = self.add_3("円単位 Xオフ/Yオフ/S:")
        self.item_line_y_offset = self.add_1("区切り線 Yオフ:")

        self.add_section("BOX枠 & 記号・ディレイ設定")
        
        row_fs = ttk.Frame(self.scroll_frame); row_fs.pack(fill="x", pady=2)
        self.boxlabel_fontsize = self.add_e(row_fs, "ラベル:", "28", 3)
        self.boxval_fontsize = self.add_e(row_fs, "共通値:", "70", 3)
        self.boxdateval_fontsize = self.add_e(row_fs, "日付値:", "70", 3)
        self.boxnoval_fontsize = self.add_e(row_fs, "番号値:", "90", 3)
        self.boxrcptval_fontsize = self.add_e(row_fs, "金額値:", "70", 3)
        
        self.box_x, self.box_y, self.box_w, self.box_h = self.add_4("外枠BOX X/Y/W/H:")
        self.boxdatelabel_x, self.boxdatelabel_y, self.boxdateval_x, self.boxdateval_y = self.add_4("日付ラベル/値 X/Y:")
        self.boxnolabel_x, self.boxnolabel_y, self.boxnoval_x, self.boxnoval_y = self.add_4("番号ラベル/値 X/Y:")
        self.boxrcptlabel_x, self.boxrcptlabel_y, self.boxrcptval_x, self.boxrcptval_y = self.add_4("受付金額ラベル/値 X/Y:")

        self.add_section("会計エビデンス項目 ( 位置)")
        self.adj_x, self.adj_y_base, self.adj_l_h = self.add_3("印字 X / Y基点 / 行間:")
        self.adj_main_fs, self.adj_sub_fs = self.add_2("文字サイズ 主 / 副:")

        self.add_section("罫線設定")
        self.line1_startx, self.line1_endx, self.line1_y = self.add_3("Line1 始/終/Y:")
        self.line2_startx, self.line2_endx, self.line2_y = self.add_3("Line2 始/終/Y:")
        self.line3_startx, self.line3_endx, self.line3_y = self.add_3("Line3 始/終/Y:")
        
        self.add_action_buttons()
        
        # --- タブ3: システム設定 ---
        self.scroll_frame = self.tab3_inner
        self.add_section("システム設定")
        self.printername = self.add_printer_row("使用プリンタ:")

        row_op_mode = ttk.Frame(self.scroll_frame)
        row_op_mode.pack(fill="x", pady=4)
        ttk.Label(row_op_mode, text="システム運用モード:", width=18).pack(side=tk.LEFT)
        
        self.opmode = ttk.Combobox(row_op_mode, values=[
            "1. A6用紙：1D並列モード (現行互換)",
            "2. A6用紙：2D一本化モード (QR仕様)",
            "3. 80mmロール紙：2D一本化モード (レシート仕様)"
        ], width=45, state="readonly")
        self.opmode.set("1. A6用紙：1D並列モード (現行互換)")
        self.opmode.pack(side=tk.LEFT, padx=2)
        self.opmode.bind("<<ComboboxSelected>>", self.on_opmode_changed)
        self.scalerate = self.add_1("倍率(A6基点):")
        
        # 0円非表示チェックボックス
        row_sys_opt1 = ttk.Frame(self.scroll_frame)
        row_sys_opt1.pack(fill="x", pady=5)
        tk.Checkbutton(row_sys_opt1, text="金額が0円の項目は印字しない（伝票を短くする）", 
                       variable=self.hide_zero_items_var, command=self.update_preview).pack(anchor="w", padx=5)
        
        # ログ保存日数（ここをFrameで囲むことで、他の入力欄と列が揃います）
        row_sys_opt2 = ttk.Frame(self.scroll_frame)
        row_sys_opt2.pack(fill="x", pady=5)
        self.log_retention_days = self.add_e(row_sys_opt2, "システム稼働ログの保存日数:", "365", 5)
        # ----------------------------------------------------------------------
        # バーコード出力モードのコンボボックスを新設配置
        # ----------------------------------------------------------------------
        row_bc_mode = ttk.Frame(self.scroll_frame)
        row_bc_mode.pack(fill="x", pady=4)
        ttk.Label(row_bc_mode, text="バーコード出力モード:", width=18).pack(side=tk.LEFT)
        self.barcode_mode_combo = ttk.Combobox(
            row_bc_mode, 
            textvariable=self.barcode_mode_var, 
            values=["2D", "1D", "BOTH"], 
            state="readonly",
            width=15
        )
        self.barcode_mode_combo.pack(side=tk.LEFT, padx=2)
        # 以下の1行をピンポイントで追記し、80mmのモード変更を即座にプレビューに叩き込む
        self.barcode_mode_combo.bind("<<ComboboxSelected>>", lambda e: self.update_preview())
        ttk.Label(row_bc_mode, text="※過渡期レジ・スキャナ対応用。BOTH＝1D+2Dの同時出力", foreground="gray40").pack(side=tk.LEFT, padx=5)
        self.noticetext = self.add_path_row("注釈1 (全体内容):")
        self.noticetext2 = self.add_path_row("注釈2 (請求QR内容):")
        self.noticetext3 = self.add_path_row("注釈3 (返金QR内容):")
        r_1d = ttk.Frame(self.scroll_frame); r_1d.pack(fill="x", pady=2)
        self.chk_1d = tk.Checkbutton(r_1d, text="1次元バーコードを印字する", variable=self.print_1d_barcode_var, command=self.update_preview)
        self.chk_1d.pack(side=tk.LEFT)
        
        self.noticetext4 = self.add_path_row("注釈4 (返金QR上段):")
        self.noticetext5 = self.add_path_row("注釈5 (返金QR下段):")
        
        btn_f = ttk.Frame(self.scroll_frame); btn_f.pack(fill="x", pady=10)
        
        self.add_section("【QRディレイ・改行 ＆ 会計ラベル詳細設定】")
        delay_symbols = ["none", "|", ",", "_", "SPACE_5", "SPACE_10", "SPACE_50", "SPACE_100"]
        r_db = ttk.Frame(self.scroll_frame); r_db.pack(fill="x", pady=2)
        ttk.Label(r_db, text="請求QRディレイ:", width=18).pack(side=tk.LEFT)
        self.enable_delay_billing = tk.IntVar(); tk.Checkbutton(r_db, variable=self.enable_delay_billing).pack(side=tk.LEFT)
        self.delay_symbol_billing = tk.StringVar(); ttk.Combobox(r_db, textvariable=self.delay_symbol_billing, values=delay_symbols, state="readonly", width=10).pack(side=tk.LEFT, padx=5)
        
        r_dr = ttk.Frame(self.scroll_frame); r_dr.pack(fill="x", pady=2)
        ttk.Label(r_dr, text="返金QRディレイ:", width=18).pack(side=tk.LEFT)
        self.enable_delay_refund = tk.IntVar(); tk.Checkbutton(r_dr, variable=self.enable_delay_refund).pack(side=tk.LEFT)
        self.delay_symbol_refund = tk.StringVar(); ttk.Combobox(r_dr, textvariable=self.delay_symbol_refund, values=delay_symbols, state="readonly", width=10).pack(side=tk.LEFT, padx=5)

        r_br = ttk.Frame(self.scroll_frame); r_br.pack(fill="x", pady=2)
        ttk.Label(r_br, text="QR改行記号:", width=18).pack(side=tk.LEFT)
        self.qrlinebreak = tk.StringVar(); ttk.Combobox(r_br, textvariable=self.qrlinebreak, values=['CRLF', 'CR', 'LF', 'non'], state="readonly", width=10).pack(side=tk.LEFT, padx=5)

        self.presc_label = self.add_path_row("請求金額ラベル:")
        self.paid_label = self.add_path_row("前回領収済ラベル:")
        self.prevunpaid_label = self.add_path_row("前回未収金ラベル:")
        self.currunpaid_label = self.add_path_row("今回未収金ラベル:")
        self.add_action_buttons()
        
        right_f = ttk.Frame(main_pane, padding=2)
        main_pane.add(right_f, weight=1)
        self.preview_label = tk.Label(right_f, bg="gray90", relief="sunken")
        self.preview_label.pack(fill="both", expand=True)

    # --- 共通UI生成メソッド群（Entry実体の安全参照化） ---
    def add_section(self, t):
        ttk.Label(self.scroll_frame, text=t, font=("", 9, "bold"), foreground="#003366").pack(anchor="w", pady=(8, 1))
        
    def add_dir_row(self, l):
        r = ttk.Frame(self.scroll_frame); r.pack(fill="x", pady=0)
        ttk.Label(r, text=l, width=16).pack(side=tk.LEFT)
        v = tk.StringVar(); ttk.Entry(r, textvariable=v).pack(side=tk.LEFT, fill="x", expand=True)
        ttk.Button(r, text="参照", command=lambda: v.set(os.path.normpath(filedialog.askdirectory() or v.get()))).pack(side=tk.LEFT, padx=2)
        return v
    def add_path_row(self, l):
        r = ttk.Frame(self.scroll_frame); r.pack(fill="x", pady=0)
        ttk.Label(r, text=l, width=16).pack(side=tk.LEFT)
        v = tk.StringVar(); ttk.Entry(r, textvariable=v).pack(side=tk.LEFT, fill="x", expand=True)
        return v
    def add_printer_row(self, l):
        r = ttk.Frame(self.scroll_frame); r.pack(fill="x", pady=0)
        ttk.Label(r, text=l, width=16).pack(side=tk.LEFT)
        v = tk.StringVar(); p_list = ["non"]
        try:
            p_enum = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 1)
            for p in p_enum: p_list.append(p[2])
        except Exception as e:
            # RPC不可など、OS起因のエラーの全容をフライトレコーダーに記録
            self.logger.error(f"[ERROR] プリンタ一覧の取得に失敗しました: {e}", exc_info=True)
        ttk.Combobox(r, textvariable=v, values=p_list, state="readonly").pack(side=tk.LEFT, fill="x", expand=True)
        return v
    def add_e(self, p, l, d, w):
        # ラベルの width を 32 に固定（または長さに合わせる）することで、
        # 「処方データ（再発行用）の保持日数:」などの長い文字列でも、右側の入力ボックスの左端が綺麗に揃います
        lbl = ttk.Label(p, text=l, width=32, anchor="w")
        lbl.pack(side=tk.LEFT, padx=(4, 1))
        v = tk.StringVar(value=d)
        e = ttk.Entry(p, textvariable=v, width=w)
        e.pack(side=tk.LEFT)
        v._entry = e  # ★実体参照のshadow_copy
        return v
        return v
    def add_1(self, l):
        r = ttk.Frame(self.scroll_frame); r.pack(fill="x", pady=0)
        ttk.Label(r, text=l, width=24).pack(side=tk.LEFT)
        v = tk.StringVar()
        e = ttk.Entry(r, textvariable=v, width=7); e.pack(side=tk.LEFT)
        v._entry = e  # ★実体参照のshadow_copy
        return v
    def add_2(self, l):
        r = ttk.Frame(self.scroll_frame); r.pack(fill="x", pady=0)
        ttk.Label(r, text=l, width=24).pack(side=tk.LEFT)
        v1, v2 = tk.StringVar(), tk.StringVar()
        e1 = ttk.Entry(r, textvariable=v1, width=7); e1.pack(side=tk.LEFT, padx=1)
        e2 = ttk.Entry(r, textvariable=v2, width=7); e2.pack(side=tk.LEFT, padx=1)
        v1._entry = e1; v2._entry = e2  # ★実体参照のshadow_copy
        return v1, v2
    def add_3(self, l):
        r = ttk.Frame(self.scroll_frame); r.pack(fill="x", pady=0)
        ttk.Label(r, text=l, width=24).pack(side=tk.LEFT)
        v1, v2, v3 = tk.StringVar(), tk.StringVar(), tk.StringVar()
        e1 = ttk.Entry(r, textvariable=v1, width=7); e1.pack(side=tk.LEFT, padx=1)
        e2 = ttk.Entry(r, textvariable=v2, width=7); e2.pack(side=tk.LEFT, padx=1)
        e3 = ttk.Entry(r, textvariable=v3, width=7); e3.pack(side=tk.LEFT, padx=1)
        v1._entry = e1; v2._entry = e2; v3._entry = e3  # ★実体参照のshadow_copy
        return v1, v2, v3
    def add_4(self, l):
        r = ttk.Frame(self.scroll_frame); r.pack(fill="x", pady=0)
        ttk.Label(r, text=l, width=24).pack(side=tk.LEFT)
        v1, v2, v3, v4 = tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()
        e1 = ttk.Entry(r, textvariable=v1, width=7); e1.pack(side=tk.LEFT, padx=1)
        e2 = ttk.Entry(r, textvariable=v2, width=7); e2.pack(side=tk.LEFT, padx=1)
        e3 = ttk.Entry(r, textvariable=v3, width=7); e3.pack(side=tk.LEFT, padx=1)
        e4 = ttk.Entry(r, textvariable=v4, width=7); e4.pack(side=tk.LEFT, padx=1)
        v1._entry = e1; v2._entry = e2; v3._entry = e3; v4._entry = e4  # ★実体参照のshadow_copy
        return v1, v2, v3, v4
        
    def add_action_buttons(self):
        btn_f = ttk.Frame(self.scroll_frame); btn_f.pack(fill="x", pady=10)
        ttk.Button(btn_f, text="プレビュー更新", command=self.update_preview).pack(side=tk.LEFT, expand=True, fill="x", padx=2)
        ttk.Button(btn_f, text="設定を保存", command=self.save_settings).pack(side=tk.LEFT, expand=True, fill="x", padx=2)

    def validate_inputs(self):
        try:
            int(self.log_retention_days.get()); int(self.nsips_presc.get()); int(self.nsips_prev_unpaid.get())
            int(self.nsips_already_paid.get()); int(self.nsips_receipt.get()); int(self.nsips_curr_unpaid.get())
            int(self.csvpatientidcolumn.get())
            float(self.scalerate.get()); return True
        except ValueError:
            messagebox.showerror("入力エラー", "Index指定や倍率には半角数値を入力してください。")
            return False

    def save_settings(self):
        if not self.validate_inputs(): return
        try:
            c = self.config
            for s in ["Paths", "Printer", "Settings", "PrintItems", "NSIPS_Assign", "Layout_A6", "ReceiptLabels"]:
                if not c.has_section(s): c.add_section(s)
            c.set("ReceiptLabels", "presc_label", self.presc_label.get().strip())
            c.set("ReceiptLabels", "paid_label", self.paid_label.get().strip())
            c.set("ReceiptLabels", "prev_unpaid_label", self.prevunpaid_label.get().strip())
            c.set("ReceiptLabels", "curr_unpaid_label", self.currunpaid_label.get().strip())
            
            c.set("NSIPS_Assign", "presc", self.nsips_presc.get().strip())
            c.set("NSIPS_Assign", "prev_unpaid", self.nsips_prev_unpaid.get().strip())
            c.set("NSIPS_Assign", "already_paid", self.nsips_already_paid.get().strip())
            c.set("NSIPS_Assign", "receipt", self.nsips_receipt.get().strip())
            c.set("NSIPS_Assign", "curr_unpaid", self.nsips_curr_unpaid.get().strip())
            c.set("NSIPS_Assign", "sentei", self.nsips_sentei.get().strip())

            c.set("Settings", "EnableDelayBilling", str(self.enable_delay_billing.get()))
            c.set("Settings", "DelaySymbolBilling", self.delay_symbol_billing.get())
            c.set("Settings", "EnableDelayRefund", str(self.enable_delay_refund.get()))
            c.set("Settings", "DelaySymbolRefund", self.delay_symbol_refund.get())
            c.set("Settings", "NoticeText", self.noticetext.get().strip())
            c.set("Settings", "QrLineBreak", self.qrlinebreak.get())
            c.set("Settings", "NoticeText2", self.noticetext2.get().strip())
            c.set("Settings", "NoticeText3", self.noticetext3.get().strip())
            # 画面上の選択結果をSettingsおよびPrinterセクションへ型セーフ保存
            c.set("Settings", "Print1DBarcode", str(self.print_1d_barcode_var.get()))
            c.set("Printer", "BarcodeMode", self.barcode_mode_var.get())
            c.set("Settings", "NoticeText4", self.noticetext4.get().strip())
            c.set("Settings", "NoticeText5", self.noticetext5.get().strip())
            c.set("Settings", "CsvPatientIdColumn", self.csvpatientidcolumn.get().strip())
            c.set("NSIPS_Assign", "TotalAmountColumn", self.totalamountcolumn.get().strip())

            c.set("Paths", "WatchDir", self.watchdir.get().strip())
            c.set("Paths", "IndexDir", self.indexdir.get().strip())
            c.set("Paths", "FacilityDir", self.facilitydir.get().strip())
            c.set("Paths", "LogDir", self.logdir.get().strip())
            c.set("Settings", "TargetDays", self.targetdays.get().strip())
            c.set("Settings", "MaintenanceTime", self.maintenancetime.get().strip())
            c.set("Printer", "PrinterName", self.printername.get().strip())
            c.set("Printer", "ScaleRate", self.scalerate.get().strip())
            c.set("Settings", "LogRetentionDays", self.log_retention_days.get().strip())
            sel_mode = self.opmode.get()
            if "80mm" in sel_mode:
                c.set("Printer", "PaperSize", "80mm")
                c.set("Printer", "BarcodeMode", self.barcode_mode_var.get()) # ★画面の選択(BOTHや1D)をそのまま活かす
                c.set("Printer", "OperationMode", "80mm_2D")
                sec = "Layout_80mm"
            elif "2D" in sel_mode:
                c.set("Printer", "PaperSize", "A6")
                c.set("Printer", "BarcodeMode", self.barcode_mode_var.get())
                c.set("Printer", "OperationMode", "A6_2D")
                sec = "Layout_A6"
            else:
                c.set("Printer", "PaperSize", "A6")
                c.set("Printer", "BarcodeMode", self.barcode_mode_var.get())
                c.set("Printer", "OperationMode", "A6_1D")
                sec = "Layout_A6"
                
            if not c.has_section("Common"):
                c.add_section("Common")
            c.set("Common", "SystemName", self.systemname_entry.get().strip())
            # INIファイルへ保存
            c.set("Common", "HideZeroAmountItems", str(self.hide_zero_items_var.get()))
            
            for i, it in enumerate(self.items_vars):
                parts = [v.get().strip() for v in it['vars']]
                parts.append(str(it['status'].get()))
                c.set("PrintItems", f"Item{i+1}", ", ".join(parts))
                
            # --- 確定した sec の砦セクションに対して、独立した注釈を書き込む ---
            if not c.has_section(sec): c.add_section(sec)
            c.set(sec, "NoticeText", self.noticetext.get().strip())
            c.set(sec, "NoticeText2", self.noticetext2.get().strip())
            c.set(sec, "NoticeText3", self.noticetext3.get().strip())
            c.set(sec, "NoticeText4", self.noticetext4.get().strip())
            c.set(sec, "NoticeText5", self.noticetext5.get().strip())

            
            l_keys = ["Title_X", "Title_Y", "Title_FontSize", "RevLabel_X", "RevLabel_Y", "RevLabel_FontSize", 
                      "PrintDate_X", "PrintDate_Y", "PrintDate_FontSize", "Date_X", "Date_Y", "DispDate_FontSize", 
                      "Patient_X", "Patient_Y", "Patient_FontSize", "TotalAmount_X", "TotalAmount_Y", "TotalAmount_FontSize", 
                      "TotalLabel_X", "TotalLabel_Y_Offset", "Notice_X", "Notice_Y", "Notice_FontSize", 
                      "Notice2_X", "Notice2_Y", "Notice2_FontSize", "Notice3_X", "Notice3_Y", "Notice3_FontSize",
                      "QR_X", "QR_Y", "QR_Size", "Item_Barcode_Y_Offset", "Item_Barcode_W", "Item_Barcode_H", 
                      "Item_Amount_X_Offset", "Item_Amount_Y_Offset", "Item_Amount_FontSize", "Item_Yen_X_Offset", 
                      "Item_Yen_Y_Offset", "Item_Yen_FontSize", "Item_Name_FontSize", "Item_Line_Y_Offset", 
                      "Box_X", "Box_Y", "Box_W", "Box_H", "BoxLabel_FontSize", "BoxVal_FontSize", 
                      "BoxDateVal_FontSize", "BoxNoVal_FontSize", "BoxRcptVal_FontSize",
                      "BoxDateLabel_X", "BoxDateLabel_Y", "BoxDateVal_X", "BoxDateVal_Y", 
                      "BoxNoLabel_X", "BoxNoLabel_Y", "BoxNoVal_X", "BoxNoVal_Y", 
                      "BoxRcptLabel_X", "BoxRcptLabel_Y", "BoxRcptVal_X", "BoxRcptVal_Y",
                      "Adj_X", "Adj_Y_Base", "Adj_L_H",
                      "Adj_Main_FS", "Adj_Sub_FS",
                      "Line1_StartX", "Line1_EndX", "Line1_Y", "Line2_StartX", "Line2_EndX", "Line2_Y", "Line3_StartX", 
                      "Line3_EndX", "Line3_Y", "RefundQR_X", "RefundQR_Y", "RefundQR_Size", "BurdenRate_X", 
                      "BurdenRate_Y", "BurdenRate_FontSize"]
            
            for k in l_keys: c.set(sec, k, getattr(self, k.lower()).get().strip())
            with open(self.ini_file, 'w', encoding='cp932') as f:
                c.write(f)
            self.logger.info(f"[INFO] 設定を保存しました。 (Version: {const.CURRENT_VERSION})")
            messagebox.showinfo("完了", "設定を保存しました。")
        except Exception as e:
            self.logger.critical(f"[CRITICAL] INIファイルの保存に失敗しました:\n{traceback.format_exc()}")
            messagebox.showerror("保存エラー", f"ファイルの書き込みに失敗しました。\nエラー: {e}")

    def update_preview(self):
        """右側のプレビュー画像を設定値に基づいてリアルタイムに再描画する"""
        try:
            # 1. 画面上の最新入力値（実際のUI変数名に完全同期）から一時的な設定辞書をモック生成
            mock_conf = {}
            mock_conf['systemname'] = self.systemname_entry.get().strip()
            mock_conf['watch_dir'] = self.watchdir.get().strip()
            mock_conf['index_dir'] = self.indexdir.get().strip()
            mock_conf['facility_dir'] = self.facilitydir.get().strip()
            mock_conf['log_dir'] = self.logdir.get().strip()
            
            mock_conf['target_days'] = int(self.targetdays.get() or "365")
            mock_conf['csv_col'] = int(self.csvpatientidcolumn.get() or "2")
            mock_conf['maint_time'] = self.maintenancetime.get().strip()
            mock_conf['notice_text'] = self.noticetext.get().strip()
            mock_conf['notice_text2'] = self.noticetext2.get().strip()
            mock_conf['notice_text3'] = self.noticetext3.get().strip()
            mock_conf['notice_text4'] = self.noticetext4.get().strip()
            mock_conf['notice_text5'] = self.noticetext5.get().strip()
            
            
            # プレビュー用モックに0円非表示フラグを渡す
            if 'Common' not in mock_conf:
                mock_conf['Common'] = {}
            mock_conf['Common']['HideZeroAmountItems'] = str(self.hide_zero_items_var.get())
            
            # 【A6連動の核心】画面上の1Dバーコード出力チェックボックスの状態を取得
            is_1d_checked = self.print_1d_barcode_var.get()
            mock_conf['print_1d_barcode'] = is_1d_checked 
            
            # 2. プリンタおよび運用モード設定まわり
            mock_conf['printer'] = self.printername.get().strip()
            mock_conf['scale_rate'] = float(self.scalerate.get() or "1.0")
            
            sel_mode = self.opmode.get() # 例: "1. A6用紙：1D並列モード..." など
            
            # 内部のレイアウト辞書を先行確保
            mock_conf['layout'] = {}
            mock_conf['Settings'] = {} # コンソール側がSettings階層を直読みするケースへの防弾

            # 【A6/80mm完全統合】チェックボックス縛りを廃止し、両モードともコンボボックス(1D/2D/BOTH)を強制適用
            b_mode = self.barcode_mode_var.get()
            mock_conf['barcode_mode'] = b_mode
            mock_conf['layout']['BarcodeMode'] = b_mode
            mock_conf['layout']['barcodemode'] = b_mode
            
            if "80mm" in sel_mode:
                mock_conf['paper_size'] = "80mm"
            else:
                mock_conf['paper_size'] = "A6"

            # コンソール側がどのキー名で探しに来ても確実に拾えるよう全方位マッピング
            chosen_line_break = self.qrlinebreak.get()
            mock_conf['qr_line_break'] = chosen_line_break
            mock_conf['QrLineBreak'] = chosen_line_break
            mock_conf['Settings']['QrLineBreak'] = chosen_line_break
            mock_conf['layout']['QrLineBreak'] = chosen_line_break
            mock_conf['layout']['qrlinebreak'] = chosen_line_break
            
            # 3. 印字項目設定（グリッドタブ）の有効項目を走査してパース
            mock_conf['print_items'] = []
            for it in self.items_vars:
                if it['status'].get() == 1:
                    v = it['vars']
                    mock_conf['print_items'].append({
                        'name': v[0].get().strip(), 'prefix': v[1].get().strip(), 'code': v[2].get().strip(),
                        'record_type': v[3].get().strip(), 'col_idx': int(v[4].get().strip() or 0),
                        'pos_x': int(v[5].get().strip() or 0), 'pos_y': int(v[6].get().strip() or 0), 'status': 1
                    })

            # 4. 共通レイアウト座標の一時モック化（getattrを使って画面から全キーを一括抽出し完全同期）
            mock_conf['layout'] = {}
            l_keys = ["Title_X", "Title_Y", "Title_FontSize", "RevLabel_X", "RevLabel_Y", "RevLabel_FontSize", 
                      "PrintDate_X", "PrintDate_Y", "PrintDate_FontSize", "Date_X", "Date_Y", "DispDate_FontSize", 
                      "Patient_X", "Patient_Y", "Patient_FontSize", "TotalAmount_X", "TotalAmount_Y", "TotalAmount_FontSize", 
                      "TotalLabel_X", "TotalLabel_Y_Offset", "Notice_X", "Notice_Y", "Notice_FontSize", 
                      "Notice2_X", "Notice2_Y", "Notice2_FontSize", "Notice3_X", "Notice3_Y", "Notice3_FontSize",
                      "QR_X", "QR_Y", "QR_Size", "Item_Barcode_Y_Offset", "Item_Barcode_W", "Item_Barcode_H", 
                      "Item_Amount_X_Offset", "Item_Amount_Y_Offset", "Item_Amount_FontSize", "Item_Yen_X_Offset", 
                      "Item_Yen_Y_Offset", "Item_Yen_FontSize", "Item_Name_FontSize", "Item_Line_Y_Offset", 
                      "Box_X", "Box_Y", "Box_W", "Box_H", "BoxLabel_FontSize", "BoxVal_FontSize", 
                      "BoxDateVal_FontSize", "BoxNoVal_FontSize", "BoxRcptVal_FontSize",
                      "BoxDateLabel_X", "BoxDateLabel_Y", "BoxDateVal_X", "BoxDateVal_Y", 
                      "BoxNoLabel_X", "BoxNoLabel_Y", "BoxNoVal_X", "BoxNoVal_Y", 
                      "BoxRcptLabel_X", "BoxRcptLabel_Y", "BoxRcptVal_X", "BoxRcptVal_Y",
                      "Adj_X", "Adj_Y_Base", "Adj_L_H", "Adj_Main_FS", "Adj_Sub_FS",
                      "Line1_StartX", "Line1_EndX", "Line1_Y", "Line2_StartX", "Line2_EndX", "Line2_Y", "Line3_StartX", 
                      "Line3_EndX", "Line3_Y", "RefundQR_X", "RefundQR_Y", "RefundQR_Size", "BurdenRate_X", 
                      "BurdenRate_Y", "BurdenRate_FontSize"]
            for k in l_keys:
                mock_conf['layout'][k] = getattr(self, k.lower()).get().strip()

            # 5. ダミーの処方データ(プレビュー用サンプル)を構築
            test_amts = {it['name']: (500 if "返金" in it['name'] else (1200 if i < 2 else 0)) for i, it in enumerate(mock_conf['print_items'])}

            dummy_data = {
                'patient_id': '99999',
                'patient_name': 'プレビュー 太郎',
                'dispensing_date': '2026/05/22',
                'burden_rate': 30,
                'total_amount': 3600,       # 精算総額
                'pure_presc': 3600,         # 請求金額
                'already_paid': 0,          # 前回領収済
                'prev_unpaid': 0,           # 前回未収金
                'curr_unpaid': 0,           # 今回未収金
                'rev_label': '新規',
                'receipt_no': '123',
                'filename': 'PREVIEW',
                'is_adjustment': True,       # エビデンスエリア確認用強制ON
                'is_refund_total': True,     # 返金注釈確認用強制ON
                'amounts': test_amts,        # 上で作ったコンパクトな辞書を渡す
                'amounts_disp': {k: f"{v:,}" for k, v in test_amts.items()}, # ★カンマ区切り文字列を自動生成
                'receipt_labels': {
                    'presc': self.presc_label.get().strip(),
                    'paid': self.paid_label.get().strip(),
                    'prev_unpaid': self.prevunpaid_label.get().strip(),
                    'curr_unpaid': self.currunpaid_label.get().strip()
                }
            }
            
            # 6. バックエンド描画ロジックをプレビューモード(is_preview=True)で呼び出し
            img = drawer.generate_pdf_logic(dummy_data, mock_conf, self.work_dir, None, None, is_preview=True)
            
            if img:
                # 7. 右側のプレビュー表示枠に合わせて縮小リサイズ (横720 × 縦950枠)
                img.thumbnail((720, 950), Image.Resampling.LANCZOS)
                
                # 8. Tkinter画像オブジェクトへ変換して、実際の配置先「self.preview_label」へ流し込み
                self.img_tk = ImageTk.PhotoImage(img)
                self.preview_label.config(image=self.img_tk)
                
        except Exception as e:
            # printをパージし、裏起動時でも確実にフライトレコーダー(ログ)に行数付きで刻む
            self.logger.error(f"[PREVIEW_ERROR] 描画処理・プレビュー生成でエラーが発生しました: {e}", exc_info=True)

    def load_settings(self):
        c = self.config
        
        def safe_load(target_var, section, key, fallback, is_int=False):
            try:
                val = c.get(section, key, fallback=str(fallback))
                target_var.set(int(val) if is_int else val)
            except Exception as load_e:
                # 何が原因（型変換エラーか不在か）でフォールバックしたのか詳細に追跡
                self.logger.error(f"設定ロード失敗: [{section}] {key} - 既定値 '{fallback}' を適用します。理由: {load_e}", exc_info=True)
                target_var.set(fallback)
                
        # Paths
        safe_load(self.watchdir, "Paths", "WatchDir", "")
        safe_load(self.indexdir, "Paths", "IndexDir", "")
        safe_load(self.facilitydir, "Paths", "FacilityDir", "../facility_csv")
        safe_load(self.logdir, "Paths", "LogDir", "../log")
        
        # 会計ラベルのロード
        safe_load(self.presc_label, "ReceiptLabels", "presc_label", "請求金額")
        safe_load(self.paid_label, "ReceiptLabels", "paid_label", "前回領収済")
        safe_load(self.prevunpaid_label, "ReceiptLabels", "prev_unpaid_label", "前回未収金")
        safe_load(self.currunpaid_label, "ReceiptLabels", "curr_unpaid_label", "今回未収金")
        
               
        safe_load(self.enable_delay_billing, "Settings", "EnableDelayBilling", 0, is_int=True)
        safe_load(self.delay_symbol_billing, "Settings", "DelaySymbolBilling", "none")
        safe_load(self.enable_delay_refund, "Settings", "EnableDelayRefund", 0, is_int=True)      
        safe_load(self.delay_symbol_refund, "Settings", "DelaySymbolRefund", "none")
        safe_load(self.qrlinebreak, "Settings", "QrLineBreak", "CRLF")
        # INIからロードして画面コントロールへ状態反映
        safe_load(self.print_1d_barcode_var, "Settings", "Print1DBarcode", 1, is_int=True)
        safe_load(self.barcode_mode_var, "Printer", "BarcodeMode", "2D")
        
        safe_load(self.targetdays, "Settings", "TargetDays", 365, is_int=True)
        safe_load(self.maintenancetime, "Settings", "MaintenanceTime", 0, is_int=True)
        safe_load(self.csvpatientidcolumn, "Settings", "CsvPatientIdColumn", 2, is_int=True)
        safe_load(self.totalamountcolumn, "NSIPS_Assign", "TotalAmountColumn", 19, is_int=True)
        
        # --- NSIPSアサイン ---
        safe_load(self.nsips_presc, "NSIPS_Assign", "presc", 17, is_int=True)
        safe_load(self.nsips_prev_unpaid, "NSIPS_Assign", "prev_unpaid", 16, is_int=True)
        safe_load(self.nsips_already_paid, "NSIPS_Assign", "already_paid", 18, is_int=True)
        safe_load(self.nsips_receipt, "NSIPS_Assign", "receipt", 19, is_int=True)
        safe_load(self.nsips_curr_unpaid, "NSIPS_Assign", "curr_unpaid", 20, is_int=True)
        safe_load(self.nsips_sentei, "NSIPS_Assign", "sentei", 15, is_int=True)
        
        # --- Printer ---
        safe_load(self.printername, "Printer", "PrinterName", "non")
        safe_load(self.scalerate, "Printer", "ScaleRate", "1.0")
        safe_load(self.log_retention_days, "Settings", "LogRetentionDays", 365, is_int=True)
        op_code = c.get("Printer", "OperationMode", fallback="A6_1D")
        if op_code == "80mm_2D":
            self.opmode.set("3. 80mmロール紙：2D一本化モード (レシート仕様)")
        elif op_code == "A6_2D":
            self.opmode.set("2. A6用紙：2D一本化モード (QR仕様)")
        else:
            self.opmode.set("1. A6用紙：1D並列モード (現行互換)")
        
        safe_load(self.systemname_entry, "Common", "SystemName", "領収明細票（薬局控）")
        self.root.title(f"{self.systemname_entry.get().strip()} 設定管理ツール")
        # 起動時にINIから読み込んでUIに反映
        safe_load(self.hide_zero_items_var, "Common", "HideZeroAmountItems", 0, is_int=True)
        
        if c.has_section("PrintItems"):
            for i in range(6):
                v = c.get("PrintItems", f"Item{i+1}", fallback="")
                if v:
                    ps = [p.strip() for p in v.split(',')]
                    for j in range(min(7, len(ps))): self.items_vars[i]['vars'][j].set(ps[j])
                    if len(ps) >= 8: self.items_vars[i]['status'].set(int(ps[7]))

        sel_mode = self.opmode.get()
        target_sec = "Layout_80mm" if "80mm" in sel_mode else "Layout_A6"
        self.load_layout_only(target_sec)
        self.on_opmode_changed()
        
    def load_layout_only(self, sec="Layout_A6"):
        c = self.config
        if not c.has_section(sec) and sec == "Layout_80mm" and c.has_section("Layout_A6"):
            c.add_section("Layout_80mm")
            for k, v in c.items("Layout_A6"): c.set("Layout_80mm", k, v)
            
        keys = [("Title_X", "270"), ("Title_Y", "30"), ("Title_FontSize", "50"), ("RevLabel_X", "300"), ("RevLabel_Y", "1100"),
        ("RevLabel_FontSize", "35"), ("PrintDate_X", "400"), ("PrintDate_Y", "130"), ("PrintDate_FontSize", "30"), ("Date_X", "400"), ("Date_Y", "90"), ("DispDate_FontSize", "35"), 
        ("Patient_X", "230"), ("Patient_Y", "195"), ("Patient_FontSize", "42"), ("TotalAmount_X", "0"), ("TotalAmount_Y", "900"), ("TotalAmount_FontSize", "90"), ("TotalLabel_X", "450"),
        ("TotalLabel_Y_Offset", "-50"), ("Notice_X", "570"), ("Notice_Y", "1090"), ("Notice_FontSize", "25"), ("Notice2_X", "40"), ("Notice2_Y", "240"), ("Notice2_FontSize", "22"),
        ("Notice3_X", "650"), ("Notice3_Y", "1120"), ("Notice3_FontSize", "22"), ("QR_X", "40"), ("QR_Y", "20"), ("QR_Size", "160"), ("Item_Barcode_Y_Offset", "115"), ("Item_Barcode_W", "280"),
        ("Item_Barcode_H", "70"), ("Item_Amount_X_Offset", "350"), ("Item_Amount_Y_Offset", "30"), ("Item_Amount_FontSize", "70"), ("Item_Yen_X_Offset", "350"), ("Item_Yen_Y_Offset", "70"), ("Item_Yen_FontSize", "30"),
        ("Item_Name_FontSize", "30"), ("Item_Line_Y_Offset", "190"), ("Box_X", "40"), ("Box_Y", "1010"), ("Box_W", "250"), ("Box_H", "190"), ("BoxLabel_FontSize", "28"), ("BoxVal_FontSize", "70"),
        ("BoxDateVal_FontSize", "70"), ("BoxNoVal_FontSize", "90"), ("BoxRcptVal_FontSize", "70"), ("BoxDateLabel_X", "55"), ("BoxDateLabel_Y", "1010"), ("BoxDateVal_X", "70"), ("BoxDateVal_Y", "1030"),
        ("BoxNoLabel_X", "55"), ("BoxNoLabel_Y", "1100"), ("BoxNoVal_X", "100"), ("BoxNoVal_Y", "1120"), ("BoxRcptLabel_X", "500"), ("BoxRcptLabel_Y", "1100"), ("BoxRcptVal_X", "500"), ("BoxRcptVal_Y", "1120"),
        ("Line1_StartX", "40"), ("Line1_EndX", "834"), ("Line1_Y", "260"), ("Line2_StartX", "40"), ("Line2_EndX", "834"), ("Line2_Y", "844"), ("Line3_StartX", "40"), ("Line3_EndX", "834"), ("Line3_Y", "1000"),
        ("RefundQR_X", "680"), ("RefundQR_Y", "1040"), ("RefundQR_Size", "160"), ("BurdenRate_X", "40"), ("BurdenRate_Y", "850"), ("BurdenRate_FontSize", "40"), ("Adj_X", "40"), ("Adj_Y_Base", "880"), ("Adj_L_H", "35"),
        ("Adj_Main_FS", "28"), ("Adj_Sub_FS", "22")
        ]
        for k, dv in keys: getattr(self, k.lower()).set(c.get(sec, k, fallback=dv))

        # --- 各セクションから注釈を取得し、無ければデフォルト値をセット ---
        val4_default = "※【返品】ボタン"
        val5_default = " 先に押すこと!!"
        
        # INIファイルから値を取得（セクションやキーが無ければデフォルト値を採用）
        t1 = c.get(sec, "NoticeText", fallback="")
        t2 = c.get(sec, "NoticeText2", fallback="")
        t3 = c.get(sec, "NoticeText3", fallback="")
        t4 = c.get(sec, "NoticeText4", fallback=val4_default)
        t5 = c.get(sec, "NoticeText5", fallback=val5_default)
        
        # 確実に画面のUI変数へセット
        self.noticetext.set(t1)
        self.noticetext2.set(t2)
        self.noticetext3.set(t3)
        self.noticetext4.set(t4 if t4 else val4_default)
        self.noticetext5.set(t5 if t5 else val5_default)
    # 運用モードに応じて、不要な座標エントリを一斉グレーアウトする衛兵ロジック
    def on_opmode_changed(self, event=None):
        sel_mode = self.opmode.get()
        target_sec = "Layout_80mm" if "80mm" in sel_mode else "Layout_A6"
        
        # 1. まず正しい砦からデータをロード
        self.load_layout_only(target_sec)
        
        # 2. モード1（現行互換）の時だけ1Dチェックボックスを有効化、それ以外はグレーアウト
        if "1. A6用紙：1D" in sel_mode:
            self.chk_1d.config(state="normal", text="1次元バーコードを印字する")
        else:
            self.chk_1d.config(state="disabled", text="1次元バーコードを印字する (※モード1のみ有効)")
            
        # 3. 80mm判定フラグ
        is_80mm = "80mm" in sel_mode
        ui_state = "disabled" if is_80mm else "normal"
        bg_color = "#E0E0E0" if is_80mm else "white"  # ★80mm時はグレー、A6時は白に強制変更
        
        # 4.80mmでプログラムが完全自動計算する「全座標・全オフセット」の指定
        disabled_vars_80mm = [
            self.title_x, self.title_y,
            self.revlabel_x, self.revlabel_y,
            self.printdate_x, self.printdate_y,
            self.date_x, self.date_y,
            self.patient_x, self.patient_y,
            self.totalamount_x, self.totalamount_y,
            self.totallabel_x, self.totallabel_y_offset,
            self.qr_x, self.qr_y,
            self.refundqr_x, self.refundqr_y,
            self.burdenrate_x, self.burdenrate_y,
            self.notice_x, self.notice_y,
            self.notice2_x, self.notice2_y,
            self.notice3_x, self.notice3_y,
            self.item_barcode_y_offset, self.item_barcode_w, self.item_barcode_h,
            self.item_amount_x_offset, self.item_amount_y_offset,
            self.item_yen_x_offset, self.item_yen_y_offset,
            self.item_line_y_offset,
            self.box_x, self.box_y, self.box_w, self.box_h,
            self.boxdatelabel_x, self.boxdatelabel_y, self.boxdateval_x, self.boxdateval_y,
            self.boxnolabel_x, self.boxnolabel_y, self.boxnoval_x, self.boxnoval_y,
            self.boxrcptlabel_x, self.boxrcptlabel_y, self.boxrcptval_x, self.boxrcptval_y,
            self.adj_x, self.adj_y_base, self.adj_l_h,
            self.line1_startx, self.line1_endx, self.line1_y,
            self.line2_startx, self.line2_endx, self.line2_y,
            self.line3_startx, self.line3_endx, self.line3_y
        ]
        
        # shadow_copyした_entry 属性を検知して状態を一斉制御
        for v in disabled_vars_80mm:
            if hasattr(v, '_entry'):
                v._entry.config(state=ui_state)
                
        # 5. 【一斉グレーアウト】「印字項目設定」タブにある各行の X / Y 座標入力欄も防御
        for it in self.items_vars:
            vs = it['vars']
            if hasattr(vs[5], '_entry'):  # 5番目：pos_x
                vs[5]._entry.config(state=ui_state)
            if hasattr(vs[6], '_entry'):  # 6番目：pos_y
                vs[6]._entry.config(state=ui_state)
                
        # プレビューを自動で再描画して確認
        self.update_preview()
        
    def on_closing(self):
        """×ボタンが押された時、または安全に終了する際のシーケンス"""
        self.logger.info("[SYSTEM] 設定管理ツールを終了します。(OSレベルでの完全クリーンアップ)")
        # sys.exit() ではなく、OSレベルでプロセスを叩き切る
        os._exit(0)
        
if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = ConfigEditorV2(root)
        root.mainloop()
    except Exception as fatal_e:
        # Tkinter自体の崩壊や、起動直後の致命的エラーを絶対に逃がさない
        error_msg = traceback.format_exc()
        try:
            # なんとか足元にエラーログだけは吐き出す
            fallback_log = os.path.join(const.BASE_DIR, "fatal_gui_crash.txt")
            with open(fallback_log, "w", encoding="cp932") as f:
                f.write(f"【致命的起動エラー】\n{error_msg}")
        except:
            pass
            
        # Tkinterが死んでいても、WindowsのOS APIを直接叩いて生エラーメッセージを表示
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, 
            f"GUIの起動に失敗しました。\n\n【エラー内容】\n{fatal_e}\n\n※詳細は fatal_gui_crash.txt を確認してください。", 
            "システム起動エラー", 
            0x10
        )
        
# Copyright (c) 2026 ph-SIM133
# All rights reserved.
# This software is for non-commercial use only.