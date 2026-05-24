# -*- coding: cp932 -*-
#必ずshift-jisにて保存を！！
import os
import sys

# ==============================================================================
# 0地点（BASE_DIR）をsys.pathの最優先に挿入し、参照先を強制固定
# ==============================================================================
import lib_constants as const
sys.path.insert(0, const.BASE_DIR)

import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import datetime
import json
from PIL import Image
from PIL import ImageTk
import configparser
import msvcrt
from typing import Dict, Any, Optional

try:
    from tkcalendar import DateEntry
except ImportError:
    messagebox.showerror("環境不備", "tkcalendarがインストールされていません。\n'pip install tkcalendar' を実行してください。")
    sys.exit(1)

import win32print
import lib_barcode_parser as parser
import lib_barcode_drawer as drawer
import lib_common as common

class ReissueToolPro:
    def __init__(self, root_win: tk.Tk):
        self.root = root_win
        self.root.title("領収明細票 再発行管理 (0地点同期・容器分離対応版)")
        self.root.geometry("1680x880")
        
        self.conf = {}
        try:
            self.conf = common.load_full_config()
            raw_cfg = configparser.ConfigParser(interpolation=None)
            raw_cfg.optionxform = str
            raw_cfg.read(const.INI_FILE, encoding='cp932')
            
            self.conf['paper_size'] = raw_cfg.get('Printer', 'PaperSize', fallback='A6')
            self.conf['barcode_mode'] = raw_cfg.get('Printer', 'BarcodeMode', fallback='1D')
            self.conf['layout'] = dict(raw_cfg['Layout_A6']) if 'Layout_A6' in raw_cfg else {}
            self.history_file = const.MASTER_HISTORY_FILE
             
            path_dict = common.initialize_directories(raw_cfg)
            self.work_dir = path_dict['work']
            self.pdf_out_dir = path_dict['pdf']
            
        except Exception as e:
            messagebox.showerror("初期化エラー", f"設定の同期に失敗しました:\n{e}")
            self.root.destroy()
            return
            
        # 【機能共存】両方のチェックボックスフラグを独立して定義
        self.latest_only_var = tk.BooleanVar(value=False)    # 既存機能: 最新のみ抽出（初期OFF推奨）
        self.use_tree_view_var = tk.BooleanVar(value=True)   # 新機能: ツリーソート表示（初期ON）
        
        self.file_map = {}
        self.history_filenames = []
        self.history_data = [] 
        
        self.create_widgets()
        self.load_history()

    def create_widgets(self):
        """UIコンポーネントの構築"""
        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(fill=tk.X)
        
        ttk.Label(top_frame, text="検索日:").pack(side=tk.LEFT, padx=5)
        self.date_entry = DateEntry(top_frame, width=12, background='darkblue', foreground='white', borderwidth=2, date_pattern='yyyy/mm/dd')
        self.date_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(top_frame, text="患者ID:").pack(side=tk.LEFT, padx=(20, 5))
        self.id_search_var = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.id_search_var, width=15).pack(side=tk.LEFT, padx=5)
        
        # 2つのチェックボックスを横に並べて配置（機能の追加）
        tk.Checkbutton(top_frame, text="最新のみ表示", variable=self.latest_only_var, command=self.filter_data).pack(side=tk.LEFT, padx=5)
        tk.Checkbutton(top_frame, text="ツリー表示 (履歴束ね)", variable=self.use_tree_view_var, command=self.filter_data).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(top_frame, text="履歴を検索", command=self.filter_data).pack(side=tk.LEFT, padx=20)
        
        printers = ["non"]
        try:
            p_enum = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
            for p in p_enum: printers.append(p[2])
        except:
            pass
        
        ttk.Label(top_frame, text="出力先:").pack(side=tk.LEFT, padx=(30, 5))
        init_printer = self.conf.get('printer', 'non')
        self.printer_var = tk.StringVar(value=init_printer)
        ttk.Combobox(top_frame, textvariable=self.printer_var, values=printers, state="readonly", width=25).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(top_frame, text="選択した処方を再発行", command=self.execute_reissue).pack(side=tk.RIGHT, padx=10)

        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left_frame = ttk.Frame(main_pane)
        main_pane.add(left_frame, weight=3)
        
        cols = ("date", "patient_id", "name", "item1", "item2", "item3", "item4", "item5", "item6", "total", "filename")
        self.tree = ttk.Treeview(left_frame, columns=cols, show="headings", selectmode="extended")
        
        p_items = self.conf.get('print_items', [])
        heads = {
            "date": "来局日", "patient_id": "患者ID", "name": "患者氏名",
            "total": "請求総額", "filename": "ファイル名"
        }
        for i in range(1, 7):
            heads[f"item{i}"] = p_items[i-1]['name'] if len(p_items) >= i else f"予備枠{i}"

        for c, h in heads.items():
            self.tree.heading(c, text=h)
            w_val = 15 if "item" in c else 20 if c in ["date", "patient_id"] else 40
            align_pos = tk.E if "item" in c or c == "total" else tk.W
            self.tree.column(c, width=w_val, anchor=align_pos)
            
        sb = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        right_frame = ttk.Frame(main_pane)
        main_pane.add(right_frame, weight=2)
        ttk.Label(right_frame, text="印字プレビュー", font=("", 12, "bold")).pack(pady=5)
        self.preview_lbl = tk.Label(right_frame, bg="white", relief="solid")
        self.preview_lbl.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def load_history(self):
        self.history_filenames = []
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 0x7FFFFFFF)
                    try:
                        self.history_filenames = json.load(f)
                    finally:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 0x7FFFFFFF)
            except Exception as e:
                print(f"履歴ロードエラー: {e}")
                pass
        self.filter_data()

    def filter_data(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        self.file_map.clear()
        self.history_data = []
        
        target_str = self.date_entry.get_date().strftime('%Y%m%d')
        search_id = self.id_search_var.get().strip()
        watch_dir = self.conf.get('watch_dir', '')
        
        if not watch_dir or not os.path.exists(watch_dir):
            return

        for fname in self.history_filenames:
            if len(fname) < 21: continue
            if fname[3:11] != target_str: continue
            if search_id and (search_id not in fname): continue
            
            fpath = os.path.normpath(os.path.join(watch_dir, fname))
            if not os.path.exists(fpath): continue
            
            try:
                res = parser.process_nsips(fpath, watch_dir, set(), is_comparing=True, conf=self.conf)
                if isinstance(res, dict):
                    res['version_info'] = const.CURRENT_VERSION
                    rec = {
                        "date": res.get("dispensing_date", ""),
                        "patient_id": str(res.get("patient_id", "----")),
                        "name": res.get("patient_name", ""),
                        "total": int(res.get("total_amount", 0)),
                        "filename": fname,
                        "raw_data": res
                    }
                    
                    p_items_cfg = self.conf.get('print_items', [])
                    for i in range(1, 7):
                        if len(p_items_cfg) >= i:
                            name_key = p_items_cfg[i-1]['name']
                            rec[f"item{i}"] = res['amounts'].get(name_key, 0)
                        else:
                            rec[f"item{i}"] = 0
                    
                    disp_amounts = []
                    for i in range(1, 7):
                        disp_amounts.append(f"{int(rec[f'item{i}']):,}")
                        
                    total_str = f"{int(rec['total']):,}円"
                    rec["disp_values"] = (rec["date"], rec["patient_id"], rec["name"]) + tuple(disp_amounts) + (total_str, fname)
                    self.history_data.append(rec)
            except Exception as e:
                print(f"解析スキップ ({fname}): {e}")
                pass

        # 【機能1】最新版のみの表示
        if self.latest_only_var.get():
            latest_map = {}
            for r in self.history_data:
                fn = os.path.splitext(r["filename"])[0] 
                # 先頭3文字(A00等)と末尾5文字(00000等)を除外し、真のコアだけを抜き出す
                core_id = fn[3:-5] if len(fn) >= 21 else fn[3:-3] 
                seq = int(fn[-5:]) if len(fn) >= 21 else int(fn[-3:]) 
                if core_id not in latest_map or seq > latest_map[core_id]["seq"]:
                    latest_map[core_id] = {"seq": seq, "rec": r}
            self.history_data = [v["rec"] for v in latest_map.values()]

        # ★【機能2】ツリーソート表示ロジック
        if self.use_tree_view_var.get():
            core_groups = {}
            for r in self.history_data:
                fn = os.path.splitext(r["filename"])[0] 
                # 先頭3文字(A00等)と末尾5文字(00000等)を除外し、真のコアだけを抜き出す
                core_id = fn[3:-5] if len(fn) >= 21 else fn[3:-3]
                if core_id not in core_groups:
                    core_groups[core_id] = []
                core_groups[core_id].append(r)

            for core_id, rec_list in sorted(core_groups.items()):
                rec_list.sort(key=lambda x: x["filename"])
                
                formatted_date = f"{core_id[:4]}/{core_id[4:6]}/{core_id[6:8]}" if len(core_id) >= 8 else core_id
                seq_num = core_id[8:] if len(core_id) >= 8 else ""
                parent_label = f" {formatted_date} - {seq_num}" if seq_num else f" {core_id}"
                
                rep = rec_list[0]
                parent_values = (parent_label, rep["patient_id"], rep["name"], "", "", "", "", "", "", "", "※ツリーを展開")
                
                pid = self.tree.insert("", tk.END, values=parent_values, open=True)
                
                for r in rec_list:
                    fn = os.path.splitext(r["filename"])[0]
                    kind_prefix = fn[:2]             
                    branch_idx = str(int(fn[-5:])) if len(fn) >= 21 else str(int(fn[-3:]))  
                    branch_name = f"└── {kind_prefix[0]}{branch_idx}"
                    
                    child_disp = (branch_name, r["disp_values"][1], r["disp_values"][2]) + r["disp_values"][3:]
                    cid = self.tree.insert(pid, tk.END, values=child_disp)
                    self.file_map[cid] = r["filename"]
        else:
            # フラット表示
            for rec in self.history_data:
                iid = self.tree.insert("", tk.END, values=rec["disp_values"])
                self.file_map[iid] = rec["filename"]

    def on_select(self, event):
        selection_list = self.tree.selection()
        if not selection_list: return
            
        if selection_list[0] not in self.file_map: return
            
        target_fn = self.file_map[selection_list[0]]
        
        for record in self.history_data:
            if record["filename"] == target_fn:
                try:
                    preview_img = drawer.generate_pdf_logic(record["raw_data"], self.conf, self.work_dir, None, None, is_preview=True)
                    if preview_img:
                        preview_img.thumbnail((600, 850), Image.Resampling.LANCZOS)
                        self.img_tk = ImageTk.PhotoImage(preview_img)
                        self.preview_lbl.config(image=self.img_tk)
                except Exception as e:
                    print(f"プレビュー生成失敗: {e}")
                break

    def execute_reissue(self) -> None:
        selection_list = self.tree.selection()
        valid_selections = [iid for iid in selection_list if iid in self.file_map]
        
        if not valid_selections:
            messagebox.showwarning("選択不備", "再発行する具体的なファイル（A0, U0など）を選択してください。")
            return
            
        selected_printer = self.printer_var.get()
        confirm_msg = f"{len(valid_selections)}件の処方を再発行します。\n出力先: {selected_printer}"
        if not messagebox.askyesno("発行確認", confirm_msg): return
            
        for iid in valid_selections:
            fname_key = self.file_map[iid]
            for record in self.history_data:
                if record["filename"] == fname_key:
                    try:
                        self.conf['printer'] = selected_printer
                        drawer.generate_pdf_logic(record["raw_data"], self.conf, self.work_dir, self.pdf_out_dir, common.print_image_directly, False)
                    except Exception as err:                  
                        messagebox.showerror("発行エラー", f"ファイル {fname_key} の出力中にエラーが発生:\n{err}")
                    break
                    
        messagebox.showinfo("完了", "再発行処理が完了しました。")

if __name__ == "__main__":
    root = tk.Tk()
    app = ReissueToolPro(root)
    root.mainloop()

# Copyright (c) 2026 ph-SIM133
# All rights reserved.
# This software is for non-commercial use only.