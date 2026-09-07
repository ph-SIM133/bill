from __future__ import annotations

import calendar
import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tempfile import TemporaryDirectory
from tkinter import filedialog, messagebox, ttk

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from medi_sup.model import (
        MedicationPeriod,
        INTERMITTENT_BOUNDARY_HOSPITAL,
        PackagingAssessment,
        PackagingResult,
        ValidationError,
        audit_result_balance,
        build_period_report,
        build_inventory_update,
        calculate_assessment,
        common_delivery_periods,
        confirm_packaging,
        continuous_period_from_counts,
        counterpart_hospitals,
        delivered_periods,
        next_creation_candidates,
        parse_date,
        period_slot_counts,
        period_result_to_dict,
        remaining_outside_interval,
    )
    from medi_sup.storage import DEFAULT_DATA_DIR, load_preferences, load_record, patient_directory, save_error_log, save_preferences, save_record
    from medi_sup.pdf_export import MAX_DRIVER_PRINTABLE_HEIGHT_MM, estimate_90mm_height, export_90mm_report
    from medi_sup.odt_import import import_previous_odt
    from medi_sup.odt_export import export_or_append_odt
    from medi_sup.version import APP_NAME, VERSION
    from medi_sup.intermittent import WEEKDAYS, build_intermittent_report, format_dates, format_dates_compact, item_dates, merge_intermittent_extension, next_dose_dates, rule_label
else:
    from .model import (
        MedicationPeriod,
        INTERMITTENT_BOUNDARY_HOSPITAL,
        PackagingAssessment,
        PackagingResult,
        ValidationError,
        audit_result_balance,
        build_period_report,
        build_inventory_update,
        calculate_assessment,
        common_delivery_periods,
        confirm_packaging,
        continuous_period_from_counts,
        counterpart_hospitals,
        delivered_periods,
        next_creation_candidates,
        parse_date,
        period_slot_counts,
        period_result_to_dict,
        remaining_outside_interval,
    )
    from .storage import DEFAULT_DATA_DIR, load_preferences, load_record, patient_directory, save_error_log, save_preferences, save_record
    from .pdf_export import MAX_DRIVER_PRINTABLE_HEIGHT_MM, estimate_90mm_height, export_90mm_report
    from .odt_import import import_previous_odt
    from .odt_export import export_or_append_odt
    from .version import APP_NAME, VERSION
    from .intermittent import WEEKDAYS, build_intermittent_report, format_dates, format_dates_compact, item_dates, merge_intermittent_extension, next_dose_dates, rule_label


DELIVERY_STORE = "揃わない薬は薬局保管"
DELIVERY_RELEASE = "揃わない薬も払い出す"
UNMATCHED_STORE = "お渡し期間に合わせて交付し、残りの薬は薬局保管"
UNMATCHED_RELEASE = "重ならない服用時点は合包範囲外とし、すべて渡す"


class MedicationSupportApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("1500x1000")
        self.minsize(1100, 760)
        self.assessment: PackagingAssessment | None = None
        self.result: PackagingResult | None = None
        self.inventory_record: dict | None = None
        self.inventory_report: str | None = None
        self.inventory_corrections: list[dict] = []
        self.anomaly_reviews: list[dict] = []
        self.assessment_issues: tuple[str, ...] = ()
        self.odt_path: Path | None = None
        self.intermittent_items: list[dict] = []
        self.intermittent_forecasts: list[dict] = []
        self.delivery_mode = tk.StringVar(value=DELIVERY_STORE)
        self.unmatched_slot_mode = tk.StringVar(value=UNMATCHED_RELEASE)
        self.show_intermittent = tk.BooleanVar(value=True)
        self.use_slot_detail = tk.BooleanVar(value=True)
        self.use_appointment_dates = tk.BooleanVar(value=True)
        self.use_date_management = tk.BooleanVar(value=True)
        self.show_pdf_action = tk.BooleanVar(value=True)
        self.show_odt_action = tk.BooleanVar(value=True)
        preferences = load_preferences(DEFAULT_DATA_DIR)
        self.show_legacy_odt_action = tk.BooleanVar(value=bool(preferences.get("show_legacy_odt", True)))
        self.default_pharmacy = str(preferences.get("pharmacy", ""))
        self.print_corrections = tk.BooleanVar(value=True)
        self.record_saved = False
        self._configure_style()
        self._build()
        try:
            self.state("zoomed")
        except tk.TclError:
            pass

    def report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        """Tkinterが通常はコンソールへ流す例外を、利用者へ通知して保存する。"""
        try:
            path = save_error_log(exc_type, exc_value, exc_traceback)
            detail = f"\n\n記録：{path}"
        except OSError:
            detail = "\n\nエラー記録も保存できませんでした。"
        messagebox.showerror(
            "処理を完了できませんでした",
            "予期しないエラーが発生しました。入力内容は確定されていません。"
            "同じ操作を繰り返さず、表示された記録を確認してください。"
            + detail,
            parent=self,
        )

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Yu Gothic UI", 22, "bold"), foreground="#17324D")
        style.configure("Step.TLabel", font=("Yu Gothic UI", 14, "bold"), foreground="#1D5F8B")
        style.configure("InputStage.TLabel", font=("Yu Gothic UI", 19, "bold"), foreground="#154F78", background="#D9EEFF", padding=(8, 4))
        style.configure("InputStageDone.TLabel", font=("Yu Gothic UI", 19, "bold"), foreground="#245C2A", background="#DDF2DF", padding=(8, 4))
        style.configure("InputStageIdle.TLabel", font=("Yu Gothic UI", 19, "bold"), foreground="#7B8790", padding=(8, 4))
        style.configure("InputStageNote.TLabel", font=("Yu Gothic UI", 11, "bold"), foreground="#536A7A")
        style.configure("PatientInfo.TLabel", font=("Yu Gothic UI", 13, "bold"), foreground="#17324D")
        style.configure("PatientInfo.TEntry", font=("Yu Gothic UI", 13))
        style.configure("Guidance.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#17324D", background="#EAF4FF", padding=(12, 7))
        style.configure("GuidanceInput.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#154F78", background="#D9EEFF", padding=(12, 7))
        style.configure("GuidanceCurrent.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#7A4B00", background="#FFF1B8", padding=(12, 7))
        style.configure("GuidanceDone.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#245C2A", background="#DDF2DF", padding=(12, 7))
        # ③〜⑤の案内文は、目線を移しても読み取りやすい同じ大きさに統一する。
        style.configure("StageMessage.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#17324D", padding=(6, 5))
        style.configure("StageMessageCurrent.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#6A4700", background="#FFF1B8", padding=(6, 5))
        style.configure("StageMessageComplete.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#245C2A", background="#DDF2DF", padding=(6, 5))
        style.configure("Section.TLabelframe.Label", font=("Yu Gothic UI", 12, "bold"), foreground="#17324D")
        style.configure("BasicCurrent.TLabelframe", background="#D9EEFF")
        style.configure("BasicCurrent.TLabelframe.Label", background="#D9EEFF", foreground="#154F78", font=("Yu Gothic UI", 14, "bold"))
        style.configure("BasicDone.TLabelframe", background="#EAF4FF")
        style.configure("BasicDone.TLabelframe.Label", background="#EAF4FF", foreground="#245C2A", font=("Yu Gothic UI", 14, "bold"))
        style.configure("BasicSubCurrent.TLabelframe", background="#D9EEFF")
        style.configure("BasicSubCurrent.TLabelframe.Label", background="#D9EEFF", foreground="#154F78", font=("Yu Gothic UI", 12, "bold"))
        style.configure("BasicSubDone.TLabelframe", background="#EAF4FF")
        style.configure("BasicSubDone.TLabelframe.Label", background="#EAF4FF", foreground="#245C2A", font=("Yu Gothic UI", 12, "bold"))
        style.configure("BasicCurrent.TFrame", background="#D9EEFF")
        style.configure("BasicCurrent.TLabel", background="#D9EEFF", foreground="#17324D")
        style.configure("BasicCurrent.TCheckbutton", background="#D9EEFF")
        style.configure("BasicCurrentPatient.TLabel", background="#D9EEFF", foreground="#17324D", font=("Yu Gothic UI", 13, "bold"))
        style.configure("BasicDone.TFrame", background="#EAF4FF")
        style.configure("BasicDone.TLabel", background="#EAF4FF", foreground="#17324D")
        style.configure("BasicDone.TCheckbutton", background="#EAF4FF")
        style.configure("BasicDonePatient.TLabel", background="#EAF4FF", foreground="#17324D", font=("Yu Gothic UI", 13, "bold"))
        style.configure("BasicLocked.TLabelframe", background="#DDF2DF")
        style.configure("BasicLocked.TLabelframe.Label", background="#DDF2DF", foreground="#245C2A", font=("Yu Gothic UI", 14, "bold"))
        style.configure("BasicSubLocked.TLabelframe", background="#DDF2DF")
        style.configure("BasicSubLocked.TLabelframe.Label", background="#DDF2DF", foreground="#245C2A", font=("Yu Gothic UI", 12, "bold"))
        style.configure("BasicLocked.TFrame", background="#DDF2DF")
        style.configure("BasicLocked.TLabel", background="#DDF2DF", foreground="#245C2A")
        style.configure("BasicLocked.TCheckbutton", background="#DDF2DF")
        style.configure("BasicLockedPatient.TLabel", background="#DDF2DF", foreground="#245C2A", font=("Yu Gothic UI", 13, "bold"))
        style.configure("Stage.TLabelframe.Label", font=("Yu Gothic UI", 17, "bold"), foreground="#7B8790")
        style.configure("StageSub.TLabelframe.Label", font=("Yu Gothic UI", 11, "bold"), foreground="#7B8790")
        style.configure("PeriodIdle.TLabelframe.Label", font=("Yu Gothic UI", 11, "bold"), foreground="#6B7780")
        idle_fill = style.lookup("TFrame", "background") or "#F0F0F0"
        style.configure("PeriodIdle.TLabelframe", background=idle_fill)
        style.configure("PeriodIdle.TFrame", background=idle_fill)
        style.configure("PeriodIdle.TLabel", background=idle_fill)
        style.configure("PeriodIdle.TEntry", fieldbackground="white", background=idle_fill)
        style.configure("PeriodIdle.TCombobox", fieldbackground="white", background=idle_fill)
        style.configure("Primary.TButton", font=("Yu Gothic UI", 11, "bold"), padding=(14, 8))
        style.configure("Outlined.TButton", foreground="#154F78", borderwidth=2, relief="solid", padding=(8, 5))
        style.map("Outlined.TButton", foreground=[("active", "#103F61"), ("pressed", "#103F61")])
        style.configure("Outlined.Toolbutton", foreground="#154F78", borderwidth=2, relief="solid")
        style.map("Outlined.Toolbutton", foreground=[("selected", "#103F61"), ("active", "#103F61")])
        style.configure("Carryover.TButton", font=("Yu Gothic UI", 12, "bold"), padding=(16, 9), background="#D9EEFF", foreground="#154F78")
        style.map(
            "Carryover.TButton",
            background=[("active", "#C9E6FA"), ("pressed", "#A9D4F2")],
            foreground=[("active", "#103F61"), ("pressed", "#103F61")],
        )
        style.configure("Result.TLabel", font=("Yu Gothic UI", 12, "bold"), foreground="#276746")
        style.configure("Next.TLabelframe", background="#FFF1B8")
        style.configure("Next.TLabelframe.Label", background="#FFF1B8", foreground="#7A4B00", font=("Yu Gothic UI", 17, "bold"))
        style.configure("Next.TFrame", background="#FFF1B8")
        style.configure("Next.TPanedwindow", background="#FFF1B8")
        style.configure("Next.TLabel", background="#FFF1B8", foreground="#6A4700", font=("Yu Gothic UI", 10, "bold"))
        style.configure("NextSub.TLabelframe", background="#FFF1B8")
        style.configure("NextSub.TLabelframe.Label", background="#FFF1B8", foreground="#6A4700", font=("Yu Gothic UI", 11, "bold"))
        style.configure("Complete.TLabelframe", background="#DDF2DF")
        style.configure("Complete.TLabelframe.Label", background="#DDF2DF", foreground="#245C2A", font=("Yu Gothic UI", 17, "bold"))
        style.configure("Complete.TFrame", background="#DDF2DF")
        style.configure("Complete.TPanedwindow", background="#DDF2DF")
        style.configure("Complete.TLabel", background="#DDF2DF", foreground="#245C2A", font=("Yu Gothic UI", 10, "bold"))
        style.configure("CompleteSub.TLabelframe", background="#DDF2DF")
        style.configure("CompleteSub.TLabelframe.Label", background="#DDF2DF", foreground="#245C2A", font=("Yu Gothic UI", 11, "bold"))
        style.configure("Next.TButton", font=("Yu Gothic UI", 11, "bold"), padding=(14, 8), background="#F6C344")
        style.map("Next.TButton", background=[("active", "#FFD86A"), ("pressed", "#DDAA25")])
        style.configure("Ready.TButton", font=("Yu Gothic UI", 10, "bold"), padding=(10, 6), background="#87C98D")
        style.map("Ready.TButton", background=[("active", "#A8D9AC"), ("pressed", "#64AD6B")])
        style.configure("WorkflowIdle.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#7B8790", padding=(8, 4))
        style.configure("WorkflowCurrent.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#7A4B00", background="#FFF1B8", padding=(8, 4))
        style.configure("WorkflowDone.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#245C2A", background="#DDF2DF", padding=(8, 4))
        # 保管・受付はカード識別色と混同しないよう、工程表示専用の青系にする。
        style.configure("WorkflowInputCurrent.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#154F78", background="#D9EEFF", padding=(8, 4))
        style.configure("WorkflowInputDone.TLabel", font=("Yu Gothic UI", 15, "bold"), foreground="#FFFFFF", background="#397AA8", padding=(8, 4))
        style.configure("WorkflowArrow.TLabel", font=("Yu Gothic UI", 13, "bold"), foreground="#8C969D")
        # 保管・受付の同じ番号を横に追えるよう、行ごとに淡い識別色を付ける。
        self._pair_styles = ("Pair1.TLabelframe", "Pair2.TLabelframe", "Pair3.TLabelframe", "Pair4.TLabelframe")
        self._pair_fills = ("#EAF4FF",) * 4
        self._received_pair_fills = ("#C9E6FA",) * 4
        self._confirmed_pair_fills = ("#DDF2DF",) * 4
        for index, (name, color, received_color) in enumerate(
            zip(self._pair_styles, self._pair_fills, self._received_pair_fills), start=1
        ):
            for prefix, fill in (
                (f"Pair{index}", color),
                (f"Pair{index}Received", received_color),
                (f"Pair{index}Confirmed", self._confirmed_pair_fills[index - 1]),
            ):
                style.configure(f"{prefix}.TLabelframe", background=fill)
                style.configure(f"{prefix}.TLabelframe.Label", background=fill, foreground="#17324D", font=("Yu Gothic UI", 11, "bold"))
                style.configure(f"{prefix}.TFrame", background=fill)
                style.configure(f"{prefix}.TLabel", background=fill)
                style.configure(f"{prefix}.TEntry", fieldbackground=fill, background=fill)
                style.configure(f"{prefix}.TCombobox", fieldbackground=fill, background=fill)

    def _build(self) -> None:
        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, highlightthickness=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        root = ttk.Frame(canvas, padding=14)
        root_window = canvas.create_window((0, 0), window=root, anchor="nw")
        root.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(root_window, width=event.width))
        self.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="外来服薬支援", style="Title.TLabel").pack(side="left")
        workflow = ttk.Frame(header)
        workflow.pack(side="left", padx=14, pady=(5, 0))
        self.workflow_labels = []
        for index, name in enumerate(("基本設定", "保管", "受付", "自動判定", "検算", "判断", "結果", "交付")):
            if index > 0:
                ttk.Label(workflow, text="→", style="WorkflowArrow.TLabel").pack(side="left", padx=2)
            label = ttk.Label(workflow, text=f"{index} {name}", style="WorkflowIdle.TLabel")
            label.pack(side="left")
            self.workflow_labels.append(label)
        ttk.Label(header, text=f"v{VERSION}", foreground="#6B7780").pack(side="left", pady=(8, 0))

        guidance = ttk.Frame(root)
        guidance.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        guidance.columnconfigure(0, weight=1)
        self.next_action = ttk.Label(guidance, text="次の手順：⓪ 対象者を入力し、設定を確認してください。", style="Guidance.TLabel")
        self.next_action.grid(row=0, column=0, sticky="ew")

        self.basic_section = ttk.LabelFrame(root, text="⓪ 基本設定", style="BasicCurrent.TLabelframe", padding=8)
        basic = self.basic_section
        basic.grid(row=2, column=0, sticky="ew", pady=4)
        basic.columnconfigure(0, weight=1, uniform="basic")
        basic.columnconfigure(1, weight=2, uniform="basic")
        basic.columnconfigure(2, weight=3, uniform="basic")
        load_methods = ttk.LabelFrame(basic, text="読込方法", style="BasicSubCurrent.TLabelframe", padding=8)
        self.load_methods_section = load_methods
        self.basic_subsections = [load_methods]
        load_methods.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        load_methods.columnconfigure(0, weight=1)
        self.previous_record_button = ttk.Button(load_methods, text="前回記録を引き継ぐ", style="Carryover.TButton", command=self.load_previous_s)
        self.previous_record_button.grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )
        self.legacy_odt_button = ttk.Button(load_methods, text="旧ODT読込", command=self.load_legacy_odt)
        self.legacy_odt_button.grid(row=1, column=0, sticky="ew")
        patient_info = ttk.LabelFrame(basic, text="患者・施設情報", style="Section.TLabelframe", padding=8)
        self.patient_info_section = patient_info
        self.basic_subsections.append(patient_info)
        patient_info.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        patient_info.columnconfigure(1, weight=1)
        ttk.Label(patient_info, text="対象者：", style="PatientInfo.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.patient = ttk.Entry(patient_info, style="PatientInfo.TEntry")
        self.patient.grid(row=0, column=1, sticky="ew")
        ttk.Label(patient_info, text="様", style="PatientInfo.TLabel").grid(row=0, column=2, sticky="w", padx=(5, 0))
        ttk.Label(patient_info, text="患者ID：", style="PatientInfo.TLabel").grid(row=1, column=0, sticky="w", pady=(7, 0), padx=(0, 5))
        self.patient_id = ttk.Entry(patient_info, style="PatientInfo.TEntry")
        self.patient_id.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Label(patient_info, text="施設：", style="PatientInfo.TLabel").grid(row=2, column=0, sticky="w", pady=(7, 0), padx=(0, 5))
        self.facility = ttk.Entry(patient_info, style="PatientInfo.TEntry")
        self.facility.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Label(patient_info, text="実施日：", style="PatientInfo.TLabel").grid(row=3, column=0, sticky="w", pady=(7, 0), padx=(0, 5))
        self.service_date = ttk.Entry(patient_info, style="PatientInfo.TEntry", width=13)
        self.service_date.grid(row=3, column=1, sticky="w", pady=(7, 0))
        self.service_date.insert(0, date.today().isoformat())
        self.service_date_button = ttk.Button(patient_info, text="日付", width=5, command=lambda: self.open_calendar(self.service_date))
        self.service_date_button.grid(
            row=3, column=2, padx=(5, 0), pady=(7, 0)
        )
        self.intermittent_summary = ttk.Label(patient_info, text="間欠服用薬：なし", foreground="#536A7A")
        self.intermittent_summary.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(patient_info, text="履歴を見る", command=self.open_history).grid(
            row=4, column=2, sticky="e", pady=(10, 0)
        )
        self.patient_folder_label = ttk.Label(patient_info, text="", foreground="#536A7A", wraplength=520)
        self.patient_folder_label.grid(row=5, column=0, columnspan=3, sticky="w", pady=(7, 0))
        self.patient.bind("<KeyRelease>", lambda _event: self._patient_identity_changed())
        self.patient_id.bind("<KeyRelease>", lambda _event: self._update_patient_folder_label())
        self._update_patient_folder_label()
        settings = ttk.LabelFrame(basic, text="設定", style="Section.TLabelframe", padding=8)
        self.settings_section = settings
        self.basic_subsections.append(settings)
        settings.grid(row=0, column=2, sticky="nsew")
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="合包未達時の取扱い").grid(row=0, column=0, sticky="w", pady=(0, 6), padx=(0, 5))
        self.delivery_mode_widget = ttk.Combobox(
            settings, textvariable=self.delivery_mode,
            values=(DELIVERY_STORE, DELIVERY_RELEASE), state="readonly", width=28,
        )
        self.delivery_mode_widget.grid(row=0, column=1, sticky="w", pady=(0, 6))
        self.delivery_mode_widget.bind("<<ComboboxSelected>>", lambda _event: self._delivery_mode_changed())
        ttk.Label(settings, text="服用時点が重ならない薬").grid(row=1, column=0, sticky="w", pady=(0, 6), padx=(0, 5))
        self.unmatched_mode = ttk.Combobox(
            settings, textvariable=self.unmatched_slot_mode,
            values=(UNMATCHED_STORE, UNMATCHED_RELEASE), state="readonly", width=54,
        )
        self.unmatched_mode.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 6))
        self.unmatched_mode.bind("<<ComboboxSelected>>", lambda _event: self._delivery_mode_changed())
        ttk.Label(settings, text="薬局名").grid(row=2, column=0, sticky="w", pady=(0, 6), padx=(0, 5))
        self.pharmacy = ttk.Entry(settings)
        self.pharmacy.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 6))
        self.pharmacy.insert(0, self.default_pharmacy)
        self.pharmacy.bind("<FocusOut>", lambda _event: self._pharmacy_changed())
        self.pharmacy.bind("<Return>", lambda _event: self._pharmacy_changed())
        options = ttk.Frame(settings)
        options.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Checkbutton(options, text="間欠薬を取り扱う", variable=self.show_intermittent,
                        command=self._display_options_changed).pack(side="left")
        ttk.Checkbutton(options, text="服用時点ごとに扱う", variable=self.use_slot_detail,
                        command=self._display_options_changed).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(options, text="受診日を入力", variable=self.use_appointment_dates,
                        command=self._display_options_changed).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(options, text="日付管理を行う", variable=self.use_date_management,
                        command=self._date_management_changed).pack(side="left", padx=(10, 0))
        output_options = ttk.Frame(settings)
        output_options.grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(output_options, text="90mm票を表示する", variable=self.show_pdf_action,
                        command=self._refresh_output_actions).pack(side="left")
        ttk.Checkbutton(output_options, text="ODTへ保存・追記（旧保存方法）", variable=self.show_odt_action,
                        command=self._refresh_output_actions).pack(side="left", padx=(14, 0))
        ttk.Checkbutton(output_options, text="修正履歴を印刷", variable=self.print_corrections).pack(side="left", padx=(14, 0))
        legacy_options = ttk.Frame(settings)
        legacy_options.grid(row=5, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Checkbutton(
            legacy_options, text="旧ODT読込ボタンを表示", variable=self.show_legacy_odt_action,
            command=self._legacy_odt_visibility_changed,
        ).pack(side="left")
        self.setting_widgets = []
        def collect_setting_widgets(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Combobox, ttk.Checkbutton)):
                    self.setting_widgets.append(child)
                collect_setting_widgets(child)
        collect_setting_widgets(settings)

        workspace = ttk.Frame(root)
        workspace.grid(row=3, column=0, sticky="nsew", pady=4)
        workspace.columnconfigure(0, weight=3, uniform="workspace")
        workspace.columnconfigure(1, weight=2, uniform="workspace")
        workspace.rowconfigure(0, weight=1)
        left = ttk.Frame(workspace)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left.columnconfigure(0, weight=1)
        right = ttk.Frame(workspace)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=1)

        pairs_section = ttk.Frame(left, padding=8)
        pairs_section.grid(row=0, column=0, sticky="ew")
        pairs_section.columnconfigure(0, weight=1, uniform="pair")
        pairs_section.columnconfigure(1, weight=1, uniform="pair")
        self.storage_stage_label = ttk.Label(pairs_section, text="① 保管", style="InputStageIdle.TLabel")
        self.storage_stage_label.grid(
            row=0, column=0, sticky="w"
        )
        self.reception_stage_label = ttk.Label(pairs_section, text="② 受付", style="InputStageIdle.TLabel")
        self.reception_stage_label.grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Label(pairs_section, text="現在の預かり薬・処方待ち", style="InputStageNote.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 6)
        )
        ttk.Label(pairs_section, text="今回持参・処方された薬", style="InputStageNote.TLabel").grid(
            row=1, column=1, sticky="w", padx=(12, 0), pady=(0, 6)
        )
        self.pairs_container = ttk.Frame(pairs_section)
        self.pairs_container.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.pairs_container.columnconfigure(0, weight=1, uniform="pair")
        self.pairs_container.columnconfigure(1, weight=1, uniform="pair")
        # 同じ親フレームの同じ行に置くことで、枠の高さが違っても番号が横に揃う。
        self.s_container = self.pairs_container
        self.o_container = self.pairs_container
        self.s_fields: list[dict[str, ttk.Entry]] = []
        self.s_forecast_flags: list[bool] = []
        self.add_s_box()
        self.previous_forecast = ttk.Label(
            pairs_section,
            text="前回からの作成予想：なし",
            foreground="#536A7A",
            anchor="w",
        )
        self.previous_forecast.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.previous_forecast.grid_remove()
        self.o_fields: list[dict[str, ttk.Entry]] = []
        self.o_links: list[str] = []
        self.o_roles: list[str] = []
        self.o_link_labels: list[ttk.Label] = []
        self.o_role_labels: list[ttk.Label] = []
        self.add_o_box()
        self.add_s_button = ttk.Button(pairs_section, text="＋ 保管薬を追加", command=self.add_s_box)
        self.add_s_button.grid(
            row=4, column=0, sticky="e", pady=(6, 0)
        )
        self.add_o_button = ttk.Button(pairs_section, text="＋ 新しい処方薬を追加", command=self.add_o_box)
        self.add_o_button.grid(
            row=4, column=1, sticky="e", pady=(6, 0)
        )

        judgement = ttk.LabelFrame(right, text="③ 自動判定", style="Stage.TLabelframe", padding=10)
        self.judgement_card = judgement
        judgement.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        judgement.columnconfigure(0, weight=1)
        self.judgement_text = ttk.Label(
            judgement,
            text="①保管と②受付の入力から、処理方法を自動判定します。",
            style="StageMessage.TLabel", anchor="center", justify="center", wraplength=620,
        )
        self.judgement_text.grid(row=0, column=0, sticky="ew", padx=10, pady=4)

        assessment = ttk.LabelFrame(right, text="④ 検算", style="Stage.TLabelframe", padding=10)
        self.assessment_card = assessment
        assessment.grid(row=1, column=0, sticky="ew", pady=4)
        assessment.columnconfigure(0, weight=1)
        self.assessment_text = ttk.Label(assessment, text="保管と受付を入力して「合包案を計算」を押してください。", style="StageMessage.TLabel", anchor="center", justify="center", wraplength=620)
        self.assessment_text.grid(row=0, column=0, sticky="ew", padx=10)
        self.assessment_controls = ttk.Frame(assessment)
        self.assessment_controls.grid(row=1, column=0, pady=(8, 0))
        self.assessment_calculate_button = ttk.Button(
            self.assessment_controls, text="合包案を計算", style="Primary.TButton",
            command=self._calculate_assessment_with_feedback,
        )
        self.assessment_calculate_button.pack(side="left", padx=4)
        self.assessment_return_button = ttk.Button(
            self.assessment_controls, text="差戻し", command=self.return_to_assessment,
        )
        self.assessment_return_button.pack(side="left", padx=4)
        self.assessment_return_button.pack_forget()
        decision = ttk.LabelFrame(right, text="⑤ 判断", style="Stage.TLabelframe", padding=10)
        self.decision_card = decision
        decision.grid(row=2, column=0, sticky="ew", pady=4)
        decision.columnconfigure(0, weight=1)
        self.result_instruction = ttk.Label(decision, text="④の検算結果を確認してください。", style="StageMessage.TLabel", anchor="center", justify="center", wraplength=620)
        self.result_instruction.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.assessment_actions = ttk.Frame(decision)
        self.assessment_actions.grid(row=1, column=0, pady=(4, 0))
        self.use_proposal_button = ttk.Button(
            self.assessment_actions, text="提案どおり実施", style="Primary.TButton", command=self.use_proposal
        )
        self.use_proposal_button.pack(side="left", padx=4)
        self.change_period_button = ttk.Button(
            self.assessment_actions, text="期間を変更して実施", command=self.show_actual_editor
        )
        self.change_period_button.pack(side="left", padx=4)
        self.return_button = ttk.Button(
            self.assessment_actions, text="差戻し", command=self.return_to_assessment
        )
        self.return_button.pack(side="left", padx=4)
        self.assessment_actions.grid_remove()
        self.actual_editor = ttk.Frame(decision)
        self.actual_editor.grid(row=2, column=0, sticky="ew")
        self.actual_editor.columnconfigure(1, weight=1)
        ttk.Label(self.actual_editor, text="開始").grid(row=0, column=0, padx=(0, 4))
        self.actual_start = ttk.Entry(self.actual_editor, width=13)
        self.actual_start.grid(row=0, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(
            self.actual_editor,
            text="日付",
            width=5,
            command=lambda: self.open_calendar(self.actual_start, self.actual_start_slot),
        ).grid(row=0, column=2, padx=(0, 4))
        self.actual_start_slot = tk.StringVar(value="朝")
        ttk.Button(self.actual_editor, textvariable=self.actual_start_slot, width=5, command=lambda: self.open_slot_picker(self.actual_start_slot)).grid(row=0, column=3)
        ttk.Label(self.actual_editor, text="終了").grid(row=1, column=0, padx=(0, 4), pady=(5, 0))
        self.actual_end = ttk.Entry(self.actual_editor, width=13)
        self.actual_end.grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=(5, 0))
        ttk.Button(
            self.actual_editor,
            text="日付",
            width=5,
            command=lambda: self.open_calendar(self.actual_end, self.actual_end_slot),
        ).grid(row=1, column=2, padx=(0, 4), pady=(5, 0))
        self.actual_end_slot = tk.StringVar(value="寝前")
        ttk.Button(self.actual_editor, textvariable=self.actual_end_slot, width=5, command=lambda: self.open_slot_picker(self.actual_end_slot)).grid(row=1, column=3, pady=(5, 0))
        ttk.Button(self.actual_editor, text="変更した期間で実施", style="Primary.TButton", command=self.calculate_result).grid(row=2, column=0, columnspan=4, pady=(8, 0))
        self.actual_editor.grid_remove()
        self.inventory_report_button = ttk.Button(
            decision, text="日数延長の報告を作成", style="Primary.TButton", command=self.calculate_result
        )
        self.inventory_report_button.grid(row=3, column=0, pady=(4, 0))
        self.inventory_report_button.grid_remove()

        result = ttk.LabelFrame(right, text="⑥ 結果", style="Stage.TLabelframe", padding=10)
        self.result_card = result
        result.grid(row=3, column=0, sticky="ew", pady=4)
        result.columnconfigure(0, weight=1)
        self.result_summary = ttk.Label(
            result,
            text="⑤で判断を確定すると、交付する期間をここへ表示します。",
            style="StageMessage.TLabel",
            anchor="center",
            justify="center",
            wraplength=620,
        )
        self.result_summary.grid(row=0, column=0, sticky="ew", padx=10, pady=4)

        delivery = ttk.LabelFrame(right, text="⑦ 交付　記録・出力", style="Stage.TLabelframe", padding=8)
        self.delivery_card = delivery
        delivery.grid(row=4, column=0, sticky="nsew", pady=(4, 0))
        delivery.columnconfigure(0, weight=1)
        delivery.rowconfigure(0, weight=1)
        lower = ttk.Panedwindow(delivery, orient="vertical")
        lower.grid(row=0, column=0, sticky="nsew")
        actions = ttk.Frame(lower, padding=(0, 4))
        actions.columnconfigure(3, weight=1)
        self.save_button = ttk.Button(actions, text="記録を保存", style="Primary.TButton", command=self.save)
        self.save_button.grid(row=0, column=0, sticky="w")
        self.pdf_button = ttk.Button(actions, text="90mm票を表示", command=self.open_90mm_report)
        self.pdf_button.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.odt_button = ttk.Button(actions, text="ODTへ保存・追記", command=self.save_odt)
        self.odt_button.grid(row=0, column=2, sticky="w", padx=(6, 0))
        # ⑦は操作を選ぶ場所として簡潔にし、内部状態の文言は表示しない。
        self.status = ttk.Label(actions, text="", foreground="#276746")
        preview_frame = ttk.LabelFrame(lower, text="実施報告プレビュー", style="Section.TLabelframe", padding=8)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview = tk.Text(preview_frame, height=16, wrap="none", font=("MS Gothic", 11))
        y_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=y_scroll.set)
        self.preview.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        lower.add(actions, weight=0)
        lower.add(preview_frame, weight=1)
        self._refresh_output_actions()
        self._refresh_legacy_odt_action()
        self.after_idle(self._update_workflow_colors)

    def _watch_workflow_fields(self, fields: dict) -> None:
        """入力変更を検知し、次に押す場所の案内色を更新する。"""
        for value in fields.values():
            if isinstance(value, ttk.Entry):
                value.bind("<KeyRelease>", lambda _event: self._workflow_inputs_changed(), add="+")
                value.bind("<FocusOut>", lambda _event: self._update_workflow_colors(), add="+")
        variables = [fields.get("mode"), fields.get("start_slot"), fields.get("end_slot"), fields.get("intermittent_slot"), fields.get("intermittent_rule")]
        variables.extend(fields.get("active_slots", {}).values())
        variables.extend(fields.get("weekdays", {}).values())
        for variable in variables:
            if isinstance(variable, tk.Variable):
                variable.trace_add("write", lambda *_args: self.after_idle(self._workflow_inputs_changed))

    def _workflow_inputs_changed(self) -> None:
        if not hasattr(self, "assessment_card"):
            return
        self.record_saved = False
        if self.assessment is not None or self.result is not None or self.inventory_record is not None:
            self.assessment = None
            self.result = None
            self.inventory_record = None
            self.inventory_report = None
            self.assessment_issues = ()
            _set_entry(self.actual_start, "")
            _set_entry(self.actual_end, "")
            self.assessment_actions.grid_remove()
            self.actual_editor.grid_remove()
            self.inventory_report_button.grid_remove()
            self.preview.delete("1.0", "end")
            self.assessment_text.configure(text="入力を変更しました。もう一度検算してください。", style="TLabel")
            self.result_instruction.configure(text="検算で合包案を計算してください。")
            self.result_summary.configure(text="⑤で判断を確定すると、交付する期間をここへ表示します。")
        self._update_workflow_colors()

    def _workflow_fields_complete(self, fields: dict) -> bool:
        if fields.get("inactive") or not _fields_have_input(fields):
            return False
        required = ("hospital", "start", "days", "end") if self.use_date_management.get() else ("hospital", "days")
        if not all(fields[key].get().strip() for key in required):
            return False
        if fields["mode"].get() == "間欠" and not fields["drug"].get().strip():
            return False
        return True

    def _assessment_inputs_ready(self) -> bool:
        actual_s = [
            fields for fields, forecast in zip(self.s_fields, self.s_forecast_flags)
            if not forecast and self._workflow_fields_complete(fields)
        ]
        actual_o = [fields for fields in self.o_fields if self._workflow_fields_complete(fields)]
        if actual_o and not all(fields.get("confirmed") for fields in actual_o):
            return False
        if self.inventory_corrections and not actual_o:
            return True
        if not self.use_date_management.get():
            return bool(actual_o)
        reception_only_targets = sum(
            fields["mode"].get() in ("連続", "間欠") and self.o_roles[index] == "合包対象"
            for index, fields in enumerate(self.o_fields)
            if self._workflow_fields_complete(fields)
        )
        # 保管が0件でも、受付に通常の合包対象薬が2件以上あれば、
        # 受付薬どうしで検算できる。保管カード自体は医療機関の管理枠として残す。
        return bool(actual_s and actual_o) or reception_only_targets >= 2

    def _automatic_judgement_text(self, input_ready: bool) -> str:
        """入力内容だけで処理の種類を示す。期間と帳尻の確定は④検算で行う。"""
        if not input_ready:
            return "①保管と②受付の入力から、処理方法を自動判定します。"
        if not self.use_date_management.get():
            return "合包なし\n服用時点を合わせ、今回受付分をすべて払い出します。"
        roles = [
            self.o_roles[index]
            for index, fields in enumerate(self.o_fields)
            if self._workflow_fields_complete(fields)
        ]
        if self.inventory_corrections and not roles:
            return "合包なし\n薬局預かり薬の修正報告を作成します。"
        if roles and all(role == "在庫積み増し" for role in roles):
            return "合包なし\n今回の受付分を加えて日数を延長します。"
        if roles and all(role == "臨時追加" for role in roles):
            return "合包なし\n臨時追加として報告を作成します。"

        candidates = []
        for fields, forecast in zip(self.s_fields, self.s_forecast_flags):
            if not forecast and self._workflow_fields_complete(fields) and fields["mode"].get() == "連続":
                candidates.append(fields)
        for index, fields in enumerate(self.o_fields):
            if (
                self._workflow_fields_complete(fields)
                and fields["mode"].get() == "連続"
                and self.o_roles[index] == "合包対象"
            ):
                candidates.append(fields)
        try:
            periods = [self._period(fields) for fields in candidates]
        except ValidationError:
            return "入力内容を確認してください\n修正すると自動判定を更新します。"
        has_common = any(
            left.start <= right.end
            and right.start <= left.end
            and bool(set(left.active_slots) & set(right.active_slots))
            for index, left in enumerate(periods)
            for right in periods[index + 1:]
        )
        if has_common:
            return "合包あり\n共通する期間と服用時点があります。"
        return "合包なし\n共通する期間または服用時点がありません。"

    def _update_workflow_colors(self) -> None:
        if not hasattr(self, "assessment_card"):
            return
        report_ready = bool(self.preview.get("1.0", "end").strip()) and (
            self.result is not None or self.inventory_record is not None
        )
        basic_ready = bool(self.patient.get().strip())
        assessment_ready = self.assessment is not None or self.inventory_record is not None
        input_ready = basic_ready and self._assessment_inputs_ready()
        inputs_locked = input_ready or assessment_ready or report_ready
        patient_locked = assessment_ready or report_ready

        has_held = any(
            not forecast and self._workflow_fields_complete(fields)
            for fields, forecast in zip(self.s_fields, self.s_forecast_flags)
        )
        has_received = any(self._workflow_fields_complete(fields) for fields in self.o_fields)
        if not basic_ready:
            workflow_states = ("current", "idle", "idle", "idle", "idle", "idle", "idle", "idle")
        elif report_ready and self.record_saved:
            workflow_states = ("done", "done", "done", "done", "done", "done", "done", "done")
        elif report_ready:
            workflow_states = ("done", "done", "done", "done", "done", "done", "done", "current")
        elif assessment_ready:
            workflow_states = ("done", "done", "done", "done", "done", "current", "idle", "idle")
        elif input_ready:
            workflow_states = ("done", "done", "done", "done", "current", "idle", "idle", "idle")
        elif has_received:
            workflow_states = ("done", ("done" if has_held else "idle"), "current", "idle", "idle", "idle", "idle", "idle")
        elif has_held:
            workflow_states = ("done", "current", "idle", "idle", "idle", "idle", "idle", "idle")
        else:
            workflow_states = ("done", "current", "idle", "idle", "idle", "idle", "idle", "idle")
        names = ("基本設定", "保管", "受付", "自動判定", "検算", "判断", "結果", "交付")
        numerals = ("⓪", "①", "②", "③", "④", "⑤", "⑥", "⑦")
        style_by_state = {"idle": "WorkflowIdle.TLabel", "current": "WorkflowCurrent.TLabel", "done": "WorkflowDone.TLabel"}
        suffix_by_state = {"idle": "", "current": " ▶", "done": " ✅"}
        for index, (label, name, state) in enumerate(zip(self.workflow_labels, names, workflow_states)):
            label_style = style_by_state[state]
            if index in (1, 2) and state == "current":
                label_style = "WorkflowInputCurrent.TLabel"
            label.configure(text=f"{numerals[index]} {name}{suffix_by_state[state]}", style=label_style)

        input_title_style = {
            "idle": "InputStageIdle.TLabel", "current": "InputStage.TLabel", "done": "InputStageDone.TLabel",
        }
        self.storage_stage_label.configure(
            text=f"① 保管{suffix_by_state[workflow_states[1]]}",
            style=input_title_style[workflow_states[1]],
        )
        self.reception_stage_label.configure(
            text=f"② 受付{suffix_by_state[workflow_states[2]]}",
            style=input_title_style[workflow_states[2]],
        )

        if not basic_ready:
            guidance = "⓪ 基本設定：対象者を入力し、施設情報と設定を確認してください。"
        elif report_ready and self.record_saved:
            guidance = "処理が完了しました。必要に応じて履歴を確認してください。"
        elif report_ready:
            guidance = "⑦ 交付：記録を保存し、必要な出力を行ってください。"
        elif assessment_ready:
            guidance = "⑤ 判断：検算結果を確認し、実施方法を選んでください。"
        elif input_ready:
            guidance = "④ 検算：自動判定を確認し、「合包案を計算」を押してください。"
        elif has_received:
            guidance = "② 受付：今回持参・処方された薬の内容を確認・入力してください。"
        elif has_held:
            guidance = "① 保管：保管中・処方待ちの枠を追加するか、今回処方が来た枠を受付へ送ってください。"
        else:
            guidance = "① 保管：現在保管している薬、または処方待ちを入力してください。"
        if workflow_states[0] == "current" or workflow_states[1] == "current" or workflow_states[2] == "current":
            guidance_style = "GuidanceInput.TLabel"
        elif "current" in workflow_states:
            guidance_style = "GuidanceCurrent.TLabel"
        elif report_ready and self.record_saved:
            guidance_style = "GuidanceDone.TLabel"
        else:
            guidance_style = "Guidance.TLabel"
        self.next_action.configure(text=f"次の手順：{guidance}", style=guidance_style)
        self.basic_section.configure(
            text="⓪ 基本設定 ✅" if basic_ready else "⓪ 基本設定",
            style="BasicLocked.TLabelframe" if patient_locked else ("BasicDone.TLabelframe" if basic_ready else "BasicCurrent.TLabelframe"),
        )
        for subsection in self.basic_subsections:
            subsection.configure(style="BasicSubDone.TLabelframe" if basic_ready else "BasicSubCurrent.TLabelframe")
        self._apply_basic_visual(basic_ready)
        for index, fields in enumerate(self.s_fields):
            filled = basic_ready and _fields_have_input(fields)
            self._apply_pair_visual(fields, index, confirmed=inputs_locked) if filled else self._apply_period_idle_visual(fields)
        for index, fields in enumerate(self.o_fields):
            filled = basic_ready and _fields_have_input(fields)
            self._apply_pair_visual(fields, index, True, bool(fields.get("confirmed"))) if filled else self._apply_period_idle_visual(fields)

        self.judgement_card.configure(
            text="③ 自動判定 ✅" if input_ready else "③ 自動判定",
            style="Complete.TLabelframe" if input_ready else "Stage.TLabelframe",
        )
        self._color_workflow_card(self.judgement_card, "done" if input_ready else "idle")
        self.judgement_text.configure(
            text=self._automatic_judgement_text(input_ready),
            style="StageMessageComplete.TLabel" if input_ready else "StageMessage.TLabel",
        )
        self.assessment_card.configure(
            text="④ 検算 ✅" if assessment_ready else ("④ 検算 ▶" if input_ready else "④ 検算"),
            style="Complete.TLabelframe" if assessment_ready else ("Next.TLabelframe" if input_ready else "Stage.TLabelframe")
        )
        self._color_workflow_card(self.assessment_card, "done" if assessment_ready else ("current" if input_ready else "idle"))
        self.assessment_text.configure(style="StageMessageComplete.TLabel" if assessment_ready else ("StageMessageCurrent.TLabel" if input_ready else "StageMessage.TLabel"))
        self.assessment_calculate_button.configure(
            style="Next.TButton" if input_ready and not assessment_ready else "Primary.TButton",
            state="normal" if input_ready else "disabled",
        )
        if assessment_ready:
            if not self.assessment_return_button.winfo_manager():
                self.assessment_return_button.pack(side="left", padx=4)
        else:
            self.assessment_return_button.pack_forget()
        self.decision_card.configure(
            text="⑤ 判断 ✅" if report_ready else ("⑤ 判断 ▶" if assessment_ready else "⑤ 判断"),
            style="Complete.TLabelframe" if report_ready else ("Next.TLabelframe" if assessment_ready else "Stage.TLabelframe")
        )
        self._color_workflow_card(self.decision_card, "done" if report_ready else ("current" if assessment_ready else "idle"))
        self.result_instruction.configure(style="StageMessageComplete.TLabel" if report_ready else ("StageMessageCurrent.TLabel" if assessment_ready else "StageMessage.TLabel"))
        self.result_card.configure(
            text="⑥ 結果 ✅" if report_ready else "⑥ 結果",
            style="Complete.TLabelframe" if report_ready else "Stage.TLabelframe"
        )
        self._color_workflow_card(self.result_card, "done" if report_ready else "idle")
        self.result_summary.configure(style="StageMessageComplete.TLabel" if report_ready else "StageMessage.TLabel")
        self.delivery_card.configure(
            text="⑦ 交付　記録・出力 ✅" if self.record_saved else ("⑦ 交付　記録・出力 ▶" if report_ready else "⑦ 交付　記録・出力"),
            style="Complete.TLabelframe" if self.record_saved else ("Next.TLabelframe" if report_ready else "Stage.TLabelframe")
        )
        self._color_workflow_card(self.delivery_card, "done" if self.record_saved else ("current" if report_ready else "idle"))
        result_action_style = "Next.TButton" if assessment_ready and not report_ready else "Primary.TButton"
        self.use_proposal_button.configure(style=result_action_style)
        self.change_period_button.configure(style=result_action_style)
        self.return_button.configure(style=result_action_style)
        self.inventory_report_button.configure(style=result_action_style)
        output_style = "Ready.TButton" if report_ready else None
        self.save_button.configure(style=output_style or "Primary.TButton")
        for button in (self.pdf_button, self.odt_button):
            button.configure(style=output_style or "TButton")
        self._apply_stage_locks(inputs_locked, patient_locked)

    def _refresh_output_actions(self) -> None:
        """基本設定の選択に応じ、不要な出力ボタンを交付工程から隠す。"""
        for button, visible in (
            (self.pdf_button, self.show_pdf_action.get()),
            (self.odt_button, self.show_odt_action.get()),
        ):
            if visible:
                if not button.winfo_manager():
                    button.grid()
            else:
                button.grid_remove()

    def _legacy_odt_visibility_changed(self) -> None:
        try:
            save_preferences({"show_legacy_odt": self.show_legacy_odt_action.get()}, DEFAULT_DATA_DIR)
        except OSError as exc:
            messagebox.showerror("設定を保存できません", str(exc))
        self._refresh_legacy_odt_action()

    def _pharmacy_changed(self) -> None:
        try:
            save_preferences({"pharmacy": self.pharmacy.get().strip()}, DEFAULT_DATA_DIR)
        except OSError as exc:
            messagebox.showerror("設定を保存できません", str(exc))

    def _refresh_legacy_odt_action(self) -> None:
        if self.show_legacy_odt_action.get():
            if not self.legacy_odt_button.winfo_manager():
                self.legacy_odt_button.grid(row=1, column=0, sticky="ew")
        else:
            self.legacy_odt_button.grid_remove()

    def _current_patient_directory(self) -> Path:
        return patient_directory(
            self.patient.get().strip() or "対象者",
            DEFAULT_DATA_DIR,
            self.patient_id.get().strip(),
        )

    def _update_patient_folder_label(self) -> None:
        self.patient_folder_label.configure(text=f"保存先：{self._current_patient_directory()}")

    def _patient_identity_changed(self) -> None:
        self._update_patient_folder_label()
        self._update_workflow_colors()

    def _color_workflow_card(self, card: ttk.LabelFrame, state: str) -> None:
        """工程カード内の余白と説明ラベルも、カード本体と同じ案内色にする。"""
        frame_style = {"idle": "TFrame", "current": "Next.TFrame", "done": "Complete.TFrame"}[state]
        label_style = {"idle": "TLabel", "current": "Next.TLabel", "done": "Complete.TLabel"}[state]
        subframe_style = {"idle": "StageSub.TLabelframe", "current": "NextSub.TLabelframe", "done": "CompleteSub.TLabelframe"}[state]
        paned_style = {"idle": "TPanedwindow", "current": "Next.TPanedwindow", "done": "Complete.TPanedwindow"}[state]

        def apply(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                if isinstance(child, ttk.LabelFrame):
                    child.configure(style=subframe_style)
                elif isinstance(child, ttk.Panedwindow):
                    child.configure(style=paned_style)
                elif isinstance(child, ttk.Frame):
                    child.configure(style=frame_style)
                elif isinstance(child, ttk.Label):
                    # ③〜⑥の主案内は工程色にかかわらず同じ文字サイズを保つ。
                    if child not in (self.judgement_text, self.assessment_text, self.result_instruction, self.result_summary):
                        child.configure(style=label_style)
                apply(child)

        apply(card)

    def _calculate_assessment_with_feedback(self) -> bool:
        """検算後に、次の工程を案内する色へ直ちに切り替える。"""
        completed = self.calculate_assessment()
        self._update_workflow_colors()
        return completed

    def open_90mm_report(self) -> None:
        if not self.preview.get("1.0", "end").strip() and not self.calculate_result():
            return
        report = self._output_report_text()
        if not report:
            messagebox.showwarning("実施報告を確認してください", "印刷する実施報告がありません。")
            return
        printable_report = self._print_report_text(report)
        table_data = self._print_table_data()
        height_mm = estimate_90mm_height(printable_report, table_data)
        if height_mm > MAX_DRIVER_PRINTABLE_HEIGHT_MM and not messagebox.askyesno(
            "90mm票が用紙長を超えます",
            f"票の長さは約{height_mm:.1f}mmです。現在の印刷可能長{MAX_DRIVER_PRINTABLE_HEIGHT_MM:.1f}mmを超えるため、"
            "末尾が切れる可能性があります。PDFを表示しますか？",
        ):
            return
        output = self._current_patient_directory() / "print" / f"{datetime.now():%Y%m%d_%H%M%S}_90mm.pdf"
        try:
            export_90mm_report(
                printable_report,
                output,
                table_data,
                self.pharmacy.get(),
            )
            os.startfile(str(output))
        except (OSError, ValueError) as exc:
            messagebox.showerror("90mm票を作成できません", str(exc))
            return
        self.status.configure(text=f"90mm票を作成しました：{output.name}")

    def save_odt(self) -> None:
        if not self.preview.get("1.0", "end").strip() and not self.calculate_result():
            return
        # ODT and PDF are the same external report. JSON remains the internal record.
        report = self._output_report_text()
        if not report:
            messagebox.showwarning("実施報告を確認してください", "保存する実施報告がありません。")
            return
        default_dir = self._current_patient_directory()
        default_dir.mkdir(parents=True, exist_ok=True)
        output = default_dir / "外来服薬支援.odt"
        if self.odt_path and self.odt_path.is_file() and self.odt_path.resolve() != output.resolve() and not output.exists():
            shutil.copy2(self.odt_path, output)
        try:
            output, page_number = export_or_append_odt(
                self._print_report_text(report),
                output,
                self._print_table_data(),
                self.pharmacy.get(),
                self._odt_metadata(),
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("ODTを保存できません", str(exc))
            return
        self.odt_path = output.resolve()
        self.status.configure(text=f"最新票をODTの先頭へ保存しました（全{page_number}ページ）：{output.name}")

    def _odt_metadata(self) -> dict[str, str]:
        metadata = {
            "Schema": "1",
            "AppVersion": VERSION,
            "PatientId": self.patient_id.get().strip(),
            "DeliveryMode": self.delivery_mode.get(),
            "UnmatchedSlotMode": self.unmatched_slot_mode.get(),
            "Intermittent": "1" if self.show_intermittent.get() else "0",
            "SlotDetail": "1" if self.use_slot_detail.get() else "0",
            "AppointmentDates": "1" if self.use_appointment_dates.get() else "0",
            "DateManagement": "1" if self.use_date_management.get() else "0",
            "Appointments": json.dumps(
                self._appointment_records(), ensure_ascii=False, separators=(",", ":")
            ),
        }
        if self.result is not None:
            record = period_result_to_dict(
                self.result, self.patient.get(), self.facility.get(), self.service_date.get().strip()
            )
            self._retain_waiting_cards(record)
            if not self.use_date_management.get():
                record["next_creations"] = []
                record["post_delivery_states"] = []
            metadata["NextCreations"] = json.dumps(
                record.get("next_creations", []), ensure_ascii=False, separators=(",", ":")
            )
            metadata["PostDeliveryStates"] = json.dumps(
                record.get("post_delivery_states", []), ensure_ascii=False, separators=(",", ":")
            )
            delivered_intermittent, remaining_intermittent = self._split_intermittent()
            metadata["IntermittentRemaining"] = json.dumps(
                [self._intermittent_record(item, dates) for item, dates in remaining_intermittent],
                ensure_ascii=False, separators=(",", ":"),
            )
            metadata["IntermittentForecasts"] = json.dumps(
                [
                    {
                        "hospital": item["hospital"], "drug": item["drug"], "slot": item["slot"],
                        "rule": item["rule"], "interval_days": item.get("interval_days", 1),
                        "weekdays": list(item.get("weekdays", ())),
                        "appointment_date": item.get("appointment_date", ""),
                        "dose_dates": [value.isoformat() for value in next_dose_dates(item, 2)],
                    }
                    for item in self.intermittent_items
                ],
                ensure_ascii=False, separators=(",", ":"),
            )
        return metadata

    def _retain_waiting_cards(self, record: dict) -> None:
        """Keep named zero-stock cards even when they did not join this delivery."""
        forecasts = list(next_creation_candidates(record))
        represented = {item['hospital'] for item in forecasts}
        represented.update(item['hospital'] for item in record.get('result', {}).get('uncombined', []))
        if self.result is not None:
            represented.update(source.hospital for source in self.result.assessment.sources)
        for fields, forecast in zip(self.s_fields, self.s_forecast_flags):
            hospital = fields['hospital'].get().strip()
            start = fields['start'].get().strip()
            if (not (forecast or fields.get('inactive')) or fields.get('forecast_received')
                    or not hospital or not start or hospital in represented
                    or fields['mode'].get() != '連続'):
                continue
            selected = [slot for slot, variable in fields['active_slots'].items() if variable.get()]
            forecasts.append({
                'hospital': hospital, 'start': start, 'start_slot': fields['start_slot'].get(),
                'active_slots': selected or [slot for slot in ('朝', '昼', '夕', '寝前')
                                            if slot in fields.get('prescribed_slots', ())],
                'appointment_date': fields['appointment'].get().strip(),
            })
            represented.add(hospital)
        if forecasts:
            record['next_creations'] = forecasts
            record['next_creation'] = forecasts[0]

    def open_history(self) -> None:
        HistoryDialog(self)

    def open_intermittent(self) -> None:
        IntermittentManagerDialog(
            self, self.intermittent_items, self.intermittent_forecasts, self._set_intermittent_items
        )

    def _delivery_mode_changed(self) -> None:
        self.record_saved = False
        self.assessment = None
        self.result = None
        self.inventory_record = None
        self.inventory_report = None
        self.assessment_issues = ()
        _set_entry(self.actual_start, "")
        _set_entry(self.actual_end, "")
        self.preview.delete("1.0", "end")
        self.assessment_actions.grid_remove()
        self.actual_editor.grid_remove()
        self.inventory_report_button.grid_remove()
        if self._release_all_selected():
            message = "合包可能部分をまとめ、残りも単独薬としてすべて交付します。"
            self.unmatched_mode.configure(state="disabled")
        elif self._release_unmatched_selected():
            message = "重ならない服用時点は合包範囲外とし、その時点の薬は全期間分を渡します。"
            self.unmatched_mode.configure(state="readonly")
        else:
            message = "合包できなかった分は薬局預かりとして次回へ引き継ぎます。"
            self.unmatched_mode.configure(state="readonly")
        self.assessment_text.configure(text=message, style="TLabel")
        self.result_instruction.configure(text="④で合包案を計算してください。")
        self.result_summary.configure(text="⑤で判断を確定すると、交付する期間をここへ表示します。")
        self.status.configure(text=f"交付方法を「{self.delivery_mode.get()}」に変更しました。")
        self._update_workflow_colors()

    def _release_all_selected(self) -> bool:
        return not self.use_date_management.get() or self.delivery_mode.get() in (DELIVERY_RELEASE, "すべて払い出し")

    def _release_unmatched_selected(self) -> bool:
        return self.unmatched_slot_mode.get() == UNMATCHED_RELEASE

    def _display_options_changed(self) -> None:
        date_enabled = self.use_date_management.get()
        self.delivery_mode_widget.configure(state="readonly" if date_enabled else "disabled")
        self.unmatched_mode.configure(state="readonly" if date_enabled else "disabled")
        for fields in (*self.s_fields, *self.o_fields):
            self._apply_display_options(fields)
        self.intermittent_summary.grid() if self.show_intermittent.get() else self.intermittent_summary.grid_remove()
        self.assessment = None
        self.result = None
        self.inventory_record = None
        self.inventory_report = None
        self.assessment_issues = ()
        self.record_saved = False
        _set_entry(self.actual_start, "")
        _set_entry(self.actual_end, "")
        self.preview.delete("1.0", "end")
        self.assessment_actions.grid_remove()
        self.actual_editor.grid_remove()
        self.inventory_report_button.grid_remove()
        self.result_summary.configure(text="⑤で判断を確定すると、交付する期間をここへ表示します。")
        self.status.configure(text="入力条件を変更しました。内部の入力内容は保持しています。")
        self._update_workflow_colors()

    def _date_management_changed(self) -> None:
        """日付を使わない患者では、服用時点と日数だけを入力対象にする。"""
        self._display_options_changed()
        enabled = self.use_date_management.get()
        self.delivery_mode_widget.configure(state="readonly" if enabled else "disabled")
        self.unmatched_mode.configure(state="readonly" if enabled else "disabled")
        if enabled:
            self.assessment_text.configure(text="日付管理を有効にしました。もう一度検算してください。")
        else:
            self.assessment_text.configure(
                text="日付管理なし：受付薬の服用時点を合わせ、今回分をすべて払い出します。"
            )
        self.status.configure(text="日付管理の条件を変更しました。入力内容は保持しています。")

    def _apply_display_options(self, fields: dict) -> None:
        if self.use_appointment_dates.get():
            fields["appointment_label"].grid()
            fields["appointment"].grid()
            fields["appointment_button"].grid()
        else:
            fields["appointment_label"].grid_remove()
            fields["appointment"].grid_remove()
            fields["appointment_button"].grid_remove()
        date_widgets = (
            fields["start_label"], fields["start"], fields["start_date_button"],
            fields["end_label"], fields["end"], fields["end_date_button"],
        )
        for widget in date_widgets:
            widget.grid() if self.use_date_management.get() else widget.grid_remove()
        if fields["mode"].get() == "連続":
            if self.use_slot_detail.get():
                if self.use_date_management.get():
                    fields["start_slot_button"].grid()
                    fields["end_slot_button"].grid()
                else:
                    fields["start_slot_button"].grid_remove()
                    fields["end_slot_button"].grid_remove()
                fields["active_label"].grid()
                fields["active_frame"].grid()
            else:
                fields["start_slot_button"].grid_remove()
                fields["end_slot_button"].grid_remove()
                fields["active_label"].grid_remove()
                fields["active_frame"].grid_remove()
        if fields["mode"].get() == "間欠" and not self.show_intermittent.get():
            fields["hospital"].master.grid_remove()
        elif fields["hospital"].master.winfo_manager() == "":
            fields["hospital"].master.grid()

    def _set_intermittent_items(self, items: list[dict], forecasts=None) -> None:
        self.intermittent_items = [dict(item) for item in items]
        if forecasts is not None:
            self.intermittent_forecasts = [dict(item) for item in forecasts]
        if items:
            names = "、".join(f"{item['hospital']} {item['drug']}" for item in items)
            self.intermittent_summary.configure(text=f"間欠服用薬：{len(items)}件　{names}", foreground="#276746")
        else:
            forecast_text = f"　処方待ち{len(self.intermittent_forecasts)}件" if self.intermittent_forecasts else ""
            self.intermittent_summary.configure(text=f"間欠服用薬：なし{forecast_text}", foreground="#536A7A")
        self._refresh_preview_extras()

    def _refresh_preview_extras(self) -> None:
        if self.result is not None:
            self.calculate_result()
            return
        current = self.preview.get("1.0", "end").strip()
        marker = "■間欠服用薬"
        changed = False
        if marker in current:
            current = current.split(marker, 1)[0].rstrip()
            changed = True
        if current and (changed or self.intermittent_items):
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", current + build_intermittent_report(self.intermittent_items))

    def _output_report_text(self) -> str:
        report = self.preview.get("1.0", "end").strip()
        if self.print_corrections.get():
            return report
        marker = "★処方変更・残薬確認に伴う薬局預かり薬の修正"
        return report.split(marker, 1)[0].rstrip()

    def _print_table_data(self) -> dict | None:
        if self.result is None:
            if self.inventory_record and self.inventory_record.get("operation") == "inventory_update":
                return _inventory_print_table_data(self.inventory_record)
            return None
        hospital_order = [period.hospital for period in self.result.assessment.sources]
        if self.show_intermittent.get():
            hospital_order.extend(item["hospital"] for item in self.intermittent_items)
        icon_by_hospital = _hospital_symbol_map(hospital_order)
        sources = [
            _print_period_row(period, icon_by_hospital[period.hospital])
            for period in self.result.assessment.sources
        ]
        uncombined = [
            _print_period_row(period, icon_by_hospital[period.hospital])
            for period in self.result.uncombined
        ]
        delivered_period_values = delivered_periods(self.result)
        slot_use_count = {
            slot: sum(slot in period.active_slots for period in self.result.assessment.sources)
            for slot in ("朝", "昼", "夕", "寝前")
        }
        shared_slots = {slot for slot, count in slot_use_count.items() if count >= 2}
        if self.result.release_unmatched_slots and not self.result.release_all:
            common_period_values = [
                period for period in delivered_period_values
                if any(slot in shared_slots for slot in period.active_slots)
            ]
            single_period_values = [
                period for period in delivered_period_values
                if period.active_slots and all(slot not in shared_slots for slot in period.active_slots)
            ]
        else:
            common_period_values = list(delivered_period_values)
            single_period_values = []
        delivered_rows = [_print_period_row(period, icon_by_hospital[period.hospital]) for period in common_period_values]
        delivered_intermittent, remaining_intermittent = self._split_intermittent()
        slots = ("朝", "昼", "夕", "寝前")
        delivery_counts = {
            slot: max((row["counts"].get(slot, 0) for row in delivered_rows), default=0)
            for slot in slots
        }
        executed = {
            "hospital": "合包部分" if self.result.release_all else ("合包交付" if single_period_values else "今回お渡し分"),
            "symbols": "".join(dict.fromkeys(
                [icon_by_hospital[period.hospital] for period in common_period_values]
                + [icon_by_hospital[item["hospital"]] for item, dates in delivered_intermittent if dates]
            )),
            "period": (
                f"{self.result.actual_start.month}/{self.result.actual_start.day}"
                f"{'寝' if self.result.actual_start_slot == '寝前' else self.result.actual_start_slot}-"
                f"{self.result.actual_end.month}/{self.result.actual_end.day}"
                f"{'寝' if self.result.actual_end_slot == '寝前' else self.result.actual_end_slot}"
            ),
            "active_slots": [slot for slot in slots if delivery_counts[slot]],
            "counts": delivery_counts,
        }
        executed_extra = []
        for period in single_period_values:
            row = _print_period_row(period, icon_by_hospital[period.hospital])
            executed_extra.append({
                "hospital": "単独交付",
                "symbols": row["mark"],
                "period": row["period"],
                "active_slots": row["active_slots"],
                "counts": row["counts"],
            })
        if not any(delivery_counts.values()) and executed_extra:
            executed = executed_extra.pop(0)
        if self.result.release_all:
            sorting_rows = _release_all_sorting_rows(self.result, icon_by_hospital)
            if sorting_rows:
                executed = sorting_rows[0]
                executed_extra = sorting_rows[1:]
        if not self.use_date_management.get():
            for row in (*sources, *uncombined, executed, *executed_extra):
                row["period"] = "日付管理なし"
        intermittent_all = []
        for item in self.intermittent_items if self.show_intermittent.get() else ():
            dates = item_dates(item)
            intermittent_all.append({
                "title": f"{icon_by_hospital[item['hospital']]}{item['hospital']}　{item['drug']}　{item['slot']}　{len(dates)}包（{rule_label(item)}）",
                "dates": format_dates_compact(dates),
            })
        intermittent = []
        for item, dates in delivered_intermittent:
            intermittent.append({
                "title": f"{icon_by_hospital[item['hospital']]}{item['hospital']}　{item['drug']}　{item['slot']}　{len(dates)}包（{rule_label(item)}）",
                "dates": format_dates_compact(dates),
            })
        intermittent_remaining = []
        for item, dates in remaining_intermittent:
            intermittent_remaining.append({
                "title": f"{icon_by_hospital[item['hospital']]}{item['hospital']}　{item['drug']}　{item['slot']}　{len(dates)}包（{rule_label(item)}）",
                "dates": format_dates_compact(dates),
            })
        return {
            "release_all": self.result.release_all,
            "sources": sources,
            "executed": executed,
            "executed_extra": executed_extra,
            "uncombined": uncombined,
            "intermittent_all": intermittent_all,
            "intermittent": intermittent,
            "intermittent_remaining": intermittent_remaining,
        }

    def _split_intermittent(self):
        """Split intermittent doses at the actual delivery boundary."""
        delivered = []
        remaining = []
        intermittent_option = getattr(self, "show_intermittent", None)
        if intermittent_option is not None and not intermittent_option.get():
            return delivered, remaining
        if self.result is None:
            return delivered, [(item, item_dates(item)) for item in self.intermittent_items]
        if getattr(self.result, "release_all", False):
            return [(item, item_dates(item)) for item in self.intermittent_items], remaining
        slots = ("朝", "昼", "夕", "寝前")
        # 連続薬と同じく、開始日は朝から「今回お渡し分」とする。
        # A/Pの開始が寝前でも、同日に交付する単独薬を預かりへ残さない。
        start_point = self.result.actual_start.toordinal() * 4 + slots.index("朝")
        end_point = self.result.actual_end.toordinal() * 4 + slots.index(self.result.actual_end_slot)
        for item in self.intermittent_items:
            slot_index = slots.index(item["slot"])
            before = []
            after = []
            for dose_date in item_dates(item):
                point = dose_date.toordinal() * 4 + slot_index
                (before if start_point <= point <= end_point else after).append(dose_date)
            if before:
                delivered.append((item, before))
            if after:
                remaining.append((item, after))
        return delivered, remaining

    def _sync_intermittent_from_period_boxes(self) -> None:
        if not self.show_intermittent.get():
            return
        held_items = []
        incoming_items = []
        forecasts = []
        for fields, forecast in zip(self.s_fields, self.s_forecast_flags):
            if fields["mode"].get() != "間欠" or not fields["hospital"].get().strip():
                continue
            item = self._intermittent_item_from_fields(fields, allow_waiting=forecast)
            if forecast:
                if not fields.get("forecast_received"):
                    preview = dict(item)
                    preview["count"] = 2
                    preview.pop("dose_dates", None)
                    preview["dose_dates"] = [value.isoformat() for value in item_dates(preview)]
                    forecasts.append(preview)
                continue
            held_items.append(item)
        for index, fields in enumerate(self.o_fields):
            if fields["mode"].get() == "間欠" and fields["hospital"].get().strip():
                item = self._intermittent_item_from_fields(fields)
                item["processing_role"] = self.o_roles[index]
                incoming_items.append(item)
        items = list(held_items)
        for incoming in incoming_items:
            matches = [
                (index, held)
                for index, held in enumerate(items)
                if held.get("hospital") == incoming.get("hospital")
                and held.get("drug") == incoming.get("drug")
            ]
            if len(matches) > 1:
                raise ValidationError(
                    f"{incoming.get('hospital', '')} {incoming.get('drug', '')}のS在庫が複数あります。"
                    "統合対象を1枠に整理してください。"
                )
            if matches:
                match_index, held = matches[0]
                try:
                    items[match_index] = merge_intermittent_extension(held, incoming)
                except ValueError as exc:
                    raise ValidationError(str(exc)) from exc
            else:
                items.append(incoming)
        self.intermittent_items = items
        self.intermittent_forecasts = forecasts

    def _intermittent_record(self, item: dict, dates: list[date]) -> dict:
        value = dict(item)
        value["first_date"] = dates[0].isoformat()
        value["count"] = len(dates)
        value["dose_dates"] = [dose_date.isoformat() for dose_date in dates]
        return value

    def _integrate_intermittent_report(self, report: str) -> str:
        if not self.show_intermittent.get() or not self.intermittent_items:
            return report
        delivered, remaining = self._split_intermittent()
        incoming_lines = ["■間欠服用薬"]
        for item in self.intermittent_items:
            dates = item_dates(item)
            incoming_lines.extend((
                f"{item['hospital']}　{item['drug']}　{item['slot']}　{len(dates)}包（{rule_label(item)}）",
                f"服用日：{format_dates_compact(dates)}",
            ))
        delivered_lines = []
        for item, dates in delivered:
            delivered_lines.extend((
                f"{item['hospital']}　{item['drug']}　{item['slot']}　{len(dates)}包（{rule_label(item)}）",
                f"服用日：{format_dates_compact(dates)}",
            ))
        remaining_lines = []
        for item, dates in remaining:
            remaining_lines.extend((
                f"{item['hospital']}　{item['drug']}　{item['slot']}　{len(dates)}包（{rule_label(item)}）",
                f"服用日：{format_dates_compact(dates)}　→ 次回へ引継ぎ",
            ))
        lines = report.rstrip().splitlines()
        delivery_index = next((i for i, line in enumerate(lines) if line.startswith("★今回お渡し分")), len(lines))
        lines[delivery_index:delivery_index] = ["", *incoming_lines, ""]
        wait_index = next((i for i, line in enumerate(lines) if line.startswith("★次の処方待ち")), len(lines))
        if remaining_lines:
            lines[wait_index:wait_index] = [*remaining_lines, ""]
        wait_index = next((i for i, line in enumerate(lines) if line.startswith("★次の処方待ち")), len(lines))
        if delivered_lines:
            held_index = next((i for i, line in enumerate(lines) if line.startswith("★薬局預かり分")), wait_index)
            lines[held_index:held_index] = [*delivered_lines, ""]
        forecasts = []
        for item in self.intermittent_items:
            if any(remaining_item is item for remaining_item, _dates in remaining):
                continue
            future = next_dose_dates(item, 2)
            if future:
                forecasts.extend((
                    f"{item['hospital']}　{item['drug']}　{item['slot']}（{rule_label(item)}）",
                    f"服用日：{format_dates(future)}～　→ 次回へ引継ぎ",
                ))
        for item in self.intermittent_forecasts:
            future = item_dates(item)
            if future:
                forecasts.extend((
                    f"{item['hospital']}　{item['drug']}　{item['slot']}（{rule_label(item)}）",
                    f"服用日：{format_dates(future[:2])}～　→ 次回へ引継ぎ",
                ))
        if forecasts:
            wait_index = next((i for i, line in enumerate(lines) if line.startswith("★次の処方待ち")), len(lines))
            insert_at = wait_index + 1
            while insert_at < len(lines) and not lines[insert_at].startswith(("★", "■")):
                insert_at += 1
            lines[insert_at:insert_at] = [*forecasts, ""]
        return "\n".join(lines) + "\n"

    def _print_report_text(self, report: str) -> str:
        source_lines = report.splitlines()
        output: list[str] = []
        hospital_order = [
            fields["hospital"].get().strip()
            for fields in (*self.s_fields, *self.o_fields)
            if fields["hospital"].get().strip()
        ]
        if self.result is not None:
            hospital_order.extend(period.hospital for period in self.result.assessment.sources)
        hospital_order.extend(item.get("hospital", "") for item in self.intermittent_items)
        symbols = _hospital_symbol_map(hospital_order)
        in_next_wait = False
        skip_indices: set[int] = set()
        for index, line in enumerate(source_lines):
            if index in skip_indices:
                continue
            if index == 0 and "【外来服薬支援】" in line:
                suffix = line.split("【外来服薬支援】", 1)[1]
                patient_name = self.patient.get().strip()
                if patient_name.endswith("様"):
                    patient_name = patient_name[:-1]
                output.append(f"{patient_name}様　【外来服薬支援】{suffix}")
                continue
            if line.startswith("施設："):
                continue
            if line == "★次の処方待ち":
                output.append("★薬局コメント")
                in_next_wait = True
                continue
            if in_next_wait and (line.startswith("★") or line.startswith("■")):
                in_next_wait = False
            if in_next_wait and line.startswith("服用日："):
                output.append(line.split("　→", 1)[0])
                continue
            if in_next_wait and "（" in line and "）" in line:
                hospital = line.partition("　")[0]
                details = [part.strip() for part in line.split("　") if part.strip()]
                next_line = source_lines[index + 1] if index + 1 < len(source_lines) else ""
                if len(details) >= 3 and next_line.startswith("服用日："):
                    dates = next_line.split("：", 1)[1].split("　→", 1)[0].strip()
                    drug = details[1]
                    slot_rule = details[-1]
                    slot = slot_rule.split("（", 1)[0]
                    rule = slot_rule.split("（", 1)[1].rsplit("）", 1)[0]
                    output.append(
                        f"次の処方待ちは、{symbols.get(hospital, '')}{hospital}の{drug}です。"
                        f"開始日は、{dates}の{slot}（{rule}）となります。"
                    )
                    skip_indices.add(index + 1)
                else:
                    output.append(f"{symbols.get(hospital, '')}{line}")
                continue
            if in_next_wait and line.strip():
                hospital, separator, detail = line.partition("　")
                start_text = detail.split("　→", 1)[0].strip() if separator else ""
                output.append(f"処方待ちは、{symbols.get(hospital, '')}{hospital}です。次の開始日は{start_text}となります。")
                continue
            output.append(line)
        appointment_lines = self._appointment_comment_lines(symbols)
        if appointment_lines:
            insert_at = next(
                (i for i, line in enumerate(output) if line.startswith(("★処方変更", "★臨時追加"))),
                len(output),
            )
            output[insert_at:insert_at] = ["★受診日情報", *appointment_lines]
        return "\n".join(output)

    def _appointment_comment_lines(self, symbols: dict[str, str]) -> list[str]:
        if not self.use_appointment_dates.get():
            return []
        remaining_hospitals = set()
        exhausted_hospitals = set()
        if self.result is not None:
            remaining_hospitals.update(period.hospital for period in self.result.uncombined)
            _delivered, intermittent_remaining = self._split_intermittent()
            remaining_hospitals.update(item.get("hospital", "") for item, dates in intermittent_remaining if dates)
            exhausted_hospitals.update(
                period.hospital for period in self.result.assessment.sources
                if period.hospital != INTERMITTENT_BOUNDARY_HOSPITAL
            )
            exhausted_hospitals.update(item.get("hospital", "") for item in self.intermittent_items)
            exhausted_hospitals.difference_update(remaining_hospitals)
        nearest: dict[str, date] = {}
        for fields in (*self.s_fields, *self.o_fields):
            hospital = fields["hospital"].get().strip()
            value = fields["appointment"].get().strip()
            if not hospital or not value or hospital not in exhausted_hospitals:
                continue
            try:
                appointment = parse_date(value)
            except ValidationError as exc:
                raise ValidationError(f"{hospital}の受診日を確認してください。") from exc
            nearest[hospital] = min(appointment, nearest.get(hospital, appointment))
        return [
            f"{symbols.get(hospital, '')}{hospital}の次の受診日は、{value.month}/{value.day}頃です。"
            for hospital, value in sorted(nearest.items(), key=lambda item: item[1])
        ]

    def _appointment_records(self) -> list[dict]:
        records = []
        seen = set()
        for section, fields_list in (("S", self.s_fields), ("O", self.o_fields)):
            for fields in fields_list:
                hospital = fields["hospital"].get().strip()
                appointment = fields["appointment"].get().strip()
                key = (hospital, appointment)
                if not hospital or not appointment or key in seen:
                    continue
                seen.add(key)
                records.append({"hospital": hospital, "appointment_date": appointment, "section": section})
        return records

    def _input_options_record(self) -> dict:
        return {
            "intermittent": self.show_intermittent.get(),
            "slot_detail": self.use_slot_detail.get(),
            "appointment_dates": self.use_appointment_dates.get(),
            "date_management": self.use_date_management.get(),
            "unmatched_slot_mode": self.unmatched_slot_mode.get(),
        }

    def _period_box(
        self,
        parent: ttk.Frame,
        column: int,
        title: str,
        start_label: str = "開始日",
        row: int = 0,
        outer_padding: tuple[int, int, int, int] | None = None,
        show_inventory_counts: bool = False,
        style_name: str = "Section.TLabelframe",
    ) -> dict[str, ttk.Entry]:
        box = ttk.LabelFrame(parent, text=title, style=style_name, padding=10)
        if outer_padding is None:
            outer_padding = (0, 5, 5, 0) if column == 0 else (5, 0, 5, 0)
        box.grid(row=row, column=column, sticky="nsew", padx=(outer_padding[0], outer_padding[1]), pady=(outer_padding[2], outer_padding[3]))
        box.columnconfigure(1, weight=1)
        fields: dict = {}
        fields["box"] = box
        fields["mode"] = tk.StringVar(value="連続")
        mode_frame = ttk.Frame(box)
        mode_frame.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(mode_frame, text="薬の種類").pack(side="left", padx=(0, 6))
        for mode in ("連続", "間欠"):
            ttk.Radiobutton(
                mode_frame, text=mode, value=mode, variable=fields["mode"], style="Outlined.Toolbutton",
                command=lambda f=fields: self._toggle_period_mode(f),
            ).pack(side="left", padx=2)
        fields["mode_frame"] = mode_frame
        for row, (key, label) in enumerate((("hospital", "医療機関"), ("start", start_label), ("days", "日数"), ("end", "終了予定")), start=1):
            field_label = ttk.Label(box, text=label)
            field_label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
            if key == "end":
                fields["end_label"] = field_label
            if key == "start":
                fields["start_label"] = field_label
            if key == "days":
                fields["days_label"] = field_label
            entry = ttk.Entry(box)
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            fields[key] = entry
        fields["start_slot"] = tk.StringVar(value="朝")
        fields["end_slot"] = tk.StringVar(value="寝前")
        fields["active_slots"] = {slot: tk.BooleanVar(value=True) for slot in ("朝", "昼", "夕", "寝前")}
        fields["start_date_button"] = ttk.Button(
            box,
            text="日付",
            width=5,
            command=lambda f=fields: self._open_period_start_calendar(f),
        )
        fields["start_date_button"].grid(row=2, column=2, padx=(5, 0), pady=5)
        _start_slot_button = ttk.Button(
            box,
            textvariable=fields["start_slot"],
            width=5,
            command=lambda v=fields["start_slot"], f=fields: self.open_slot_picker(v, lambda: self._auto_end(f)),
        )
        fields["start_slot_button"] = _start_slot_button
        _start_slot_button.grid(row=2, column=3, padx=(3, 0), pady=5)
        fields["end_date_button"] = ttk.Button(
            box,
            text="日付",
            width=5,
            command=lambda e=fields["end"], v=fields["end_slot"], f=fields: self.open_calendar(e, v, lambda: self._end_slot_changed(f)),
        )
        fields["end_date_button"].grid(row=4, column=2, padx=(5, 0), pady=5)
        _end_slot_button = ttk.Button(
            box,
            textvariable=fields["end_slot"],
            width=5,
            command=lambda v=fields["end_slot"], f=fields: self.open_slot_picker(v, lambda: self._end_slot_changed(f)),
        )
        fields["end_slot_button"] = _end_slot_button
        _end_slot_button.grid(row=4, column=3, padx=(3, 0), pady=5)
        entry.bind("<FocusOut>", lambda _event, f=fields: self._auto_end(f))
        fields["start"].bind("<FocusOut>", lambda _event, f=fields: self._auto_end(f))
        fields["days"].bind("<KeyRelease>", lambda _event, f=fields: self._auto_end(f))
        fields["active_label"] = ttk.Label(box, text="薬局保持時点" if show_inventory_counts else "服用時点")
        fields["active_label"].grid(row=5, column=0, sticky="w", padx=(0, 8), pady=5)
        active_frame = ttk.Frame(box)
        active_frame.grid(row=5, column=1, columnspan=3, sticky="w", pady=5)
        fields["active_frame"] = active_frame
        for slot in ("朝", "昼", "夕", "寝前"):
            ttk.Checkbutton(
                active_frame,
                text=slot,
                variable=fields["active_slots"][slot],
                style="Outlined.Toolbutton",
                command=lambda f=fields: self._auto_end(f),
            ).pack(side="left", padx=(0, 3))
        intermittent = ttk.Frame(box)
        intermittent.grid(row=5, column=0, columnspan=4, sticky="ew", pady=4)
        intermittent.columnconfigure(1, weight=1)
        fields["intermittent_frame"] = intermittent
        ttk.Label(intermittent, text="薬品名").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        fields["drug"] = ttk.Entry(intermittent)
        fields["drug"].grid(row=0, column=1, columnspan=5, sticky="ew", pady=2)
        ttk.Label(intermittent, text="服用時点").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=2)
        fields["intermittent_slot"] = tk.StringVar(value="朝")
        ttk.Button(intermittent, textvariable=fields["intermittent_slot"], width=6,
                   command=lambda v=fields["intermittent_slot"]: self.open_slot_picker(v)).grid(row=1, column=1, sticky="w")
        ttk.Label(intermittent, text="規則").grid(row=1, column=2, padx=(10, 4))
        fields["intermittent_rule"] = ttk.Combobox(intermittent, values=("指定日数おき", "曜日指定", "月1回"), state="readonly", width=12)
        fields["intermittent_rule"].grid(row=1, column=3, sticky="w")
        fields["intermittent_rule"].set("指定日数おき")
        fields["intermittent_rule"].bind("<<ComboboxSelected>>", lambda _event, f=fields: self._auto_end(f))
        ttk.Label(intermittent, text="間隔").grid(row=1, column=4, padx=(8, 4))
        fields["interval_days"] = ttk.Entry(intermittent, width=5)
        fields["interval_days"].grid(row=1, column=5, sticky="w")
        fields["interval_days"].insert(0, "1")
        fields["interval_days"].bind("<KeyRelease>", lambda _event, f=fields: self._auto_end(f))
        weekday_frame = ttk.Frame(intermittent)
        weekday_frame.grid(row=2, column=1, columnspan=5, sticky="w", pady=(3, 0))
        fields["weekdays"] = {}
        for weekday_index, weekday in enumerate(WEEKDAYS):
            variable = tk.BooleanVar(value=False)
            fields["weekdays"][weekday_index] = variable
            ttk.Checkbutton(weekday_frame, text=weekday, variable=variable, style="Outlined.Toolbutton",
                            command=lambda f=fields: self._auto_end(f)).pack(side="left", padx=1)
        intermittent.grid_remove()
        next_row = 6
        if show_inventory_counts:
            fields["prescribed_slots"] = set(("朝", "昼", "夕", "寝前"))
            fields["prescribed_label"] = ttk.Label(box, text="処方上の用法")
            fields["prescribed_label"].grid(row=6, column=0, sticky="w", padx=(0, 8), pady=5)
            fields["prescribed_text"] = tk.StringVar(value="朝・昼・夕・寝前")
            fields["prescribed_value_label"] = ttk.Label(box, textvariable=fields["prescribed_text"], foreground="#536A7A")
            fields["prescribed_value_label"].grid(row=6, column=1, columnspan=3, sticky="w", pady=5)
            fields["count_label"] = ttk.Label(box, text="薬局保持包数")
            fields["count_label"].grid(row=7, column=0, sticky="w", padx=(0, 8), pady=5)
            count_frame = ttk.Frame(box)
            count_frame.grid(row=7, column=1, columnspan=3, sticky="w", pady=5)
            fields["count_frame"] = count_frame
            fields["count_vars"] = {}
            for slot in ("朝", "昼", "夕", "寝前"):
                value = tk.StringVar(value=f"{slot} -")
                fields["count_vars"][slot] = value
                ttk.Label(count_frame, textvariable=value, width=8).pack(side="left", padx=(0, 3))
            next_row = 8
        fields["appointment_label"] = ttk.Label(box, text="受診日")
        fields["appointment_label"].grid(row=next_row, column=0, sticky="w", padx=(0, 8), pady=5)
        fields["appointment"] = ttk.Entry(box)
        fields["appointment"].grid(row=next_row, column=1, sticky="ew", pady=5)
        fields["appointment_button"] = ttk.Button(
            box, text="日付", width=5, command=lambda e=fields["appointment"]: self.open_calendar(e)
        )
        fields["appointment_button"].grid(row=next_row, column=2, padx=(5, 0), pady=5)
        fields["next_row"] = next_row + 2
        fields["input_widgets"] = []
        def collect_input_widgets(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Combobox, ttk.Button, ttk.Radiobutton, ttk.Checkbutton)):
                    fields["input_widgets"].append(child)
                collect_input_widgets(child)
        collect_input_widgets(box)
        self._apply_display_options(fields)
        return fields

    def _open_period_start_calendar(self, fields: dict) -> None:
        slot = fields["intermittent_slot"] if fields["mode"].get() == "間欠" else fields["start_slot"]
        self.open_calendar(fields["start"], slot, lambda: self._auto_end(fields))

    def _toggle_period_mode(self, fields: dict) -> None:
        intermittent = fields["mode"].get() == "間欠"
        fields["days_label"].configure(text="包数" if intermittent else "日数")
        fields["end_label"].configure(text="最終服用日" if intermittent else "終了予定")
        if intermittent:
            fields["active_label"].grid_remove()
            fields["active_frame"].grid_remove()
            fields["start_slot_button"].grid_remove()
            fields["end_slot_button"].grid_remove()
            fields["intermittent_frame"].grid()
            if "prescribed_label" in fields:
                fields["prescribed_label"].grid_remove()
                fields["prescribed_value_label"].grid_remove()
                fields["count_label"].grid_remove()
                fields["count_frame"].grid_remove()
        else:
            fields["intermittent_frame"].grid_remove()
            fields["active_label"].grid()
            fields["active_frame"].grid()
            fields["start_slot_button"].grid()
            fields["end_slot_button"].grid()
            if "prescribed_label" in fields:
                fields["prescribed_label"].grid()
                fields["prescribed_value_label"].grid()
                fields["count_label"].grid()
                fields["count_frame"].grid()
        self._apply_display_options(fields)
        self._auto_end(fields)

    def _intermittent_item_from_fields(self, fields: dict, allow_waiting: bool = False) -> dict:
        hospital = fields["hospital"].get().strip()
        drug = fields["drug"].get().strip()
        first_date = fields["start"].get().strip()
        count_text = fields["days"].get().strip()
        if not hospital or not drug or not first_date:
            raise ValidationError("間欠薬の医療機関・薬品名・初回服用日を入力してください。")
        if not count_text and allow_waiting:
            count = 2
        else:
            try:
                count = int(count_text)
            except ValueError as exc:
                raise ValidationError("間欠薬の包数を整数で入力してください。") from exc
        try:
            interval_days = int(fields["interval_days"].get().strip() or "1")
        except ValueError as exc:
            raise ValidationError("間欠薬の服用間隔を整数で入力してください。") from exc
        item = {
            "hospital": hospital, "drug": drug, "slot": fields["intermittent_slot"].get(),
            "first_date": parse_date(first_date).isoformat(), "count": count,
            "rule": fields["intermittent_rule"].get(),
            "interval_days": interval_days,
            "weekdays": [index for index, variable in fields["weekdays"].items() if variable.get()],
            "appointment_date": fields["appointment"].get().strip(),
        }
        item_dates(item)
        if allow_waiting and not count_text:
            item["count"] = 0
        return item

    def open_calendar(self, entry: ttk.Entry, slot_variable: tk.StringVar | None = None, after_select=None) -> None:
        initial = date.today()
        try:
            if entry.get().strip():
                initial = parse_date(entry.get())
        except ValidationError:
            pass
        def selected(value: date) -> None:
            _set_entry(entry, value.isoformat())
            if slot_variable is not None:
                self.open_slot_picker(slot_variable, after_select)
            elif after_select is not None:
                after_select()

        DatePickerDialog(self, initial, selected)

    def open_slot_picker(self, variable: tk.StringVar, after_select=None) -> None:
        SlotPickerDialog(self, variable.get(), lambda value: self._set_slot(variable, value, after_select))

    @staticmethod
    def _set_slot(variable: tk.StringVar, value: str, after_select=None) -> None:
        variable.set(value)
        if after_select is not None:
            after_select()

    def _auto_end(self, fields: dict) -> None:
        if fields.get("mode") is not None and fields["mode"].get() == "間欠":
            try:
                item = self._intermittent_item_from_fields(fields)
                dates = item_dates(item)
                _set_entry(fields["end"], dates[-1].isoformat())
            except (ValidationError, ValueError):
                if not fields["days"].get().strip():
                    _set_entry(fields["end"], "")
            return
        try:
            start = parse_date(fields["start"].get())
            days = int(fields["days"].get().strip())
            if days <= 0:
                _set_entry(fields["end"], "")
                return
        except (ValidationError, ValueError):
            if not fields["days"].get().strip():
                _set_entry(fields["end"], "")
            return
        slots = ("朝", "昼", "夕", "寝前")
        active_slots = [slot for slot in slots if fields["active_slots"][slot].get()]
        start_slot = fields["start_slot"].get()
        if start_slot not in active_slots:
            fields["active_slots"][start_slot].set(True)
            active_slots = [slot for slot in slots if fields["active_slots"][slot].get()]
        if not active_slots:
            return
        counts = {slot: 0 for slot in active_slots}
        end_point = start.toordinal() * 4 + slots.index(start_slot)
        while True:
            slot = slots[end_point % 4]
            if slot in counts:
                counts[slot] += 1
            if all(count == days for count in counts.values()):
                break
            end_point += 1
        end_ordinal, end_slot_index = divmod(end_point, 4)
        _set_entry(fields["end"], date.fromordinal(end_ordinal).isoformat())
        fields["end_slot"].set(slots[end_slot_index])
        self._refresh_inventory_counts(fields)

    def _end_slot_changed(self, fields: dict) -> None:
        end_slot = fields["end_slot"].get()
        fields["active_slots"][end_slot].set(True)
        self._refresh_inventory_counts(fields)

    def _refresh_inventory_counts(self, fields: dict) -> None:
        if "count_vars" not in fields:
            return
        try:
            counts = period_slot_counts(self._period(fields))
        except ValidationError:
            counts = {slot: None for slot in ("朝", "昼", "夕", "寝前")}
        for slot, variable in fields["count_vars"].items():
            count = counts.get(slot)
            variable.set(f"{slot} {'-' if count is None else count}")

    def _apply_basic_visual(self, complete: bool) -> None:
        prefix = "BasicDone" if complete else "BasicCurrent"

        def paint(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, ttk.LabelFrame):
                    child.configure(style=f"BasicSub{'Done' if complete else 'Current'}.TLabelframe")
                elif isinstance(child, ttk.Frame):
                    child.configure(style=f"{prefix}.TFrame")
                elif isinstance(child, ttk.Checkbutton):
                    child.configure(style=f"{prefix}.TCheckbutton")
                elif isinstance(child, ttk.Label):
                    current_style = child.cget("style")
                    if current_style in ("PatientInfo.TLabel", "BasicCurrentPatient.TLabel", "BasicDonePatient.TLabel"):
                        child.configure(style=f"{prefix}Patient.TLabel")
                    else:
                        child.configure(style=f"{prefix}.TLabel")
                paint(child)

        paint(self.basic_section)

    def _paint_basic_lock(self, section: ttk.LabelFrame, locked: bool) -> None:
        if not locked:
            return
        section.configure(style="BasicSubLocked.TLabelframe")

        def paint(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, ttk.Frame):
                    child.configure(style="BasicLocked.TFrame")
                elif isinstance(child, ttk.Checkbutton):
                    child.configure(style="BasicLocked.TCheckbutton")
                elif isinstance(child, ttk.Label):
                    current_style = child.cget("style")
                    if "Patient" in current_style:
                        child.configure(style="BasicLockedPatient.TLabel")
                    else:
                        child.configure(style="BasicLocked.TLabel")
                paint(child)

        paint(section)

    @staticmethod
    def _set_widgets_enabled(widgets, enabled: bool) -> None:
        for widget in widgets:
            if enabled:
                widget.state(["!disabled"])
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state="readonly")
            else:
                widget.state(["disabled"])

    def _set_period_inputs_enabled(self, fields: dict, enabled: bool) -> None:
        self._set_widgets_enabled(fields.get("input_widgets", ()), enabled)
        if enabled:
            self._apply_display_options(fields)

    def _apply_stage_locks(self, inputs_locked: bool, patient_locked: bool) -> None:
        self._set_widgets_enabled(self.setting_widgets, not inputs_locked)
        if not inputs_locked:
            date_enabled = self.use_date_management.get()
            self.delivery_mode_widget.configure(state="readonly" if date_enabled else "disabled")
            self.unmatched_mode.configure(state="readonly" if date_enabled else "disabled")
        for button in (self.previous_record_button, self.legacy_odt_button):
            button.configure(state="disabled" if inputs_locked else "normal")
        self._set_widgets_enabled(
            (self.patient, self.patient_id, self.facility, self.service_date, self.service_date_button),
            not patient_locked,
        )
        for index, fields in enumerate(self.s_fields):
            self._set_period_inputs_enabled(fields, not inputs_locked)
            fields["delete_button"].configure(state="disabled" if inputs_locked else "normal")
            if "correction_button" in fields:
                fields["correction_button"].configure(state="disabled" if inputs_locked else "normal")
            linked = f"S{index + 1}" in self.o_links
            fields["transfer_button"].configure(
                state="disabled" if inputs_locked or linked else "normal",
            )
        self.add_s_button.configure(state="disabled" if inputs_locked else "normal")
        self.add_o_button.configure(state="disabled" if inputs_locked else "normal")
        self._paint_basic_lock(self.load_methods_section, inputs_locked)
        self._paint_basic_lock(self.settings_section, inputs_locked)
        self._paint_basic_lock(self.patient_info_section, patient_locked)

    def _apply_period_idle_visual(self, fields: dict) -> None:
        box = fields["box"]
        box.configure(style="PeriodIdle.TLabelframe")

        def paint(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, ttk.Combobox):
                    child.configure(style="PeriodIdle.TCombobox")
                elif isinstance(child, ttk.Entry):
                    child.configure(style="PeriodIdle.TEntry")
                elif isinstance(child, ttk.Label):
                    child.configure(style="PeriodIdle.TLabel")
                elif isinstance(child, ttk.Frame):
                    child.configure(style="PeriodIdle.TFrame")
                paint(child)

        paint(box)

    def _apply_pair_visual(self, fields: dict, index: int, received: bool = False, confirmed: bool = False) -> None:
        """カード本体だけでなく、入力欄・ラベルの背景も行色へ揃える。"""
        suffix = "Confirmed" if confirmed else ("Received" if received else "")
        prefix = f"Pair{index % len(self._pair_styles) + 1}{suffix}"
        box = fields["hospital"].master
        box.configure(style=f"{prefix}.TLabelframe")

        def paint(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, ttk.Combobox):
                    child.configure(style=f"{prefix}.TCombobox")
                elif isinstance(child, ttk.Entry):
                    child.configure(style=f"{prefix}.TEntry")
                elif isinstance(child, ttk.Label):
                    child.configure(style=f"{prefix}.TLabel")
                elif isinstance(child, ttk.Frame):
                    child.configure(style=f"{prefix}.TFrame")
                paint(child)

        paint(box)

    def add_s_box(self, values: dict | None = None, forecast: bool = False) -> dict[str, ttk.Entry]:
        index = len(self.s_fields)
        fields = self._period_box(
            self.s_container,
            0,
            f"S{index + 1}　処方薬預り",
            row=index,
            outer_padding=(0, 5, 0, 0),
            show_inventory_counts=True,
            style_name=self._pair_styles[index % len(self._pair_styles)],
        )
        self.s_fields.append(fields)
        self.s_forecast_flags.append(forecast)
        fields["forecast_received"] = False
        if values:
            _set_entry(fields["hospital"], str(values.get("hospital", "")))
            _set_entry(fields["start"], str(values.get("start", "")))
            _set_entry(fields["end"], str(values.get("end", "")))
            _set_entry(fields["days"], str(values.get("days", "")))
            fields["start_slot"].set(str(values.get("start_slot", "朝")))
            fields["end_slot"].set(str(values.get("end_slot", "寝前")))
            selected_slots = values.get("active_slots", ("朝", "昼", "夕", "寝前"))
            for slot, variable in fields["active_slots"].items():
                variable.set(slot in selected_slots)
            prescribed_slots = values.get("prescribed_slots", selected_slots)
            fields["prescribed_slots"] = set(prescribed_slots)
            fields["prescribed_text"].set("・".join(slot for slot in ("朝", "昼", "夕", "寝前") if slot in prescribed_slots))
            self._set_period_mode_values(fields, values)
            _set_entry(fields["appointment"], str(values.get("appointment_date", "")))
        self._refresh_inventory_counts(fields)
        fields["end_label"].configure(text="終了予定" if forecast else "終了日")
        box = fields["hospital"].master
        if forecast:
            box.configure(text=f"S{index + 1}　処方待ち（未入荷）")
        action_row = fields["next_row"]
        actions = ttk.Frame(box)
        actions.grid(row=action_row, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        fields["transfer_button"] = ttk.Button(
            actions, text="この枠を受付枠へ承継", style="Outlined.TButton", command=lambda f=fields: self.transfer_to_o(f)
        )
        fields["transfer_button"].pack(side="left")
        if not forecast:
            fields["correction_button"] = ttk.Button(actions, text="情報を修正", style="Outlined.TButton", command=lambda f=fields: self.open_inventory_correction(f))
            fields["correction_button"].pack(
                side="left", padx=(5, 0)
            )
        fields["delete_button"] = ttk.Button(actions, text="この枠を削除", style="Outlined.TButton", command=lambda f=fields: self.remove_s_box(f))
        fields["delete_button"].pack(side="right")
        self._apply_pair_visual(fields, index)
        self._watch_workflow_fields(fields)
        return fields

    def open_inventory_correction(self, fields: dict[str, ttk.Entry]) -> None:
        try:
            before = self._period(fields)
        except ValidationError as exc:
            messagebox.showwarning("預り薬の入力を確認してください", str(exc))
            return
        prescribed_slots = tuple(
            slot for slot in ("朝", "昼", "夕", "寝前")
            if slot in fields.get("prescribed_slots", set(before.active_slots))
        )
        InventoryCorrectionDialog(
            self,
            before,
            prescribed_slots,
            lambda after, reason, disposition, note, differences, new_prescribed_slots: self._apply_inventory_correction(
                fields, before, after, reason, disposition, note, differences, new_prescribed_slots
            ),
        )

    def _apply_inventory_correction(
        self,
        fields: dict[str, ttk.Entry],
        before: MedicationPeriod,
        after: MedicationPeriod | dict,
        reason: str,
        disposition: str,
        note: str,
        differences: dict[str, int] | None = None,
        prescribed_slots: tuple[str, ...] | None = None,
    ) -> None:
        prescribed_slots = prescribed_slots or tuple(fields.get("prescribed_slots", before.active_slots))
        if differences:
            self._add_inventory_difference_o(fields, before, differences)
            self.inventory_corrections.append({
                "before": _period_to_record(before), "after": _period_to_record(before),
                "reason": reason, "disposition": disposition, "note": note,
                "slot_differences": differences, "moved_to_o": True,
                "prescribed_slots": list(slot for slot in ("朝", "昼", "夕", "寝前") if slot in prescribed_slots),
            })
            self._workflow_inputs_changed()
            self.status.configure(text=f"{before.hospital}の保持包数差を受付へ移しました。預り期間は変更していません。")
            return
        after_record = _period_to_record(after) if isinstance(after, MedicationPeriod) else dict(after)
        _set_entry(fields["hospital"], str(after_record["hospital"]))
        _set_entry(fields["start"], str(after_record.get("start", "")))
        _set_entry(fields["end"], str(after_record.get("end", "")))
        _set_entry(fields["days"], str(after_record["days"]))
        fields["start_slot"].set(str(after_record.get("start_slot", "朝")))
        fields["end_slot"].set(str(after_record.get("end_slot", "寝前")))
        for slot, variable in fields["active_slots"].items():
            variable.set(slot in after_record.get("active_slots", ()))
        fields["prescribed_slots"] = set(prescribed_slots)
        fields["prescribed_text"].set("・".join(slot for slot in ("朝", "昼", "夕", "寝前") if slot in prescribed_slots))
        after_record["prescribed_slots"] = list(slot for slot in ("朝", "昼", "夕", "寝前") if slot in prescribed_slots)
        self._refresh_inventory_counts(fields)
        fields["inactive"] = int(after_record["days"]) == 0
        if fields["inactive"]:
            fields["hospital"].master.configure(text=f"S{self.s_fields.index(fields) + 1}　処理済み（残数0）")
        self.inventory_corrections.append(
            {
                "before": _period_to_record(before),
                "after": after_record,
                "reason": reason,
                "disposition": disposition,
                "note": note,
                "prescribed_slots": list(slot for slot in ("朝", "昼", "夕", "寝前") if slot in prescribed_slots),
            }
        )
        self._workflow_inputs_changed()
        self.status.configure(text=f"{after_record['hospital']}の薬局預かり薬を修正しました。報告作成時に修正履歴を記載します。")

    def _add_inventory_difference_o(self, source_fields: dict, before: MedicationPeriod, differences: dict[str, int]) -> None:
        target = self.add_o_box(
            values=_period_to_record(before),
            link=f"S{self.s_fields.index(source_fields) + 1}",
            role="在庫差分",
        )
        target["inventory_differences"] = dict(differences)
        summary = "　".join(f"{slot}{value:+d}包" for slot, value in differences.items() if value)
        self.o_role_labels[-1].configure(text=f"処理：在庫差分（{summary}）", foreground="#A33A2B")
        target_index = len(self.o_fields) - 1
        if target_index > 0:
            for items in (self.o_fields, self.o_links, self.o_roles, self.o_link_labels, self.o_role_labels):
                items.insert(0, items.pop(target_index))
            self._reflow_boxes(self.o_fields, "O")

    def add_o_box(self, values: dict | None = None, link: str = "", role: str = "合包対象") -> dict[str, ttk.Entry]:
        index = len(self.o_fields)
        fields = self._period_box(
            self.o_container,
            1,
            _o_card_title(index + 1, link, role),
            row=index,
            outer_padding=(5, 0, 0, 0),
            style_name=self._pair_styles[index % len(self._pair_styles)],
        )
        self.o_fields.append(fields)
        self.o_links.append(link)
        self.o_roles.append(role)
        if values:
            for key in ("hospital", "start", "days", "end"):
                _set_entry(fields[key], str(values.get(key, "")))
            fields["start_slot"].set(str(values.get("start_slot", "朝")))
            fields["end_slot"].set(str(values.get("end_slot", "寝前")))
            selected_slots = values.get("active_slots", ("朝", "昼", "夕", "寝前"))
            for slot, variable in fields["active_slots"].items():
                variable.set(slot in selected_slots)
            self._set_period_mode_values(fields, values)
            _set_entry(fields["appointment"], str(values.get("appointment_date", "")))
        box = fields["hospital"].master
        detail_row = fields["next_row"]
        link_label = ttk.Label(box, text=f"状態：{_display_link(link)}より承継" if link else "状態：新規受付", foreground="#536A7A")
        link_label.grid(row=detail_row, column=0, columnspan=2, sticky="w", pady=(5, 0))
        link_label.grid_remove()
        self.o_link_labels.append(link_label)
        role_label = ttk.Label(box, text=f"処理：{_o_role_display(role, bool(link))}", foreground="#8A5A00" if role == "在庫積み増し" else "#276746")
        role_label.grid(row=detail_row + 1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        role_label.grid_remove()
        self.o_role_labels.append(role_label)
        actions = ttk.Frame(box)
        actions.grid(row=detail_row + 2, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        fields["confirmed"] = False
        fields["role_button"] = ttk.Button(
            actions, text="対応を変更", style="Outlined.TButton",
            command=lambda f=fields: self.toggle_o_role(f),
        )
        fields["role_button"].pack(side="left")
        fields["confirm_button"] = ttk.Button(
            actions, text="この受付内容を確定", style="Outlined.TButton",
            command=lambda f=fields: self.toggle_o_confirmation(f),
        )
        fields["confirm_button"].pack(side="left", padx=(5, 0))
        ttk.Button(actions, text="この枠を削除", style="Outlined.TButton", command=lambda f=fields: self.remove_o_box(f)).pack(side="right")
        fields["received_from_forecast"] = False
        self._apply_pair_visual(fields, index, bool(values and _fields_have_input(fields)))
        self._watch_workflow_fields(fields)
        self._reflow_boxes(self.o_fields, "O")
        return fields

    def _set_period_mode_values(self, fields: dict, values: dict) -> None:
        mode = str(values.get("mode", "間欠" if values.get("drug") else "連続"))
        fields["mode"].set(mode)
        if mode == "間欠":
            _set_entry(fields["drug"], str(values.get("drug", "")))
            fields["intermittent_slot"].set(str(values.get("slot", "朝")))
            fields["intermittent_rule"].set(str(values.get("rule", "指定日数おき")))
            _set_entry(fields["interval_days"], str(values.get("interval_days", 1)))
            for index, variable in fields["weekdays"].items():
                variable.set(index in set(int(value) for value in values.get("weekdays", ())))
            if values.get("first_date"):
                _set_entry(fields["start"], str(values["first_date"]))
            if values.get("count") is not None:
                _set_entry(fields["days"], "" if int(values.get("count", 0)) == 0 else str(values["count"]))
        self._toggle_period_mode(fields)

    def _clear_s_boxes(self) -> None:
        for fields in self.s_fields:
            fields["hospital"].master.destroy()
        self.s_fields.clear()
        self.s_forecast_flags.clear()

    def _clear_o_boxes(self) -> None:
        for fields in self.o_fields:
            fields["hospital"].master.destroy()
        self.o_fields.clear()
        self.o_links.clear()
        self.o_roles.clear()
        self.o_link_labels.clear()
        self.o_role_labels.clear()

    def remove_s_box(self, fields: dict[str, ttk.Entry]) -> None:
        index = self.s_fields.index(fields)
        removed_number = index + 1
        fields["hospital"].master.destroy()
        self.s_fields.pop(index)
        self.s_forecast_flags.pop(index)
        for o_index, link in enumerate(self.o_links):
            if not link.startswith("S") or not link[1:].isdigit():
                continue
            linked_number = int(link[1:])
            if linked_number == removed_number:
                self.o_links[o_index] = "引継ぎ元削除済み"
            elif linked_number > removed_number:
                self.o_links[o_index] = f"S{linked_number - 1}"
            self.o_link_labels[o_index].configure(text=f"状態：{_display_link(self.o_links[o_index])}より承継")
        if not self.s_fields:
            self.add_s_box()
        self._reflow_s_boxes()
        self._reflow_boxes(self.o_fields, "O")
        self._workflow_inputs_changed()

    def remove_o_box(self, fields: dict[str, ttk.Entry]) -> None:
        index = self.o_fields.index(fields)
        removed_link = self.o_links[index]
        fields["hospital"].master.destroy()
        self.o_fields.pop(index)
        self.o_links.pop(index)
        self.o_roles.pop(index)
        self.o_link_labels.pop(index)
        self.o_role_labels.pop(index)
        if removed_link.startswith("S") and removed_link[1:].isdigit() and removed_link not in self.o_links:
            source_index = int(removed_link[1:]) - 1
            if 0 <= source_index < len(self.s_fields):
                source = self.s_fields[source_index]
                source["forecast_received"] = False
                source["transfer_button"].configure(text="この枠を受付枠へ承継", state="normal")
                self._reflow_s_boxes()
        if not self.o_fields:
            self.add_o_box()
        self._reflow_boxes(self.o_fields, "O")
        self._workflow_inputs_changed()

    def _set_o_editable(self, fields: dict, editable: bool) -> None:
        self._set_period_inputs_enabled(fields, editable)
        fields["role_button"].configure(state="normal" if editable else "disabled")

    def toggle_o_confirmation(self, fields: dict) -> None:
        index = self.o_fields.index(fields)
        if fields.get("confirmed"):
            fields["confirmed"] = False
            self._set_o_editable(fields, True)
            fields["confirm_button"].configure(text="この受付内容を確定")
            self._workflow_inputs_changed()
            self._reflow_boxes(self.o_fields, "O")
            self.status.configure(text=f"R{index + 1}を修正できます。検算以降を解除しました。")
            return
        if not self._workflow_fields_complete(fields):
            messagebox.showwarning("受付内容を確認してください", "医療機関、開始、日数、終了予定を入力してください。")
            return
        try:
            if self.use_date_management.get():
                if fields["mode"].get() == "間欠":
                    self._intermittent_item_from_fields(fields)
                else:
                    self._period(fields).validate()
            else:
                days = int(fields["days"].get().strip())
                if days <= 0:
                    raise ValueError
        except (ValidationError, ValueError):
            messagebox.showwarning("受付内容を確認してください", "日付、日数、服用時点を確認してください。")
            return
        if self.o_roles[index] == "臨時追加" and not fields.get("temporary_addition"):
            messagebox.showwarning("受付内容を確認してください", "臨時追加の内容を「対応を変更」から確定してください。")
            return
        fields["confirmed"] = True
        self._set_o_editable(fields, False)
        fields["confirm_button"].configure(text="修正する")
        self._reflow_boxes(self.o_fields, "O")
        self._update_workflow_colors()
        self.status.configure(text=f"R{index + 1}の受付内容を確定しました。")

    def toggle_o_role(self, fields: dict[str, ttk.Entry]) -> None:
        index = self.o_fields.index(fields)
        if self.o_roles[index] == "在庫差分":
            messagebox.showinfo("在庫差分", "この枠は保持包数の差分記録です。通常処方へ変更する場合は削除して入力し直してください。")
            return
        OProcessingDialog(self, self.o_roles[index], lambda role: self._set_o_role(fields, role))

    def _set_o_role(self, fields: dict[str, ttk.Entry], role: str) -> None:
        index = self.o_fields.index(fields)
        if role == "臨時追加":
            try:
                source = self._period(fields)
                source.validate()
            except ValidationError as exc:
                messagebox.showwarning("今回処方を確認してください", str(exc))
                return
            TemporaryAdditionDialog(self, source, lambda details: self._apply_temporary_addition(fields, details))
            return
        fields.pop("temporary_addition", None)
        fields["confirmed"] = False
        self._set_o_editable(fields, True)
        fields["confirm_button"].configure(text="この受付内容を確定")
        self.o_roles[index] = role
        self.o_role_labels[index].configure(
            text=f"処理：{_o_role_display(role, bool(self.o_links[index]))}",
            foreground="#8A5A00" if role == "在庫積み増し" else "#276746",
        )
        self._reflow_boxes(self.o_fields, "O")
        fields["box"].configure(text=_o_card_title(index + 1, self.o_links[index], role))
        self._workflow_inputs_changed()
        self.status.configure(text=f"受付{index + 1}を「{_role_display(role)}」へ変更しました。")

    def _apply_temporary_addition(self, fields: dict[str, ttk.Entry], details: dict) -> None:
        index = self.o_fields.index(fields)
        fields["confirmed"] = False
        self._set_o_editable(fields, True)
        fields["confirm_button"].configure(text="この受付内容を確定")
        fields["temporary_addition"] = details
        self.o_roles[index] = "臨時追加"
        applied = details["applied"]
        carry = "残りは次回へ" if details["carryover"] else "引継ぎなし"
        self.o_role_labels[index].configure(
            text=f"処理：臨時追加　{applied['start']}{applied['start_slot']}～{applied['end']}{applied['end_slot']}（{carry}）",
            foreground="#8A5A00",
        )
        self._reflow_boxes(self.o_fields, "O")
        fields["box"].configure(text=_o_card_title(index + 1, self.o_links[index], "臨時追加"))
        self._workflow_inputs_changed()
        self.status.configure(text=f"受付{index + 1}を臨時追加として設定しました。通常の合包期間には含めません。")

    def _reflow_boxes(self, items: list[dict[str, ttk.Entry]], prefix: str) -> None:
        occupied_rows: set[int] = set()
        o_numbers: list[int] = []
        if prefix == "O":
            reserved = {
                int(link[1:]) for link in self.o_links
                if link.startswith("S") and link[1:].isdigit()
            }
            used = set(reserved)
            next_free = 1
            for link in self.o_links:
                if link.startswith("S") and link[1:].isdigit():
                    o_numbers.append(int(link[1:]))
                    continue
                while next_free in used:
                    next_free += 1
                o_numbers.append(next_free)
                used.add(next_free)
        for index, fields in enumerate(items):
            box = fields["hospital"].master
            row = index
            if prefix == "O":
                link = self.o_links[index] if index < len(self.o_links) else ""
                display_number = o_numbers[index]
                row = display_number - 1
                while row in occupied_rows:
                    row += 1
                occupied_rows.add(row)
                title = _o_card_title(display_number, link, self.o_roles[index])
                if fields.get("confirmed"):
                    title += "　✅"
                if link.startswith("S"):
                    self.o_link_labels[index].configure(text=f"状態：{_display_link(link)}より承継")
                elif link:
                    self.o_link_labels[index].configure(text=f"状態：{_display_link(link)}")
                else:
                    self.o_link_labels[index].configure(text="状態：新規受付")
            else:
                title = f"S{index + 1}　処方薬預り"
            box.configure(text=title)
            self._apply_pair_visual(
                fields, row, _fields_have_input(fields), bool(fields.get("confirmed")),
            )
            box.grid_configure(row=row, column=0 if prefix == "S" else 1)

    def _reflow_s_boxes(self) -> None:
        for index, (fields, forecast) in enumerate(zip(self.s_fields, self.s_forecast_flags)):
            box = fields["hospital"].master
            if fields.get("inactive"):
                title = f"S{index + 1}　処理済み（残数0）"
            elif forecast and fields.get("forecast_received"):
                title = f"S{index + 1}　今回受付済み"
            else:
                title = f"S{index + 1}　処方待ち（未入荷）" if forecast else f"S{index + 1}　処方薬預り"
            box.configure(text=title)
            self._apply_pair_visual(fields, index)
            box.grid_configure(row=index, column=0)

    def transfer_to_o(self, fields: dict[str, ttk.Entry]) -> None:
        s_index = self.s_fields.index(fields)
        from_forecast = self.s_forecast_flags[s_index]
        # 処方待ち枠は消さず「今回受付済み」として残す。
        # これにより、受付カードを必ず元の保管枠の右に置ける。
        link = f"S{s_index + 1}"
        role = "合包対象" if from_forecast else "在庫積み増し"
        if fields["mode"].get() == "間欠":
            try:
                item = self._intermittent_item_from_fields(fields, allow_waiting=self.s_forecast_flags[s_index])
                dates = item_dates(item) if item.get("count") else [parse_date(item["first_date"])]
            except (ValidationError, ValueError) as exc:
                messagebox.showwarning("間欠薬を確認してください", str(exc))
                return
            start = dates[0].isoformat() if self.s_forecast_flags[s_index] else next_dose_dates(item, 1)[0].isoformat()
            start_slot = item["slot"]
        elif self.s_forecast_flags[s_index]:
            start = fields["start"].get().strip()
            start_slot = fields["start_slot"].get()
        else:
            try:
                end = parse_date(fields["end"].get())
                end_slot_index = ("朝", "昼", "夕", "寝前").index(fields["end_slot"].get())
            except (ValidationError, ValueError) as exc:
                messagebox.showwarning("終了日を確認してください", str(exc))
                return
            next_point = end.toordinal() * 4 + end_slot_index + 1
            active_slots = {
                slot for slot in ("朝", "昼", "夕", "寝前") if fields["active_slots"][slot].get()
            }
            if not active_slots:
                messagebox.showwarning("服用時点を確認してください", "服用時点を1つ以上選択してください。")
                return
            while ("朝", "昼", "夕", "寝前")[next_point % 4] not in active_slots:
                next_point += 1
            start_ordinal, start_slot_index = divmod(next_point, 4)
            start = date.fromordinal(start_ordinal).isoformat()
            start_slot = ("朝", "昼", "夕", "寝前")[start_slot_index]
        target_index = next(
            (index for index, item in enumerate(self.o_fields) if not _fields_have_input(item)),
            None,
        )
        if target_index is None:
            target = self.add_o_box(link=link, role=role)
            target_index = len(self.o_fields) - 1
        else:
            target = self.o_fields[target_index]
            self.o_links[target_index] = link
            self.o_roles[target_index] = role
            self.o_link_labels[target_index].configure(text=f"状態：{_display_link(link)}より承継")
            self.o_role_labels[target_index].configure(
                text=f"処理：{_o_role_display(role, True)}",
                foreground="#8A5A00" if role == "在庫積み増し" else "#276746",
            )
        _set_entry(target["hospital"], fields["hospital"].get().strip())
        _set_entry(target["start"], start)
        _set_entry(target["days"], "")
        _set_entry(target["end"], "")
        _set_entry(target["appointment"], fields["appointment"].get().strip())
        target["start_slot"].set(start_slot)
        target["end_slot"].set("寝前")
        for slot, variable in target["active_slots"].items():
            variable.set(fields["active_slots"][slot].get())
        if fields["mode"].get() == "間欠":
            self._set_period_mode_values(target, {
                "mode": "間欠", "drug": fields["drug"].get().strip(), "slot": fields["intermittent_slot"].get(),
                "rule": fields["intermittent_rule"].get(), "interval_days": fields["interval_days"].get().strip() or 1,
                "weekdays": [index for index, variable in fields["weekdays"].items() if variable.get()],
                "first_date": start, "count": 0,
            })
        target["received_from_forecast"] = from_forecast
        fields["transfer_button"].configure(text="受付へ承継済み", state="disabled")
        if from_forecast:
            self.o_link_labels[target_index].configure(text=f"状態：{_display_link(link)}より承継（前回予想）")
            fields["forecast_received"] = True
            fields["transfer_button"].configure(text="受付へ承継済み", state="disabled")
            self._reflow_s_boxes()
        self._reflow_boxes(self.o_fields, "O")
        self.assessment = None
        self.result = None
        self.inventory_record = None
        self.inventory_report = None
        self.assessment_text.configure(
            text=(
                f"「{_role_display(role)}」で計算します。\n"
                "異なる場合は、今回の薬を確認してください。"
            ),
            style="TLabel",
        )
        self.status.configure(text=f"受付へ引継ぎました：{_role_display(role)}。日数を入力すると終了予定を自動計算します。")

    def _period(self, fields: dict) -> MedicationPeriod:
        if not self.use_slot_detail.get() and fields["mode"].get() == "連続":
            return MedicationPeriod(
                hospital=fields["hospital"].get().strip(),
                start=parse_date(fields["start"].get()),
                end=parse_date(fields["end"].get()),
                start_slot="朝", end_slot="寝前",
                active_slots=("朝", "昼", "夕", "寝前"),
            )
        return MedicationPeriod(
            hospital=fields["hospital"].get().strip(),
            start=parse_date(fields["start"].get()),
            end=parse_date(fields["end"].get()),
            start_slot=fields["start_slot"].get(),
            end_slot=fields["end_slot"].get(),
            active_slots=tuple(slot for slot in ("朝", "昼", "夕", "寝前") if fields["active_slots"][slot].get()),
        )

    def _period_without_date(self, fields: dict) -> MedicationPeriod:
        """日付管理なしの受付薬を、計算専用の同一起点へそろえる。"""
        hospital = fields["hospital"].get().strip()
        if not hospital:
            raise ValidationError("医療機関名を入力してください。")
        try:
            days = int(fields["days"].get().strip())
        except ValueError as exc:
            raise ValidationError(f"{hospital}の日数は整数で入力してください。") from exc
        if days < 1 or days > 366:
            raise ValidationError(f"{hospital}の日数は1～366日で入力してください。")
        slots = tuple(
            slot for slot in ("朝", "昼", "夕", "寝前")
            if not self.use_slot_detail.get() or fields["active_slots"][slot].get()
        )
        if not slots:
            raise ValidationError(f"{hospital}の服用時点を1つ以上選択してください。")
        # この日付は包数計算だけに使い、画面・帳票では日付管理なしと表示する。
        base = date(2000, 1, 1)
        return MedicationPeriod(hospital, base, base + timedelta(days=days - 1), slots[0], slots[-1], slots)

    def _calculate_without_date_management(self) -> PackagingAssessment:
        active = [
            (fields, self.o_roles[index])
            for index, fields in enumerate(self.o_fields)
            if _fields_have_input(fields)
        ]
        if any(fields["mode"].get() == "間欠" for fields, _role in active):
            raise ValidationError("間欠薬は服用日の規則を使うため、「日付管理を行う」をONにしてください。")
        unresolved = [index + 1 for index, (_fields, role) in enumerate(active) if role == "在庫差分"]
        if unresolved:
            raise ValidationError("在庫差分が未処理です。受付の「対応を変更」から確認してください。")
        incoming = tuple(self._period_without_date(fields) for fields, role in active if role == "合包対象")
        if not incoming:
            raise ValidationError("今回払い出す受付薬を1件以上入力してください。")
        first = incoming[0]
        virtual = MedicationPeriod(
            INTERMITTENT_BOUNDARY_HOSPITAL, first.start, first.end,
            first.start_slot, first.end_slot, first.active_slots,
        )
        return calculate_assessment(virtual, first, (), incoming[1:])

    def _intermittent_boundary(self, fields_list: list[dict], label: str) -> MedicationPeriod | None:
        items = []
        for fields in fields_list:
            if fields["mode"].get() != "間欠" or not fields["hospital"].get().strip():
                continue
            items.append(self._intermittent_item_from_fields(fields))
        return self._intermittent_items_boundary(items, label)

    @staticmethod
    def _intermittent_items_boundary(items: list[dict], label: str) -> MedicationPeriod | None:
        slots = ("朝", "昼", "夕", "寝前")
        ranges = []
        for item in items:
            dates = item_dates(item)
            # 間欠薬は指定された服用時点だけを交付するが、通常薬のお渡し境界は
            # 最初の服用日の朝から、最後の服用日の寝前までとする。
            ranges.append((dates[0].toordinal() * 4, dates[-1].toordinal() * 4 + 3))
        if not ranges:
            return None
        start_point = max(value[0] for value in ranges)
        end_point = min(value[1] for value in ranges)
        if end_point < start_point:
            raise ValidationError(f"{label}の間欠薬どうしの服用期間が重なりません。")
        start_ordinal, start_slot_index = divmod(start_point, 4)
        end_ordinal, end_slot_index = divmod(end_point, 4)
        return MedicationPeriod(
            hospital=INTERMITTENT_BOUNDARY_HOSPITAL,
            start=date.fromordinal(start_ordinal), end=date.fromordinal(end_ordinal),
            start_slot=slots[start_slot_index], end_slot=slots[end_slot_index], active_slots=slots,
        )

    def _intermittent_o_boundary(self) -> MedicationPeriod | None:
        if not self.show_intermittent.get():
            return None
        held_keys = {
            (fields["hospital"].get().strip(), fields["drug"].get().strip())
            for fields, forecast in zip(self.s_fields, self.s_forecast_flags)
            if not forecast and fields["mode"].get() == "間欠" and fields["hospital"].get().strip()
        }
        has_continuous_s = any(
            not forecast and not fields.get("inactive") and fields["mode"].get() == "連続"
            and _fields_have_input(fields)
            for fields, forecast in zip(self.s_fields, self.s_forecast_flags)
        )
        items = []
        used_extension_keys = set()
        for fields in self.o_fields:
            if fields["mode"].get() != "間欠" or not fields["hospital"].get().strip():
                continue
            key = (fields["hospital"].get().strip(), fields["drug"].get().strip())
            if key in held_keys:
                if not has_continuous_s or key in used_extension_keys:
                    continue
                merged = next(
                    (item for item in self.intermittent_items
                     if (item.get("hospital"), item.get("drug")) == key and item.get("extension")),
                    None,
                )
                if merged is not None:
                    items.append(merged)
                    used_extension_keys.add(key)
            else:
                items.append(self._intermittent_item_from_fields(fields))
        return self._intermittent_items_boundary(items, "今回受付した")

    def _intermittent_s_boundary(self) -> MedicationPeriod | None:
        if not self.show_intermittent.get():
            return None
        has_continuous_s = any(
            not forecast and not fields.get("inactive") and fields["mode"].get() == "連続"
            and _fields_have_input(fields)
            for fields, forecast in zip(self.s_fields, self.s_forecast_flags)
        )
        incoming_keys = {
            (fields["hospital"].get().strip(), fields["drug"].get().strip())
            for fields in self.o_fields
            if fields["mode"].get() == "間欠" and fields["hospital"].get().strip()
        }
        fields_list = [
            fields
            for fields, forecast in zip(self.s_fields, self.s_forecast_flags)
            if not forecast and not fields.get("inactive")
            and not (
                has_continuous_s and fields["mode"].get() == "間欠"
                and (fields["hospital"].get().strip(), fields["drug"].get().strip()) in incoming_keys
            )
        ]
        return self._intermittent_boundary(fields_list, "現在預かっている")

    def _has_common_packaging_point(self) -> bool:
        """実施候補内に、2種類以上を同じ服用時点で圧着できる箇所があるか。"""
        if self.assessment is None:
            return False
        slots = ("朝", "昼", "夕", "寝前")
        low = self.assessment.proposed_start.toordinal() * 4 + slots.index(self.assessment.proposed_start_slot)
        high = self.assessment.proposed_end.toordinal() * 4 + slots.index(self.assessment.proposed_end_slot)
        intermittent_points = []
        for item in self.intermittent_items if self.show_intermittent.get() else ():
            slot_index = slots.index(item["slot"])
            intermittent_points.extend(value.toordinal() * 4 + slot_index for value in item_dates(item))
        for point in range(low, high + 1):
            ordinal, slot_index = divmod(point, 4)
            slot = slots[slot_index]
            participants = sum(
                source.start.toordinal() * 4 + slots.index(source.start_slot) <= point
                <= source.end.toordinal() * 4 + slots.index(source.end_slot)
                and slot in source.active_slots
                for source in self.assessment.sources
            )
            participants += intermittent_points.count(point)
            if participants >= 2:
                return True
        return False

    def _review_stale_s(self) -> bool:
        """実施日より前に終了したSを、確認なしでAへ流さない。"""
        service_date = parse_date(self.service_date.get().strip())
        for index, (fields, forecast) in enumerate(zip(self.s_fields, self.s_forecast_flags)):
            if forecast or fields.get("inactive") or fields.get("stale_reviewed"):
                continue
            if not fields["hospital"].get().strip():
                continue
            if fields["mode"].get() == "間欠":
                item = self._intermittent_item_from_fields(fields)
                last_date = item_dates(item)[-1]
                description = f"S{index + 1} {item['hospital']} {item['drug']}\n最終服用日：{_md(last_date)}"
            else:
                period = self._period(fields)
                last_date = period.end
                description = f"S{index + 1} {_period_display(period)}"
            if last_date >= service_date:
                continue
            confirmed = messagebox.askyesno(
                "過去分の残薬を確認してください",
                description
                + f"\n\n実施日 {_md(service_date)} より前に終了しています。"
                "\n現物を薬局で保管していることを確認済みなら「はい」を選択してください。"
                "\n交付済み・合包し忘れ・入力誤りの場合は「いいえ」を選び、Sの「情報を修正」で記録してください。",
                parent=self,
            )
            if not confirmed:
                self.status.configure(text=f"S{index + 1}の過去分を確認し、情報を修正してください。")
                return False
            fields["stale_reviewed"] = True
            self.anomaly_reviews.append({
                "s_index": index + 1, "hospital": fields["hospital"].get().strip(),
                "mode": fields["mode"].get(), "last_date": last_date.isoformat(),
                "result": "現物を薬局で保管中と確認",
            })
        return True

    def _audit_proposal(self, tentative: PackagingResult) -> tuple[str, ...]:
        issues = list(audit_result_balance(tentative))
        low = tentative.actual_start
        high = tentative.actual_end
        seen_courses = set()
        for item in self.intermittent_items:
            dates = item_dates(item)
            if len(dates) != len(set(dates)):
                issues.append(f"{item['hospital']} {item['drug']}：服用日が重複しています。")
            course = (
                item.get("hospital"), item.get("drug"), item.get("slot"),
                item.get("rule"), tuple(item.get("weekdays", ())), item.get("interval_days", 1),
            )
            if course in seen_courses:
                issues.append(f"{item['hospital']} {item['drug']}：同じ間欠薬が二重に登録されています。")
            seen_courses.add(course)
            earlier = [value for value in dates if value < low]
            if earlier:
                issues.append(
                    f"{item['hospital']} {item['drug']}：今回お渡し期間より前の服用日が"
                    f"{len(earlier)}包残ります（{format_dates_compact(earlier)}）。"
                )
            extension = item.get("extension")
            if extension and (
                int(extension.get("before_count", 0)) + int(extension.get("incoming_count", 0))
                != int(extension.get("after_count", -1))
            ):
                issues.append(f"{item['hospital']} {item['drug']}：日数延長の包数が一致しません。")
        return tuple(dict.fromkeys(issues))

    def calculate_assessment(self) -> bool:
        unconfirmed = [
            index + 1 for index, fields in enumerate(self.o_fields)
            if self._workflow_fields_complete(fields) and not fields.get("confirmed")
        ]
        if unconfirmed:
            messagebox.showwarning(
                "受付内容を確定してください",
                "次の受付枠を確定してください：" + "、".join(f"R{index}" for index in unconfirmed),
            )
            return False
        try:
            if not self.use_date_management.get():
                self.intermittent_items = []
                self.assessment = self._calculate_without_date_management()
                held = ()
                incoming = self.assessment.incoming_sources
                additions = ()
            else:
                self._sync_intermittent_from_period_boxes()
                if not self._review_stale_s():
                    return False
            active_s = [
                fields
                for fields, forecast in zip(self.s_fields, self.s_forecast_flags)
                if not forecast and not fields.get("inactive") and fields["mode"].get() == "連続" and _fields_have_input(fields)
            ]
            intermittent_s_boundary = self._intermittent_s_boundary() if self.use_date_management.get() else None
            if not self.use_date_management.get():
                active_s = []
            else:
                pass
            if self.use_date_management.get() and not active_s:
                active_o_exists = any(_fields_have_input(fields) for fields in self.o_fields)
                reception_only_targets = sum(
                    fields["mode"].get() == "連続"
                    and self.o_roles[index] == "合包対象"
                    and _fields_have_input(fields)
                    for index, fields in enumerate(self.o_fields)
                )
                reception_only_targets += sum(
                    fields["mode"].get() == "間欠"
                    and self.o_roles[index] == "合包対象"
                    and _fields_have_input(fields)
                    for index, fields in enumerate(self.o_fields)
                )
                if self.inventory_corrections and not active_o_exists:
                    self.inventory_record = {
                        "workflow": "SOAP",
                        "operation": "inventory_correction",
                        "patient": self.patient.get().strip(),
                        "facility": self.facility.get().strip(),
                        "service_date": self.service_date.get().strip(),
                        "result": {"uncombined": []},
                    }
                    self.inventory_report = (
                        f"{self.patient.get().strip()}　【外来服薬支援】　実施日 {self.service_date.get().strip()}\n"
                        f"施設：{self.facility.get().strip()}\n"
                    )
                    self.assessment = None
                    self.result = None
                    self.assessment_text.configure(text="修正内容の検算：一致\n薬局預かり薬を0日に修正します。", style="Result.TLabel")
                    self.assessment_actions.grid_remove()
                    self.actual_editor.grid_remove()
                    self.result_instruction.configure(text="在庫リセットの内容を実施報告にまとめます。")
                    self.inventory_report_button.configure(text="在庫修正の報告を作成")
                    self.inventory_report_button.grid()
                    return True
                if intermittent_s_boundary is None and reception_only_targets >= 2:
                    # 保管量が0でも、受付に合包対象薬が2件以上あれば、
                    # 交付モードにかかわらず受付薬どうしで検算する。
                    held = ()
                elif intermittent_s_boundary is None:
                    raise ValidationError(
                        "検算対象となる薬を2件以上入力してください。"
                        "保管薬がない場合は、受付に合包対象薬（連続薬・間欠薬）が2件以上必要です。"
                    )
                else:
                    held = (intermittent_s_boundary,)
            elif self.use_date_management.get():
                held = tuple(self._period(fields) for fields in active_s)
                # 間欠薬は連続薬の交付開始日を遅らせない。連続薬がない側でのみ、
                # 合包相手が存在することを示す仮想境界として使用する。
            active_o = [
                (fields, self.o_roles[index])
                for index, fields in enumerate(self.o_fields)
                if fields["mode"].get() == "連続" and _fields_have_input(fields)
            ]
            unresolved = [
                index + 1 for index, (_fields, role) in enumerate(active_o) if role == "在庫差分"
            ]
            if self.use_date_management.get() and unresolved:
                raise ValidationError(
                    "在庫差分が未処理です。Oの在庫差分を先に確認してください。"
                    "差分を反映後、この枠を削除してから計算します。"
                )
            temporary = [
                fields.get("temporary_addition")
                for fields, role in active_o
                if role == "臨時追加"
            ]
            if any(item is None for item in temporary):
                raise ValidationError("臨時追加の詳細が未確定です。受付の「対応を変更」から入力してください。")
            temporary = [item for item in temporary if item is not None]
            if self.use_date_management.get():
                incoming = tuple(self._period(fields) for fields, role in active_o if role == "合包対象")
                additions = tuple(self._period(fields) for fields, role in active_o if role == "在庫積み増し")
            intermittent_o_boundary = self._intermittent_o_boundary() if self.use_date_management.get() else None
            if not incoming and intermittent_o_boundary is not None:
                incoming = (*incoming, intermittent_o_boundary)
            if self.use_date_management.get() and not incoming:
                if not additions:
                    intermittent_extensions = [
                        item for item in self.intermittent_items if item.get("extension")
                    ]
                    if intermittent_extensions:
                        self.inventory_record = {
                            "workflow": "SOAP",
                            "operation": "intermittent_extension",
                            "patient": self.patient.get().strip(),
                            "facility": self.facility.get().strip(),
                            "service_date": self.service_date.get().strip(),
                            "result": {"uncombined": [_period_to_record(period) for period in held]},
                        }
                        self.inventory_report = (
                            f"{self.patient.get().strip()}　【外来服薬支援】　実施日 {self.service_date.get().strip()}\n"
                            f"施設：{self.facility.get().strip()}\n"
                            "\n★間欠服用薬の日数延長\n"
                        )
                        for item in intermittent_extensions:
                            extension = item["extension"]
                            self.inventory_report += (
                                f"{item['hospital']}　{item['drug']}　{item['slot']}（{rule_label(item)}）\n"
                                f"受付前 {extension['before_count']}包／今回受付 {extension['incoming_count']}包／"
                                f"受付後 {extension['after_count']}包\n"
                                f"服用日：{format_dates_compact(item_dates(item))}\n"
                            )
                        self.assessment = None
                        self.result = None
                        self.assessment_text.configure(text="包数の検算：一致\n間欠服用薬の日数を延長します。", style="Result.TLabel")
                        self.assessment_actions.grid_remove()
                        self.actual_editor.grid_remove()
                        self.result_instruction.configure(text="受付前・今回受付・受付後を実施報告にまとめます。")
                        self.inventory_report_button.configure(text="日数延長の報告を作成")
                        self.inventory_report_button.grid()
                        return True
                    if temporary:
                        remainders = self._temporary_remainders(temporary)
                        self.inventory_record = {
                            "workflow": "SOAP",
                            "operation": "temporary_addition",
                            "patient": self.patient.get().strip(),
                            "facility": self.facility.get().strip(),
                            "service_date": self.service_date.get().strip(),
                            "temporary_additions": temporary,
                            "result": {"uncombined": [
                                _period_to_record(period) for period in (*held, *remainders)
                            ]},
                        }
                        self.inventory_report = (
                            f"{self.patient.get().strip()}　【外来服薬支援】　実施日 {self.service_date.get().strip()}\n"
                            f"施設：{self.facility.get().strip()}\n"
                            + self._temporary_report(temporary)
                        )
                        self.assessment = None
                        self.result = None
                        self.assessment_text.configure(text="追加内容の検算：一致\n臨時追加として処理します。", style="Result.TLabel")
                        self.assessment_actions.grid_remove()
                        self.actual_editor.grid_remove()
                        self.result_instruction.configure(text="臨時追加の実施内容を報告にまとめます。")
                        self.inventory_report_button.configure(text="臨時追加の報告を作成")
                        self.inventory_report_button.grid()
                        return True
                    if self.inventory_corrections:
                        self.inventory_record = {
                            "workflow": "SOAP",
                            "operation": "inventory_correction",
                            "patient": self.patient.get().strip(),
                            "facility": self.facility.get().strip(),
                            "service_date": self.service_date.get().strip(),
                            "result": {"uncombined": [_period_to_record(period) for period in held]},
                        }
                        self.inventory_report = (
                            f"{self.patient.get().strip()}　【外来服薬支援】　実施日 {self.service_date.get().strip()}\n"
                            f"施設：{self.facility.get().strip()}\n"
                        )
                        self.assessment = None
                        self.result = None
                        self.assessment_text.configure(text="修正内容の検算：一致\n薬局預かり薬の修正を報告します。", style="Result.TLabel")
                        self.assessment_actions.grid_remove()
                        self.actual_editor.grid_remove()
                        self.result_instruction.configure(text="修正前・修正後・理由・処理方法を実施報告にまとめます。")
                        self.inventory_report_button.configure(text="在庫修正の報告を作成")
                        self.inventory_report_button.grid()
                        self.status.configure(text="薬局預かり薬の修正として確認しました。")
                        return True
                    raise ValidationError("今回受け付けた薬を1件以上入力してください。")
                self.inventory_record, self.inventory_report = build_inventory_update(
                    held,
                    additions,
                    self.patient.get(),
                    self.facility.get(),
                    self.service_date.get().strip(),
                )
                self.assessment = None
                self.result = None
                self.assessment_text.configure(
                    text="日数の検算：一致\n受付前＋今回受付＝受付後",
                    style="Result.TLabel",
                )
                _set_entry(self.actual_start, "")
                _set_entry(self.actual_end, "")
                self.assessment_actions.grid_remove()
                self.actual_editor.grid_remove()
                self.result_instruction.configure(text="受付前・今回受付・受付後を実施報告にまとめます。")
                self.inventory_report_button.configure(text="日数延長の報告を作成")
                self.inventory_report_button.grid()
                self.preview.delete("1.0", "end")
                self.status.configure(text="日数延長として確認しました。")
                return True
            if not self.use_date_management.get():
                pass
            elif held:
                self.assessment = calculate_assessment(
                    held[0], incoming[0], held[1:], incoming[1:], additions
                )
            else:
                if additions:
                    raise ValidationError("積み増し先のSがありません。Oの処理を確認してください。")
                first = incoming[0]
                virtual_held = MedicationPeriod(
                    INTERMITTENT_BOUNDARY_HOSPITAL,
                    first.start,
                    first.end,
                    first.start_slot,
                    first.end_slot,
                    first.active_slots,
                )
                self.assessment = calculate_assessment(
                    virtual_held, first, (), incoming[1:]
                )
        except ValidationError as exc:
            messagebox.showwarning("入力を確認してください", str(exc))
            return False
        value = self.assessment
        self.inventory_record = None
        self.inventory_report = None
        tentative = confirm_packaging(
            value,
            value.proposed_start,
            value.proposed_end,
            value.proposed_start_slot,
            value.proposed_end_slot,
            release_all=self._release_all_selected(),
            release_unmatched_slots=self._release_unmatched_selected(),
        )
        self.assessment_issues = self._audit_proposal(tentative)
        if self.assessment_issues:
            detail = "\n".join(f"・{issue}" for issue in self.assessment_issues[:5])
            if len(self.assessment_issues) > 5:
                detail += f"\n・ほか{len(self.assessment_issues) - 5}件"
            self.assessment_text.configure(text=f"重複の検算：要確認\n{detail}", style="TLabel", justify="left")
            self.assessment_actions.grid_remove()
            self.actual_editor.grid_remove()
            self.result_instruction.configure(text="預り・受付または情報修正を確認し、再度検算してください。")
            self.status.configure(text="帳尻が合わない項目、または取り残された薬があります。Pへは進めません。")
            return False
        common_packaging = self._has_common_packaging_point()
        if not self.use_date_management.get():
            shared_slots = tuple(
                slot for slot in ("朝", "昼", "夕", "寝前")
                if sum(slot in source.active_slots for source in value.incoming_sources) >= 2
            )
            slot_text = "・".join(shared_slots) if shared_slots else "共通なし（単独で交付）"
            self.assessment_text.configure(
                text=f"服用時点の検算：一致\n共通時点：{slot_text}／今回受付分をすべて払い出し",
                style="Result.TLabel", justify="center",
            )
            _set_entry(self.actual_start, value.proposed_start.isoformat())
            _set_entry(self.actual_end, value.proposed_end.isoformat())
            self.actual_start_slot.set(value.proposed_start_slot)
            self.actual_end_slot.set(value.proposed_end_slot)
            self.inventory_report_button.grid_remove()
            self.actual_editor.grid_remove()
            self.change_period_button.configure(state="disabled")
            self.result_instruction.configure(text="服用時点を確認し、「提案どおり実施」を押してください。")
            self.assessment_actions.grid()
            self.result = None
            self.preview.delete("1.0", "end")
            self.status.configure(text="日付を使わず、服用時点と日数を検算しました。")
            return True
        self.change_period_button.configure(state="normal")
        prefix = "" if common_packaging else "同時交付（共通服用時点なし）　"
        if self._release_all_selected():
            prefix = "すべて払い出し／合包可能部分　" + prefix
        self.assessment_text.configure(
            text=(
                "重複の検算：一致\n"
                f"{prefix}{_md(value.proposed_start)}{value.proposed_start_slot} ～ "
                f"{_md(value.proposed_end)}{value.proposed_end_slot}　{value.proposed_days}日分"
            ),
            style="Result.TLabel",
            justify="center",
        )
        _set_entry(self.actual_start, value.proposed_start.isoformat())
        _set_entry(self.actual_end, value.proposed_end.isoformat())
        self.actual_start_slot.set(value.proposed_start_slot)
        self.actual_end_slot.set(value.proposed_end_slot)
        self.inventory_report_button.grid_remove()
        self.actual_editor.grid_remove()
        self.result_instruction.configure(text=(
            "共通する服用時点はありません。単独薬を同時に交付する期間を選んでください。"
            if not common_packaging else
            "検算結果の通り実施するか、下の3つから選んでください。"
        ))
        self.assessment_actions.grid()
        self.result = None
        self.preview.delete("1.0", "end")
        self.status.configure(text="合包可能期間を提案しました。")
        return True

    def use_proposal(self) -> None:
        """Confirm A as the actual result without making the user re-enter P."""
        if self.assessment is None and not self.calculate_assessment():
            return
        if self.inventory_record is not None:
            self.calculate_result()
            return
        assert self.assessment is not None
        value = self.assessment
        _set_entry(self.actual_start, value.proposed_start.isoformat())
        _set_entry(self.actual_end, value.proposed_end.isoformat())
        self.actual_start_slot.set(value.proposed_start_slot)
        self.actual_end_slot.set(value.proposed_end_slot)
        self.actual_editor.grid_remove()
        self.calculate_result()

    def return_to_assessment(self) -> None:
        """⑤の判断を取り消し、S・Rを残したまま④の検算へ戻す。"""
        self.assessment = None
        self.result = None
        self.inventory_record = None
        self.inventory_report = None
        self.record_saved = False
        self.assessment_issues = ()
        for fields in self.o_fields:
            if fields.get("confirmed"):
                fields["confirmed"] = False
                self._set_o_editable(fields, True)
                fields["confirm_button"].configure(text="この受付内容を確定")
        self._reflow_boxes(self.o_fields, "O")
        _set_entry(self.actual_start, "")
        _set_entry(self.actual_end, "")
        self.assessment_actions.grid_remove()
        self.actual_editor.grid_remove()
        self.inventory_report_button.grid_remove()
        self.preview.delete("1.0", "end")
        self.assessment_text.configure(text="判断を差戻しました。\n④で合包案をもう一度計算してください。", style="TLabel")
        self.result_instruction.configure(text="④の検算結果を確認してください。")
        self.result_summary.configure(text="⑤で判断を確定すると、交付する期間をここへ表示します。")
        self.status.configure(text="判断を差戻しました。保管・受付の入力は残しています。")
        self._update_workflow_colors()

    def show_actual_editor(self) -> None:
        """Reveal P inputs only when the actual packaging interval differs from A."""
        if self.assessment is None and not self.calculate_assessment():
            return
        if self.inventory_record is not None:
            return
        self.result_instruction.configure(text="実際にお渡しする開始・終了へ変更してください。")
        self.actual_editor.grid()
        self._update_workflow_colors()

    def calculate_result(self) -> bool:
        if self.assessment is None and self.inventory_record is None and not self.calculate_assessment():
            return False
        self.record_saved = False
        if self.inventory_record is not None:
            assert self.inventory_report is not None
            self.preview.delete("1.0", "end")
            operation = self.inventory_record.get("operation")
            intermittent_detail = (
                "" if operation == "intermittent_extension"
                else build_intermittent_report(self.intermittent_items)
            )
            self.preview.insert(
                "1.0", self.inventory_report + self._correction_report()
                + self._anomaly_report() + intermittent_detail,
            )
            if operation == "inventory_correction":
                self.status.configure(text="薬局預かり薬の修正報告を作成しました。")
            elif operation == "temporary_addition":
                self.status.configure(text="臨時追加の実施報告を作成しました。")
            else:
                self.status.configure(text="日数を延長しました。実施報告を作成しました。")
            self.result_instruction.configure(text="判断を確定しました。\n⑥の結果を確認してください。")
            self.result_summary.configure(text="実施報告を作成しました。\nプレビューで内容を確認してください。")
            self._update_workflow_colors()
            return True
        try:
            assert self.assessment is not None
            self.result = confirm_packaging(
                self.assessment,
                parse_date(self.actual_start.get()),
                parse_date(self.actual_end.get()),
                self.actual_start_slot.get(),
                self.actual_end_slot.get(),
                release_all=self._release_all_selected(),
                release_unmatched_slots=self._release_unmatched_selected(),
            )
            self.assessment_issues = self._audit_proposal(self.result)
            if self.assessment_issues:
                detail = "\n".join(f"・{issue}" for issue in self.assessment_issues[:5])
                raise ValidationError(
                    "実施期間変更後の検算で、帳尻が合わない項目があります。\n"
                    f"{detail}\nS・Oまたは実施期間を確認してください。"
                )
            if self.use_date_management.get():
                report = build_period_report(
                    self.result,
                    self.patient.get(),
                    self.facility.get(),
                    self.service_date.get().strip(),
                )
            else:
                patient = self.patient.get().strip()
                if not patient:
                    raise ValidationError("対象者を入力してください。")
                lines = [f"{patient}　【外来服薬支援】　実施日 {self.service_date.get().strip()}"]
                if self.facility.get().strip():
                    lines.append(f"施設：{self.facility.get().strip()}")
                lines.extend(["", "★今回の処方薬（日付管理なし）"])
                for index, source in enumerate(self.result.assessment.incoming_sources):
                    label = chr(ord("A") + index)
                    lines.append(f"{label}. {source.hospital}　{source.days}日分（{_slot_summary(source.active_slots)}）")
                lines.extend(["", "★今回お渡し分", "受付薬を服用時点に合わせて、すべて払い出し", "", "★薬局預かり分", "なし"])
                report = "\n".join(lines)
        except ValidationError as exc:
            messagebox.showwarning("入力を確認してください", str(exc))
            return False
        self.preview.delete("1.0", "end")
        report = self._integrate_intermittent_report(report)
        self.preview.insert(
            "1.0", report + self._temporary_report(self._current_temporary_additions())
            + self._correction_report() + self._anomaly_report(),
        )
        self.result_instruction.configure(text="判断を確定しました。\n⑥の結果を確認してください。")
        self.result_summary.configure(
            text=(
                "服用時点を合わせ、\n今回受付分をすべて払い出します。"
                if not self.use_date_management.get() else
                "合包してお渡しする期間は、\n"
                f"{_md(self.result.actual_start)}{self.result.actual_start_slot}～"
                f"{_md(self.result.actual_end)}{self.result.actual_end_slot}となります。"
            ),
            style="Result.TLabel",
        )
        self.status.configure(text="今回お渡し分と次回への引継ぎを計算しました。")
        self._update_workflow_colors()
        return True

    def _current_temporary_additions(self) -> list[dict]:
        return [
            fields["temporary_addition"]
            for fields, role in zip(self.o_fields, self.o_roles)
            if role == "臨時追加" and fields.get("temporary_addition")
        ]

    def _temporary_remainders(self, items: list[dict]) -> tuple[MedicationPeriod, ...]:
        result = []
        for item in items:
            if not item.get("carryover"):
                continue
            source = _record_to_period(item["source"])
            applied = item["applied"]
            result.extend(remaining_outside_interval(
                source,
                parse_date(applied["start"]),
                parse_date(applied["end"]),
                applied["start_slot"],
                applied["end_slot"],
            ))
        return tuple(result)

    @staticmethod
    def _temporary_report(items: list[dict]) -> str:
        if not items:
            return ""
        lines = ["", "★臨時追加実施"]
        for item in items:
            applied = item["applied"]
            slots = _slot_summary(applied.get("active_slots", ()))
            lines.extend((
                f"■ {applied['hospital']}",
                f"追加先：{item.get('target_period', '') or '既交付薬'}",
                f"追加期間：{_record_period_text(applied)}",
                f"追加内容：{slots}",
                f"処理方法：{item['method']}",
                f"次回への引継ぎ：{'あり' if item['carryover'] else 'なし'}",
            ))
            if item.get("note"):
                lines.append(f"備考：{item['note']}")
        return "\n".join(lines) + "\n"

    def _correction_report(self) -> str:
        if not self.inventory_corrections:
            return ""
        lines = ["", "★処方変更・残薬確認に伴う薬局預かり薬の修正"]
        for item in self.inventory_corrections:
            before = item["before"]
            after = item["after"]
            if item.get("moved_to_o"):
                differences = item.get("slot_differences", {})
                difference_text = "・".join(
                    f"{slot}{amount:+d}包" for slot, amount in differences.items() if amount
                )
                lines.extend(
                    (
                        f"■ {before['hospital']}",
                        f"保持包数差：{difference_text}",
                        "対応：期間の連続性を保つため、差分をOへ移しました。",
                        f"修正理由：{item['reason']}",
                        f"処理方法：{item['disposition']}",
                    )
                )
                if item.get("note"):
                    lines.append(f"備考：{item['note']}")
                continue
            delta = int(after["days"]) - int(before["days"])
            delta_text = f"＋{delta}日分" if delta > 0 else f"{delta}日分" if delta < 0 else "日数変更なし"
            lines.extend(
                (
                    f"■ {before['hospital']}",
                    f"修正前：{_record_period_text(before)}",
                    f"修正後：{_record_period_text(after)}",
                    f"変更量：{delta_text}",
                    f"修正理由：{item['reason']}",
                    f"処理方法：{item['disposition']}",
                )
            )
            prescribed_slots = item.get("prescribed_slots")
            if prescribed_slots and prescribed_slots != after.get("active_slots"):
                lines.append(f"処方上の用法：{'・'.join(prescribed_slots)}（変更なし）")
                lines.append(f"薬局保持時点：{'・'.join(after.get('active_slots', [])) or 'なし'}")
            if item.get("note"):
                lines.append(f"備考：{item['note']}")
        return "\n".join(lines) + "\n"

    def _anomaly_report(self) -> str:
        if not self.anomaly_reviews:
            return ""
        lines = ["", "★過去分として残っている薬の確認"]
        for item in self.anomaly_reviews:
            end = parse_date(item["last_date"])
            lines.append(f"{item['hospital']}　最終日 {_md(end)}　{item['result']}")
        return "\n".join(lines) + "\n"

    def save(self) -> None:
        if self.result is None and self.inventory_record is None and not self.calculate_result():
            return
        if self.inventory_record is not None:
            data = dict(self.inventory_record)
            data["pharmacy"] = self.pharmacy.get().strip()
            if self.odt_path is not None:
                data["odt_path"] = str(self.odt_path)
            if self.inventory_corrections:
                data["inventory_corrections"] = list(self.inventory_corrections)
            if self.anomaly_reviews:
                data["anomaly_reviews"] = list(self.anomaly_reviews)
            if self.intermittent_items:
                data["intermittent_items"] = list(self.intermittent_items)
            data["input_options"] = self._input_options_record()
            data["appointments"] = self._appointment_records()
            forecasts = []
            for fields, forecast in zip(self.s_fields, self.s_forecast_flags):
                if forecast and not fields.get("forecast_received") and fields["hospital"].get().strip() and fields["start"].get().strip():
                    forecasts.append(
                        {
                            "hospital": fields["hospital"].get().strip(),
                            "start": fields["start"].get().strip(),
                            "start_slot": fields["start_slot"].get(),
                            "appointment_date": fields["appointment"].get().strip(),
                        }
                    )
            if forecasts:
                data["next_creations"] = forecasts
                data["next_creation"] = forecasts[0]
            path = self._save_json_record(data)
            if path is None:
                return
            labels = {
                "inventory_correction": "在庫修正",
                "temporary_addition": "臨時追加",
                "intermittent_extension": "間欠服用薬の日数延長",
            }
            label = labels.get(data.get("operation"), "日数延長")
            self.status.configure(text=f"{label}の記録を保存しました：{path.name}")
            self.record_saved = True
            self._update_workflow_colors()
            return
        assert self.result is not None
        data = period_result_to_dict(
            self.result,
            self.patient.get(),
            self.facility.get(),
            self.service_date.get().strip(),
        )
        data["reconciliation"] = {"status": "一致", "issues": []}
        self._retain_waiting_cards(data)
        if not self.use_date_management.get():
            data["next_creations"] = []
            data["post_delivery_states"] = []
        prescribed_by_hospital = {}
        for fields, forecast in zip(self.s_fields, self.s_forecast_flags):
            if forecast or not fields["hospital"].get().strip():
                continue
            hospital = fields["hospital"].get().strip()
            prescribed_by_hospital.setdefault(hospital, set()).update(
                fields.get("prescribed_slots", {
                    slot for slot, variable in fields["active_slots"].items() if variable.get()
                })
            )
        for candidate in data.get("result", {}).get("uncombined", []):
            slots = prescribed_by_hospital.get(candidate.get("hospital", ""))
            if slots:
                candidate["prescribed_slots"] = [slot for slot in ("朝", "昼", "夕", "寝前") if slot in slots]
        data["o_links"] = [
            self.o_links[index]
            for index, fields in enumerate(self.o_fields)
            if _fields_have_input(fields)
        ]
        data["o_roles"] = [
            self.o_roles[index]
            for index, fields in enumerate(self.o_fields)
            if _fields_have_input(fields)
        ]
        if self.inventory_corrections:
            data["inventory_corrections"] = list(self.inventory_corrections)
        if self.anomaly_reviews:
            data["anomaly_reviews"] = list(self.anomaly_reviews)
        if self.intermittent_items:
            data["intermittent_items"] = list(self.intermittent_items)
            if self.show_intermittent.get():
                delivered_intermittent, remaining_intermittent = self._split_intermittent()
                data["intermittent_delivered"] = [
                    self._intermittent_record(item, dates) for item, dates in delivered_intermittent
                ]
                data["intermittent_remaining"] = [
                    self._intermittent_record(item, dates) for item, dates in remaining_intermittent
                ]
            data["intermittent_forecasts"] = [
                {
                    "hospital": item["hospital"], "drug": item["drug"], "slot": item["slot"],
                    "rule": item["rule"], "interval_days": item.get("interval_days", 1),
                    "weekdays": list(item.get("weekdays", ())),
                    "appointment_date": item.get("appointment_date", ""),
                    "dose_dates": [value.isoformat() for value in next_dose_dates(item, 2)],
                }
                for item in self.intermittent_items
            ]
        data["pharmacy"] = self.pharmacy.get().strip()
        data["input_options"] = self._input_options_record()
        data["appointments"] = self._appointment_records()
        temporary = self._current_temporary_additions()
        if temporary:
            data["temporary_additions"] = temporary
            data["result"]["uncombined"].extend(
                _period_to_record(period) for period in self._temporary_remainders(temporary)
            )
        if self.odt_path is not None:
            data["odt_path"] = str(self.odt_path)
        path = self._save_json_record(data)
        if path is None:
            return
        self.status.configure(text=f"保存しました：{path.name}")
        self.record_saved = True
        self._update_workflow_colors()

    def _save_json_record(self, data: dict) -> Path | None:
        try:
            data["patient_id"] = self.patient_id.get().strip()
            return save_record(data)
        except OSError as exc:
            messagebox.showerror("記録を保存できません", str(exc))
            self.record_saved = False
            self._update_workflow_colors()
            return None

    def load_previous_s(self, selected_path: str | Path | None = None) -> None:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = str(selected_path) if selected_path else filedialog.askdirectory(
            title="対象者のフォルダを選択",
            initialdir=str(DEFAULT_DATA_DIR),
            mustexist=True,
        )
        if not path:
            return
        selected = Path(path)
        if selected.is_dir():
            latest = selected / "latest.json"
            if latest.is_file():
                path = str(latest)
            else:
                messagebox.showerror(
                    "記録を読み込めません",
                    "選択した患者フォルダに latest.json がありません。旧票は「旧ODT読込」を使用してください。",
                )
                return
        self.record_saved = False
        source = Path(path)
        if not source.is_file() or source.suffix.lower() not in (".json", ".odt"):
            messagebox.showerror("記録を読み込めません", "JSONまたはODTの記録ファイルを指定してください。")
            return
        if Path(path).suffix.lower() == ".odt":
            self._load_previous_odt(path, migrate=True)
            return
        try:
            data = load_record(path)
            if data.get("workflow") != "SOAP":
                raise ValidationError("S→O→A→P形式の記録ではありません。")
            candidates = data.get("result", {}).get("uncombined", [])
            saved_forecasts = next_creation_candidates(data)
            options = data.get("input_options", {})
            if (not candidates and not data.get("intermittent_items") and not saved_forecasts
                    and bool(options.get("date_management", True))):
                messagebox.showinfo("外来服薬支援", "この記録には次回へ引き継ぐ未実施薬がありません。")
                return
            _set_entry(self.patient, str(data.get("patient", "")))
            _set_entry(self.patient_id, str(data.get("patient_id", "")))
            _set_entry(self.facility, str(data.get("facility", "")))
            loaded_pharmacy = str(data.get("pharmacy", "")).strip()
            if loaded_pharmacy:
                _set_entry(self.pharmacy, loaded_pharmacy)
                self._pharmacy_changed()
            self.delivery_mode.set(_normalized_delivery_mode(str(data.get("result", {}).get("delivery_mode", DELIVERY_STORE))))
            self.show_intermittent.set(bool(options.get("intermittent", True)))
            self.use_slot_detail.set(bool(options.get("slot_detail", True)))
            self.use_appointment_dates.set(bool(options.get("appointment_dates", True)))
            self.use_date_management.set(bool(options.get("date_management", True)))
            self.unmatched_slot_mode.set(_normalized_unmatched_mode(str(options.get("unmatched_slot_mode", UNMATCHED_RELEASE))))
            appointment_by_hospital = {}
            for item in data.get("appointments", []):
                hospital = str(item.get("hospital", ""))
                appointment = str(item.get("appointment_date", ""))
                if hospital and appointment:
                    appointment_by_hospital.setdefault(hospital, appointment)
            saved_odt_path = str(data.get("odt_path", "")).strip()
            self.odt_path = Path(saved_odt_path) if saved_odt_path else None
            self._clear_s_boxes()
            for candidate in candidates:
                value = dict(candidate)
                value.setdefault("appointment_date", appointment_by_hospital.get(str(value.get("hospital", "")), ""))
                self.add_s_box(value)
            intermittent_to_carry = data.get("intermittent_remaining")
            if intermittent_to_carry is None:
                intermittent_to_carry = data.get("intermittent_items", [])
            elif not intermittent_to_carry and not bool(options.get("intermittent", True)):
                intermittent_to_carry = data.get("intermittent_items", [])
            for item in intermittent_to_carry:
                value = dict(item)
                value["mode"] = "間欠"
                value.setdefault("appointment_date", appointment_by_hospital.get(str(value.get("hospital", "")), ""))
                self.add_s_box(value)
            forecasts = saved_forecasts
            if forecasts:
                for forecast in forecasts:
                    self.add_s_box(
                        {
                            "hospital": forecast.get("hospital", ""),
                            "start": forecast.get("start", ""),
                            "start_slot": forecast.get("start_slot", "寝前"),
                            "active_slots": forecast.get("active_slots", ("朝", "昼", "夕", "寝前")),
                            "end": "",
                            "appointment_date": forecast.get("appointment_date", appointment_by_hospital.get(str(forecast.get("hospital", "")), "")),
                        },
                        forecast=True,
                    )
                forecast_text = "　／　".join(
                    f"{item.get('hospital', '')}　{item.get('start', '')}{item.get('start_slot', '寝前')}から（未入荷）"
                    for item in forecasts
                )
                self.previous_forecast.configure(text=f"前回からの作成予想：{forecast_text}", foreground="#8A5A00")
            else:
                self.previous_forecast.configure(text="前回からの作成予想：なし", foreground="#536A7A")
            remaining_intermittent_keys = {_intermittent_identity(item) for item in intermittent_to_carry}
            for item in data.get("intermittent_forecasts", []):
                if _intermittent_identity(item) in remaining_intermittent_keys:
                    continue
                value = dict(item)
                dates = item_dates(value)
                value.update({"mode": "間欠", "first_date": dates[0].isoformat(), "count": 0})
                self.add_s_box(value, forecast=True)
            # Oは今回実際に持参された薬なので、前回の予想値を自動入力しない。
            self._clear_o_boxes()
            self.add_o_box()
            self._display_options_changed()
            _set_entry(self.actual_start, "")
            _set_entry(self.actual_end, "")
            self.assessment = None
            self.result = None
            self.inventory_record = None
            self.inventory_report = None
            self.inventory_corrections.clear()
            self.anomaly_reviews.clear()
            self.intermittent_forecasts = list(data.get("intermittent_forecasts", []))
            self.intermittent_items = [dict(item) for item in intermittent_to_carry]
            self._update_patient_folder_label()
            self.assessment_text.configure(
                text="前回未実施薬をSへ読み込みました。今回持参・処方された薬を受付へ入力してください。",
                style="TLabel",
            )
            self.preview.delete("1.0", "end")
            self.status.configure(text=f"未実施薬{len(candidates)}件をSへ読み込みました。Oを入力してください：{Path(path).name}")
            self.after_idle(self._update_workflow_colors)
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            messagebox.showerror("記録を読み込めません", str(exc))

    def load_legacy_odt(self, selected_path: str | Path | None = None) -> None:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = str(selected_path) if selected_path else filedialog.askopenfilename(
            title="引き継ぐ旧ODTを選択",
            initialdir=str(DEFAULT_DATA_DIR),
            filetypes=(("LibreOffice票", "*.odt"),),
        )
        if path:
            self.record_saved = False
            self._load_previous_odt(path, migrate=True)

    def _load_previous_odt(self, path: str, migrate: bool = False) -> None:
        try:
            imported = import_previous_odt(path)
            source_odt = Path(path).resolve()
            if imported.metadata:
                self.delivery_mode.set(_normalized_delivery_mode(imported.metadata.get("DeliveryMode", self.delivery_mode.get())))
                self.unmatched_slot_mode.set(_normalized_unmatched_mode(imported.metadata.get("UnmatchedSlotMode", self.unmatched_slot_mode.get())))
                self.show_intermittent.set(imported.metadata.get("Intermittent", "1") == "1")
                self.use_slot_detail.set(imported.metadata.get("SlotDetail", "1") == "1")
                self.use_appointment_dates.set(imported.metadata.get("AppointmentDates", "1") == "1")
                self.use_date_management.set(imported.metadata.get("DateManagement", "1") == "1")
            def metadata_list(name: str) -> list[dict]:
                try:
                    value = json.loads(imported.metadata.get(name, "[]"))
                except (TypeError, ValueError):
                    return []
                return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
            if migrate:
                patient_name = imported.patient.strip() or source_odt.stem.split("_外来服薬支援", 1)[0]
                patient_id = imported.metadata.get("PatientId", "").strip()
                target_dir = patient_directory(patient_name, DEFAULT_DATA_DIR, patient_id)
                target_dir.mkdir(parents=True, exist_ok=True)
                migrated_odt = target_dir / "外来服薬支援.odt"
                if source_odt != migrated_odt.resolve():
                    shutil.copy2(source_odt, migrated_odt)
                migration_record = {
                    "workflow": "SOAP",
                    "patient": patient_name,
                    "patient_id": patient_id,
                    "facility": "",
                    "service_date": "",
                    "pharmacy": "",
                    "result": {
                        "delivery_mode": _normalized_delivery_mode(
                            imported.metadata.get("DeliveryMode", self.delivery_mode.get())
                        ),
                        "uncombined": [dict(item) for item in imported.held],
                    },
                    "next_creations": [dict(item) for item in imported.forecasts],
                    "appointments": metadata_list("Appointments"),
                    "intermittent_remaining": metadata_list("IntermittentRemaining"),
                    "intermittent_forecasts": metadata_list("IntermittentForecasts"),
                    "input_options": {
                        "intermittent": imported.metadata.get("Intermittent", "1") == "1",
                        "slot_detail": imported.metadata.get("SlotDetail", "1") == "1",
                        "appointment_dates": imported.metadata.get("AppointmentDates", "1") == "1",
                        "date_management": imported.metadata.get("DateManagement", "1") == "1",
                        "unmatched_slot_mode": _normalized_unmatched_mode(
                            imported.metadata.get("UnmatchedSlotMode", self.unmatched_slot_mode.get())
                        ),
                    },
                    "odt_path": str(migrated_odt.resolve()),
                    "migration": {"source_odt": str(source_odt), "migrated_at": datetime.now().isoformat()},
                }
                save_record(migration_record)
                self.odt_path = migrated_odt.resolve()
            else:
                self.odt_path = source_odt
            appointment_by_hospital = {
                str(item.get("hospital", "")): str(item.get("appointment_date", ""))
                for item in metadata_list("Appointments") if item.get("hospital")
            }
            self._clear_s_boxes()
            if imported.patient:
                _set_entry(self.patient, imported.patient)
            _set_entry(self.patient_id, imported.metadata.get("PatientId", ""))
            for item in imported.held:
                value = dict(item)
                value.setdefault("appointment_date", appointment_by_hospital.get(str(value.get("hospital", "")), ""))
                self.add_s_box(value)
            for item in imported.forecasts:
                value = dict(item)
                value.setdefault("appointment_date", appointment_by_hospital.get(str(value.get("hospital", "")), ""))
                self.add_s_box(value, forecast=True)
            intermittent_to_carry = metadata_list("IntermittentRemaining")
            intermittent_forecasts = metadata_list("IntermittentForecasts")
            for item in intermittent_to_carry:
                value = dict(item, mode="間欠")
                value.setdefault("appointment_date", appointment_by_hospital.get(str(value.get("hospital", "")), ""))
                self.add_s_box(value)
            remaining_intermittent_keys = {_intermittent_identity(item) for item in intermittent_to_carry}
            for item in intermittent_forecasts:
                if _intermittent_identity(item) in remaining_intermittent_keys:
                    continue
                value = dict(item)
                dates = item_dates(value)
                if not dates:
                    continue
                value.update({"mode": "間欠", "first_date": dates[0].isoformat(), "count": 0})
                value.setdefault("appointment_date", appointment_by_hospital.get(str(value.get("hospital", "")), ""))
                self.add_s_box(value, forecast=True)
            self._clear_o_boxes()
            self.add_o_box()
            self._display_options_changed()
            self.unmatched_mode.configure(
                state="disabled" if self._release_all_selected() else "readonly"
            )
            forecasts = imported.forecasts
            if forecasts:
                forecast_text = "　／　".join(
                    f"{item['hospital']}　{item['start']}{item.get('start_slot', '寝前')}から（未入荷）"
                    for item in forecasts
                )
                self.previous_forecast.configure(text=f"旧票からの処方待ち：{forecast_text}", foreground="#8A5A00")
                self.previous_forecast.grid()
            else:
                self.previous_forecast.grid_remove()
            self.assessment = None
            self.result = None
            self.inventory_record = None
            self.inventory_report = None
            self.inventory_corrections.clear()
            self.anomaly_reviews.clear()
            self.intermittent_items = [dict(item) for item in intermittent_to_carry]
            self.intermittent_forecasts = [dict(item) for item in intermittent_forecasts]
            self._update_patient_folder_label()
            self.assessment_actions.grid_remove()
            self.actual_editor.grid_remove()
            self.inventory_report_button.grid_remove()
            self.assessment_text.configure(
                text="旧ODTの未実施分をSへ読み込みました。内容を確認・修正してください。",
                style="TLabel",
            )
            self.result_instruction.configure(text="④で合包案を計算してください。")
            self.preview.delete("1.0", "end")
            warning_text = f"　要確認：{len(imported.warnings)}件" if imported.warnings else ""
            self.status.configure(
                text=f"旧ODTから預かり薬{len(imported.held)}件・処方待ち{len(imported.forecasts)}件を読み込みました。{warning_text}"
            )
            self.after_idle(self._update_workflow_colors)
            if imported.warnings:
                messagebox.showwarning(
                    "一部の行を確認してください",
                    "旧ODTの次の行は自動変換できませんでした。元票と照合してください。\n\n"
                    + "\n".join(imported.warnings[:8]),
                )
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            messagebox.showerror("旧ODTを読み込めません", str(exc))


class IntermittentManagerDialog(tk.Toplevel):
    def __init__(self, parent: MedicationSupportApp, items: list[dict], forecasts: list[dict], on_confirm) -> None:
        super().__init__(parent)
        self.title("間欠服用薬")
        self.geometry("780x430")
        self.transient(parent)
        self.grab_set()
        self.parent_app = parent
        self.items = [dict(item) for item in items]
        self.forecasts = [dict(item) for item in forecasts]
        self.on_confirm = on_confirm
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="間欠服用薬（通常のS・Oとは別に管理）", style="Step.TLabel").pack(anchor="w")
        ttk.Label(body, text="隔日・曜日指定・月1回など、服用日が連続しない薬を登録します。", foreground="#536A7A").pack(anchor="w", pady=(2, 8))
        self.listbox = tk.Listbox(body, font=("Yu Gothic UI", 10), height=12)
        self.listbox.pack(fill="both", expand=True)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="＋ 追加", command=self._add).pack(side="left")
        ttk.Button(actions, text="内容を修正", command=self._edit).pack(side="left", padx=5)
        ttk.Button(actions, text="処方待ちを今回受付へ", command=self._accept_forecast).pack(side="left", padx=5)
        ttk.Button(actions, text="削除", command=self._delete).pack(side="left")
        ttk.Button(actions, text="取り消す", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="確定して報告へ反映", style="Primary.TButton", command=self._confirm).pack(side="right", padx=5)
        self._refresh()

    def _refresh(self) -> None:
        self.listbox.delete(0, "end")
        for item in self.items:
            dates = item_dates(item)
            self.listbox.insert("end", f"{item['hospital']}　{item['drug']}　{item['slot']}　{item['count']}包（{rule_label(item)}）　{dates[0].month}/{dates[0].day}～{dates[-1].month}/{dates[-1].day}")
        for item in self.forecasts:
            dates = item_dates(item)
            self.listbox.insert("end", f"【処方待ち】{item['hospital']}　{item['drug']}　{item['slot']}（{rule_label(item)}）　{format_dates(dates)}～")

    def _add(self) -> None:
        IntermittentEditDialog(self.parent_app, None, lambda value: (self.items.append(value), self._refresh()))

    def _edit(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo("間欠服用薬", "修正する薬を選択してください。", parent=self)
            return
        index = selection[0]
        if index >= len(self.items):
            messagebox.showinfo("間欠服用薬", "処方待ちは「処方待ちを今回受付へ」から入力してください。", parent=self)
            return
        def apply(value):
            self.items[index] = value
            self._refresh()
            self.listbox.selection_set(index)
        IntermittentEditDialog(self.parent_app, self.items[index], apply)

    def _delete(self) -> None:
        selection = self.listbox.curselection()
        if selection:
            index = selection[0]
            if index < len(self.items):
                del self.items[index]
            else:
                del self.forecasts[index - len(self.items)]
            self._refresh()

    def _accept_forecast(self) -> None:
        selection = self.listbox.curselection()
        if not selection or selection[0] < len(self.items):
            messagebox.showinfo("間欠服用薬", "【処方待ち】の薬を選択してください。", parent=self)
            return
        index = selection[0] - len(self.items)
        forecast = self.forecasts[index]
        dates = item_dates(forecast)
        initial = dict(forecast)
        initial.pop("dose_dates", None)
        initial["first_date"] = dates[0].isoformat()
        initial["count"] = ""
        def apply(value):
            self.items.append(value)
            del self.forecasts[index]
            self._refresh()
        IntermittentEditDialog(self.parent_app, initial, apply)

    def _confirm(self) -> None:
        self.on_confirm(self.items, self.forecasts)
        self.destroy()


class IntermittentEditDialog(tk.Toplevel):
    RULES = ("指定日数おき", "曜日指定", "月1回")

    def __init__(self, parent: MedicationSupportApp, current: dict | None, on_confirm) -> None:
        super().__init__(parent)
        self.title("間欠服用薬の内容")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.parent_app = parent
        self.on_confirm = on_confirm
        value = current or {}
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="間欠服用薬の内容", style="Step.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        body.columnconfigure(1, weight=1)
        self.hospital = self._entry(body, 1, "医療機関", str(value.get("hospital", "")))
        self.drug = self._entry(body, 2, "薬品名", str(value.get("drug", "")))
        ttk.Label(body, text="服用時点").grid(row=3, column=0, sticky="w", pady=4)
        self.slot = ttk.Combobox(body, values=("朝", "昼", "夕", "寝前"), state="readonly", width=10)
        self.slot.grid(row=3, column=1, sticky="w", pady=4)
        self.slot.set(str(value.get("slot", "朝")))
        ttk.Label(body, text="初回服用日").grid(row=4, column=0, sticky="w", pady=4)
        self.first_date = ttk.Entry(body, width=18)
        self.first_date.grid(row=4, column=1, sticky="ew", pady=4)
        self.first_date.insert(0, str(value.get("first_date", date.today().isoformat())))
        ttk.Button(body, text="日付", command=lambda: parent.open_calendar(self.first_date)).grid(row=4, column=2, padx=5)
        ttk.Label(body, text="包数").grid(row=5, column=0, sticky="w", pady=4)
        self.count = ttk.Entry(body, width=12)
        self.count.grid(row=5, column=1, sticky="w", pady=4)
        self.count.insert(0, str(value.get("count", "")))
        ttk.Label(body, text="服用規則").grid(row=6, column=0, sticky="w", pady=4)
        self.rule = ttk.Combobox(body, values=self.RULES, state="readonly")
        self.rule.grid(row=6, column=1, sticky="ew", pady=4)
        self.rule.set(str(value.get("rule", "指定日数おき")))
        self.rule.bind("<<ComboboxSelected>>", lambda _event: self._rule_state())
        interval = ttk.Frame(body)
        interval.grid(row=7, column=1, columnspan=3, sticky="w", pady=4)
        ttk.Label(interval, text="何日おき").pack(side="left")
        self.interval_days = ttk.Entry(interval, width=7)
        self.interval_days.pack(side="left", padx=(5, 12))
        self.interval_days.insert(0, str(value.get("interval_days", 1)))
        ttk.Label(interval, text="例：1＝隔日、6＝週1回").pack(side="left")
        weekdays = ttk.Frame(body)
        weekdays.grid(row=8, column=1, columnspan=3, sticky="w", pady=4)
        self.weekday_vars = {}
        selected = set(int(number) for number in value.get("weekdays", ()))
        for index, label in enumerate(WEEKDAYS):
            variable = tk.BooleanVar(value=index in selected)
            ttk.Checkbutton(weekdays, text=label, variable=variable, style="Toolbutton").pack(side="left", padx=2)
            self.weekday_vars[index] = variable
        self.note = self._entry(body, 9, "備考", str(value.get("note", "")))
        self.preview_label = ttk.Label(body, text="", foreground="#276746", wraplength=560)
        self.preview_label.grid(row=10, column=0, columnspan=4, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=11, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="服用日を確認", command=self._preview).pack(side="left", padx=5)
        ttk.Button(buttons, text="取り消す", command=self.destroy).pack(side="left", padx=5)
        ttk.Button(buttons, text="確定", style="Primary.TButton", command=self._confirm).pack(side="left", padx=5)
        self._rule_state()

    @staticmethod
    def _entry(parent, row: int, label: str, value: str):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, width=45)
        entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)
        entry.insert(0, value)
        return entry

    def _rule_state(self) -> None:
        self.interval_days.configure(state="normal" if self.rule.get() == "指定日数おき" else "disabled")

    def _value(self) -> dict:
        hospital = self.hospital.get().strip()
        drug = self.drug.get().strip()
        if not hospital or not drug:
            raise ValueError("医療機関と薬品名を入力してください。")
        try:
            count = int(self.count.get().strip())
            interval_days = int(self.interval_days.get().strip() or "1")
            first = parse_date(self.first_date.get().strip())
        except ValueError as exc:
            raise ValueError("初回服用日・包数・服用間隔を確認してください。") from exc
        item = {
            "hospital": hospital, "drug": drug, "slot": self.slot.get(),
            "first_date": first.isoformat(), "count": count, "rule": self.rule.get(),
            "interval_days": interval_days,
            "weekdays": [index for index, variable in self.weekday_vars.items() if variable.get()],
            "note": self.note.get().strip(),
        }
        item_dates(item)
        return item

    def _preview(self) -> None:
        try:
            item = self._value()
            dates = item_dates(item)
        except ValueError as exc:
            messagebox.showwarning("入力を確認してください", str(exc), parent=self)
            return
        self.preview_label.configure(text=f"服用日：{format_dates(dates)}")

    def _confirm(self) -> None:
        try:
            value = self._value()
        except ValueError as exc:
            messagebox.showwarning("入力を確認してください", str(exc), parent=self)
            return
        self.destroy()
        self.on_confirm(value)


class OProcessingDialog(tk.Toplevel):
    def __init__(self, parent: MedicationSupportApp, current: str, on_confirm) -> None:
        super().__init__(parent)
        self.title("Oの処理を選択")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.value = tk.StringVar(value=current if current in ("合包対象", "在庫積み増し", "臨時追加") else "合包対象")
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="今回処方の処理", style="Step.TLabel").pack(anchor="w", pady=(0, 8))
        for value, explanation in (
            ("合包対象", "Sの薬と合わせて、今回お渡し分を作ります。"),
            ("在庫積み増し", "同じ医療機関の薬の日数を延長します。"),
            ("臨時追加", "既に交付した薬へ追加し、通常の合包期間には含めません。"),
        ):
            row = ttk.Frame(body)
            row.pack(fill="x", pady=4)
            ttk.Radiobutton(row, text=value, variable=self.value, value=value).pack(side="left")
            ttk.Label(row, text=explanation, foreground="#536A7A").pack(side="left", padx=(8, 0))
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="取り消す", command=self.destroy).pack(side="right")
        ttk.Button(
            actions, text="選択", style="Primary.TButton",
            command=lambda: (self.destroy(), on_confirm(self.value.get())),
        ).pack(side="right", padx=(0, 8))


class TemporaryAdditionDialog(tk.Toplevel):
    METHODS = ("既交付薬へ追加", "別添で交付", "施設職員へ追加依頼", "その他")

    def __init__(self, parent: MedicationSupportApp, source: MedicationPeriod, on_confirm) -> None:
        super().__init__(parent)
        self.title("臨時追加の内容")
        self.geometry("720x690")
        self.minsize(650, 600)
        self.transient(parent)
        self.grab_set()
        self.parent_app = parent
        self.source = source
        self.on_confirm = on_confirm
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="臨時追加", style="Step.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"今回処方：{_period_display(source)}", justify="left").pack(anchor="w", pady=(8, 4))
        target = ttk.LabelFrame(body, text="追加先", style="Section.TLabelframe", padding=10)
        target.pack(fill="x", pady=5)
        ttk.Label(target, text="既交付期間・対象").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.target_period = ttk.Entry(target)
        self.target_period.grid(row=0, column=1, sticky="ew")
        target.columnconfigure(1, weight=1)

        applied_container = ttk.Frame(body)
        applied_container.pack(fill="x", pady=5)
        applied_container.columnconfigure(0, weight=1)
        self.fields = parent._period_box(applied_container, 0, "実際に追加した期間")
        _set_entry(self.fields["hospital"], source.hospital)
        self.fields["hospital"].configure(state="readonly")
        _set_entry(self.fields["start"], source.start.isoformat())
        _set_entry(self.fields["end"], source.end.isoformat())
        _set_entry(self.fields["days"], str(source.days))
        self.fields["start_slot"].set(source.start_slot)
        self.fields["end_slot"].set(source.end_slot)
        for slot, variable in self.fields["active_slots"].items():
            variable.set(slot in source.active_slots)

        details = ttk.LabelFrame(body, text="処理結果", style="Section.TLabelframe", padding=10)
        details.pack(fill="x", pady=5)
        details.columnconfigure(1, weight=1)
        ttk.Label(details, text="処理方法").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.method = ttk.Combobox(details, values=self.METHODS, state="readonly")
        self.method.set(self.METHODS[0])
        self.method.grid(row=0, column=1, sticky="ew", pady=4)
        self.carryover = tk.BooleanVar(value=False)
        ttk.Checkbutton(details, text="未使用分を次回へ引き継ぐ", variable=self.carryover).grid(
            row=1, column=1, sticky="w", pady=4
        )
        ttk.Label(details, text="備考").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=4)
        self.note = tk.Text(details, height=3, wrap="word", font=("Yu Gothic UI", 10))
        self.note.grid(row=2, column=1, sticky="ew", pady=4)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="取り消す", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="臨時追加を確定", style="Primary.TButton", command=self._confirm).pack(side="right", padx=(0, 8))

    def _confirm(self) -> None:
        try:
            applied = self.parent_app._period(self.fields)
            applied.validate()
            remaining_outside_interval(
                self.source, applied.start, applied.end, applied.start_slot, applied.end_slot
            )
            if self.method.get() == "その他" and not self.note.get("1.0", "end").strip():
                raise ValidationError("その他の処理内容を備考へ入力してください。")
        except ValidationError as exc:
            messagebox.showwarning("臨時追加の内容を確認してください", str(exc), parent=self)
            return
        details = {
            "source": _period_to_record(self.source),
            "applied": _period_to_record(applied),
            "target_period": self.target_period.get().strip(),
            "method": self.method.get(),
            "carryover": self.carryover.get(),
            "note": self.note.get("1.0", "end").strip(),
        }
        self.destroy()
        self.on_confirm(details)


class HistoryDialog(tk.Toplevel):
    def __init__(self, parent: MedicationSupportApp) -> None:
        super().__init__(parent)
        self.title("外来服薬支援の履歴（閲覧のみ）")
        self.geometry("980x720")
        self.minsize(760, 520)
        self.transient(parent)
        current_paths = [*DEFAULT_DATA_DIR.glob("*/latest.json"), *DEFAULT_DATA_DIR.glob("*.json")]
        backup_paths = [
            *DEFAULT_DATA_DIR.glob("*/_backup/*.json"),
            *DEFAULT_DATA_DIR.glob("_backup/*/*.json"),
        ]
        self.paths = sorted(
            (*current_paths, *backup_paths),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="外来服薬支援の履歴（新しい順・閲覧のみ）", style="Step.TLabel").pack(anchor="w")
        content = ttk.Panedwindow(body, orient="horizontal")
        content.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(content)
        right = ttk.Frame(content)
        content.add(left, weight=1)
        content.add(right, weight=3)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(left, exportselection=False, font=("Yu Gothic UI", 10))
        list_scroll = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll.grid(row=0, column=1, sticky="ns")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.detail = tk.Text(right, wrap="word", font=("MS Gothic", 11), state="disabled")
        detail_scroll = ttk.Scrollbar(right, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.grid(row=0, column=0, sticky="nsew", padx=(8, 0))
        detail_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Button(body, text="閉じる", command=self.destroy).pack(anchor="e", pady=(8, 0))

        for path in self.paths:
            try:
                data = load_record(path)
                label = f"{data.get('service_date', '')}　{data.get('patient', '')}"
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                label = path.stem
            self.listbox.insert("end", label)
        self.listbox.bind("<<ListboxSelect>>", self._show_selected)
        if self.paths:
            self.listbox.selection_set(0)
            self._show_selected()
        else:
            self._set_detail("保存されたJSON記録はまだありません。")

    def _show_selected(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        try:
            data = load_record(self.paths[selection[0]])
            text = _history_record_text(data)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            text = f"記録を読み込めません。\n{exc}"
        self._set_detail(text)

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")


class InventoryCorrectionDialog(tk.Toplevel):
    REASONS = (
        "処方変更",
        "患者へ交付済み",
        "施設へ交付済み",
        "交付記録漏れ",
        "合包し忘れ・未交付",
        "副作用の疑い・副作用報告",
        "患者・家族の服用拒否",
        "体調変化",
        "紛失",
        "破損・汚損",
        "製造販売業者による回収",
        "入院・施設変更等",
        "数量の確認・訂正",
        "その他",
    )
    DISPOSITIONS = (
        "廃棄",
        "施設へ交付済み",
        "患者へ交付済み",
        "施設へ返却",
        "患者へ返却",
        "薬局で別途保管",
        "薬局預かりへ追加",
        "その他",
    )

    def __init__(
        self,
        parent: MedicationSupportApp,
        before: MedicationPeriod,
        prescribed_slots: tuple[str, ...],
        on_confirm,
    ) -> None:
        super().__init__(parent)
        self.title("薬局預かり薬の情報修正")
        self.geometry("720x790")
        self.minsize(620, 620)
        self.transient(parent)
        self.grab_set()
        self.parent_app = parent
        self.before = before
        self.before_prescribed_slots = prescribed_slots
        self.on_confirm = on_confirm

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="薬局預かり薬の情報修正", style="Step.TLabel").pack(anchor="w")

        before_box = ttk.LabelFrame(body, text="修正前（変更不可）", style="Section.TLabelframe", padding=10)
        before_box.pack(fill="x", pady=(10, 6))
        ttk.Label(before_box, text=_period_display(before), justify="left").pack(anchor="w")

        after_container = ttk.Frame(body)
        after_container.pack(fill="x", pady=6)
        after_container.columnconfigure(0, weight=1)
        self.fields = parent._period_box(after_container, 0, "修正後")
        self.fields["active_label"].configure(text="薬局保持時点")
        _set_entry(self.fields["hospital"], before.hospital)
        self.fields["hospital"].configure(state="readonly")
        _set_entry(self.fields["start"], before.start.isoformat())
        _set_entry(self.fields["end"], before.end.isoformat())
        _set_entry(self.fields["days"], str(before.days))
        self.fields["start_slot"].set(before.start_slot)
        self.fields["end_slot"].set(before.end_slot)
        for slot, variable in self.fields["active_slots"].items():
            variable.set(slot in before.active_slots)

        counts_box = ttk.LabelFrame(body, text="服用時点別の保持包数", style="Section.TLabelframe", padding=10)
        counts_box.pack(fill="x", pady=6)
        ttk.Label(counts_box, text="時点").grid(row=0, column=0, padx=5)
        ttk.Label(counts_box, text="期間からの計算").grid(row=0, column=1, padx=5)
        ttk.Label(counts_box, text="実際の確認数").grid(row=0, column=2, padx=5)
        self.actual_count_entries = {}
        before_counts = period_slot_counts(before)
        for row_index, slot in enumerate(("朝", "昼", "夕", "寝前"), start=1):
            calculated = before_counts[slot]
            ttk.Label(counts_box, text=slot).grid(row=row_index, column=0, padx=5, pady=3)
            ttk.Label(counts_box, text="-" if calculated is None else str(calculated), width=12).grid(row=row_index, column=1, padx=5, pady=3)
            entry = ttk.Entry(counts_box, width=12)
            entry.grid(row=row_index, column=2, padx=5, pady=3)
            if calculated is None:
                entry.insert(0, "-")
                entry.configure(state="disabled")
            else:
                entry.insert(0, str(calculated))
            self.actual_count_entries[slot] = entry
        ttk.Label(counts_box, text="期間で表せない過不足はOへ移し、Sの日付を維持します。", foreground="#8A5A00").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )

        prescription_box = ttk.LabelFrame(body, text="処方上の用法", style="Section.TLabelframe", padding=10)
        prescription_box.pack(fill="x", pady=6)
        self.change_prescribed = tk.BooleanVar(value=False)
        ttk.Radiobutton(
            prescription_box, text="変更しない", variable=self.change_prescribed, value=False,
            command=self._update_prescribed_state,
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(
            prescription_box, text="変更する", variable=self.change_prescribed, value=True,
            command=self._update_prescribed_state,
        ).pack(side="left", padx=(0, 12))
        self.prescribed_vars = {
            slot: tk.BooleanVar(value=slot in prescribed_slots) for slot in ("朝", "昼", "夕", "寝前")
        }
        self.prescribed_buttons = []
        for slot in ("朝", "昼", "夕", "寝前"):
            button = ttk.Checkbutton(
                prescription_box, text=slot, variable=self.prescribed_vars[slot], style="Toolbutton"
            )
            button.pack(side="left", padx=2)
            self.prescribed_buttons.append(button)
        self._update_prescribed_state()

        details = ttk.LabelFrame(body, text="修正内容", style="Section.TLabelframe", padding=10)
        details.pack(fill="x", pady=6)
        details.columnconfigure(1, weight=1)
        ttk.Label(details, text="修正理由").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.reason = ttk.Combobox(details, values=self.REASONS, state="readonly")
        self.reason.grid(row=0, column=1, sticky="ew", pady=4)
        self.reason.bind("<<ComboboxSelected>>", self._reason_selected)
        ttk.Label(details, text="処理方法").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.disposition = ttk.Combobox(details, values=self.DISPOSITIONS, state="readonly")
        self.disposition.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(details, text="備考・詳細").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=4)
        self.note = tk.Text(details, height=3, wrap="word", font=("Yu Gothic UI", 10))
        self.note.grid(row=2, column=1, sticky="ew", pady=4)

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="取り消す", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="修正を確定", style="Primary.TButton", command=self._confirm).pack(side="right", padx=(0, 8))

    def _update_prescribed_state(self) -> None:
        state = "normal" if self.change_prescribed.get() else "disabled"
        for button in self.prescribed_buttons:
            button.configure(state=state)

    def _reason_selected(self, _event=None) -> None:
        mapping = {
            "患者へ交付済み": "患者へ交付済み",
            "施設へ交付済み": "施設へ交付済み",
        }
        disposition = mapping.get(self.reason.get())
        if disposition:
            self.disposition.set(disposition)

    def _confirm(self) -> None:
        try:
            before_counts = period_slot_counts(self.before)
            actual_counts = {}
            for slot, entry in self.actual_count_entries.items():
                if before_counts[slot] is None:
                    actual_counts[slot] = None
                    continue
                try:
                    value = int(entry.get().strip())
                except ValueError as exc:
                    raise ValidationError(f"{slot}の実際の確認数を整数で入力してください。") from exc
                if value < 0:
                    raise ValidationError(f"{slot}の実際の確認数は0以上で入力してください。")
                actual_counts[slot] = value
            differences = {
                slot: actual_counts[slot] - before_counts[slot]
                for slot in ("朝", "昼", "夕", "寝前")
                if before_counts[slot] is not None and actual_counts[slot] != before_counts[slot]
            }
            if differences:
                active_slots = tuple(
                    slot for slot in ("朝", "昼", "夕", "寝前")
                    if slot in self.before.active_slots and (actual_counts[slot] or 0) > 0
                )
                represented = continuous_period_from_counts(
                    self.before.hospital,
                    self.before.start,
                    self.before.start_slot,
                    active_slots,
                    {slot: actual_counts[slot] or 0 for slot in active_slots},
                )
                if represented is not None:
                    after = represented
                    differences = {}
                    days_text = str(after.days)
                elif all((actual_counts[slot] or 0) == 0 for slot in active_slots):
                    days_text = "0"
                    differences = {}
                else:
                    days_text = self.fields["days"].get().strip()
            else:
                days_text = self.fields["days"].get().strip()
            if days_text == "0":
                after = {
                    "hospital": self.before.hospital,
                    "start": self.fields["start"].get().strip(),
                    "end": "",
                    "days": 0,
                    "start_slot": self.fields["start_slot"].get(),
                    "end_slot": "",
                    "active_slots": [
                        slot for slot in ("朝", "昼", "夕", "寝前") if self.fields["active_slots"][slot].get()
                    ],
                }
            else:
                if not differences and 'represented' in locals() and represented is not None:
                    after = represented
                else:
                    after = self.parent_app._period(self.fields)
                after.validate()
            reason = self.reason.get().strip()
            disposition = self.disposition.get().strip()
            note = self.note.get("1.0", "end").strip()
            if not reason:
                raise ValidationError("修正理由を選択してください。")
            if not disposition:
                raise ValidationError("処理方法を選択してください。")
            if (reason == "その他" or disposition == "その他") and not note:
                raise ValidationError("「その他」の内容を備考・詳細へ入力してください。")
            prescribed_slots = tuple(
                slot for slot in ("朝", "昼", "夕", "寝前") if self.prescribed_vars[slot].get()
            ) if self.change_prescribed.get() else self.before_prescribed_slots
            if not prescribed_slots:
                raise ValidationError("処方上の用法を1つ以上選択してください。")
            if self.change_prescribed.get() and reason != "処方変更":
                if not messagebox.askyesno(
                    "服用時点の変更確認",
                    "修正理由が「処方変更」ではありません。処方上の用法も変更しますか？",
                    parent=self,
                ):
                    return
            after_record = _period_to_record(after) if isinstance(after, MedicationPeriod) else after
            if after_record == _period_to_record(self.before) and not differences:
                raise ValidationError("修正前と修正後が同じです。変更内容を入力してください。")
        except (ValidationError, ValueError) as exc:
            messagebox.showwarning("修正内容を確認してください", str(exc), parent=self)
            return
        self.destroy()
        self.on_confirm(after, reason, disposition, note, differences, prescribed_slots)


class SlotPickerDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, current: str, on_select) -> None:
        super().__init__(parent)
        self.title("服用時点を選択")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        ttk.Label(self, text="開始・終了の時点を選んでください", padding=(14, 12, 14, 6)).pack()
        buttons = ttk.Frame(self, padding=(12, 4, 12, 12))
        buttons.pack()
        for slot in ("朝", "昼", "夕", "寝前"):
            label = f"● {slot}" if slot == current else slot
            ttk.Button(
                buttons,
                text=label,
                width=8,
                command=lambda value=slot: self._choose(value, on_select),
            ).pack(side="left", padx=3)

    def _choose(self, value: str, on_select) -> None:
        self.destroy()
        on_select(value)


class DatePickerDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, initial: date, on_select) -> None:
        super().__init__(parent)
        self.title("日付を選択")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.year = initial.year
        self.month = initial.month
        self.selected = initial
        self.on_select = on_select

        navigation = ttk.Frame(self, padding=(10, 10, 10, 4))
        navigation.pack(fill="x")
        ttk.Button(navigation, text="◀", width=4, command=lambda: self._move_month(-1)).pack(side="left")
        self.month_label = ttk.Label(navigation, anchor="center", font=("Yu Gothic UI", 12, "bold"))
        self.month_label.pack(side="left", fill="x", expand=True, padx=10)
        ttk.Button(navigation, text="▶", width=4, command=lambda: self._move_month(1)).pack(side="right")

        self.days = ttk.Frame(self, padding=(10, 4, 10, 6))
        self.days.pack()
        actions = ttk.Frame(self, padding=(10, 0, 10, 10))
        actions.pack(fill="x")
        ttk.Button(actions, text="今日", command=self._choose_today).pack(side="left")
        ttk.Button(actions, text="キャンセル", command=self.destroy).pack(side="right")
        self._draw()

    def _draw(self) -> None:
        for child in self.days.winfo_children():
            child.destroy()
        self.month_label.configure(text=f"{self.year}年 {self.month}月")
        for column, label in enumerate(("月", "火", "水", "木", "金", "土", "日")):
            ttk.Label(self.days, text=label, anchor="center", width=5).grid(row=0, column=column, pady=(0, 4))
        for row, week in enumerate(calendar.monthcalendar(self.year, self.month), start=1):
            for column, day_value in enumerate(week):
                if day_value == 0:
                    ttk.Label(self.days, text="", width=5).grid(row=row, column=column)
                    continue
                value = date(self.year, self.month, day_value)
                text = f"[{day_value}]" if value == self.selected else str(day_value)
                ttk.Button(
                    self.days,
                    text=text,
                    width=5,
                    command=lambda chosen=value: self._choose(chosen),
                ).grid(row=row, column=column, padx=1, pady=1)

    def _move_month(self, amount: int) -> None:
        month_index = self.year * 12 + self.month - 1 + amount
        self.year, zero_based_month = divmod(month_index, 12)
        self.month = zero_based_month + 1
        self._draw()

    def _choose_today(self) -> None:
        self._choose(date.today())

    def _choose(self, value: date) -> None:
        self.destroy()
        self.on_select(value)


def _set_entry(entry: ttk.Entry, value: str) -> None:
    entry.delete(0, "end")
    entry.insert(0, value)


def _fields_have_input(fields: dict) -> bool:
    return any(fields[key].get().strip() for key in ("hospital", "start", "days", "end"))


def _role_display(role: str) -> str:
    return {"在庫積み増し": "日数延長", "合包対象": "合包対応"}.get(role, role)


def _o_role_display(role: str, inherited: bool = False) -> str:
    if role == "合包対象":
        return "期間引継による対応" if inherited else "合包対応"
    return _role_display(role)


def _o_card_title(number: int, link: str, role: str) -> str:
    parts = [f"R{number}"]
    if link == "引継ぎ元削除済み":
        parts.append(link)
    elif link:
        parts.append(f"{_display_link(link)}より承継")
    parts.append(_o_role_display(role, bool(link)))
    return "　".join(parts)


def _display_link(link: str) -> str:
    if link.startswith("S") and link[1:].isdigit():
        return f"S{link[1:]}"
    if link.startswith("O") and link[1:].isdigit():
        return f"R{link[1:]}"
    return link


def _md(value: date) -> str:
    return f"{value.month}/{value.day}"


def _period_to_record(period: MedicationPeriod) -> dict:
    return {
        "hospital": period.hospital,
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "days": period.days,
        "start_slot": period.start_slot,
        "end_slot": period.end_slot,
        "active_slots": list(period.active_slots),
    }


def _record_to_period(value: dict) -> MedicationPeriod:
    return MedicationPeriod(
        hospital=str(value.get("hospital", "")),
        start=parse_date(value.get("start", "")),
        end=parse_date(value.get("end", "")),
        start_slot=str(value.get("start_slot", "朝")),
        end_slot=str(value.get("end_slot", "寝前")),
        active_slots=tuple(value.get("active_slots", ("朝", "昼", "夕", "寝前"))),
    )


def _slot_summary(slots) -> str:
    abbreviations = {"朝": "朝", "昼": "昼", "夕": "夕", "寝前": "寝"}
    return "".join(abbreviations[slot] for slot in ("朝", "昼", "夕", "寝前") if slot in slots)


def _record_period_text(value: dict) -> str:
    if int(value.get("days", 0)) == 0:
        return "0日分（薬局預かり在庫なし）"
    start = parse_date(value["start"])
    end = parse_date(value["end"])
    show_year = start.year != end.year
    start_text = f"{start.year}/{start.month}/{start.day}" if show_year else _md(start)
    end_text = f"{end.year}/{end.month}/{end.day}" if show_year else _md(end)
    slots = _slot_summary(value.get("active_slots", ("朝", "昼", "夕", "寝前")))
    return (
        f"{start_text}{value.get('start_slot', '朝')}～{end_text}{value.get('end_slot', '寝前')}　"
        f"{value.get('days', '')}日分（{slots}）"
    )


def _period_display(period: MedicationPeriod) -> str:
    return f"医療機関：{period.hospital}\n期間：{_record_period_text(_period_to_record(period))}"


def _history_record_text(data: dict) -> str:
    lines = [
        f"対象者：{data.get('patient', '')}",
        f"実施日：{data.get('service_date', '')}",
        f"施設：{data.get('facility', '')}",
        f"薬局：{data.get('pharmacy', '')}",
    ]
    reconciliation = data.get("reconciliation")
    if reconciliation:
        lines.append(f"検算：{reconciliation.get('status', '未実施')}")
    sections = (
        ("■合包待ち・所持薬", data.get("held", [])),
        ("■今回受付分", data.get("incoming_items", [])),
        ("■今回お渡し分", data.get("result", {}).get("delivered", [])),
        ("■薬局預かり分", data.get("result", {}).get("uncombined", [])),
    )
    for heading, records in sections:
        if not records:
            continue
        lines.extend(("", heading))
        for record in records:
            lines.append(f"{record.get('hospital', '')}　{_record_period_text(record)}")
    forecasts = data.get("next_creations", [])
    if forecasts:
        lines.extend(("", "■次の処方待ち"))
        for item in forecasts:
            lines.append(f"{item.get('hospital', '')}　{item.get('start', '')}{item.get('start_slot', '')}から")
    corrections = data.get("inventory_corrections", [])
    if corrections:
        lines.extend(("", "■情報修正履歴"))
        for item in corrections:
            before = item.get("before", {})
            lines.append(f"{before.get('hospital', '')}　{item.get('reason', '')}／{item.get('disposition', '')}")
            if item.get("note"):
                lines.append(f"備考：{item['note']}")
    temporary = data.get("temporary_additions", [])
    if temporary:
        lines.extend(("", "■臨時追加実施"))
        for item in temporary:
            applied = item.get("applied", {})
            lines.append(f"{applied.get('hospital', '')}　{_record_period_text(applied)}")
            lines.append(f"処理方法：{item.get('method', '')}／引継ぎ：{'あり' if item.get('carryover') else 'なし'}")
    intermittent = data.get("intermittent_items", [])
    if intermittent:
        lines.extend(("", "■間欠服用薬"))
        for item in intermittent:
            try:
                dates = item_dates(item)
                lines.append(f"{item.get('hospital', '')}　{item.get('drug', '')}　{item.get('slot', '')}　{item.get('count', '')}包（{rule_label(item)}）")
                lines.append(f"服用日：{format_dates(dates)}")
            except (ValueError, KeyError, TypeError, IndexError):
                lines.append(f"{item.get('hospital', '')}　{item.get('drug', '')}（日付を確認してください）")
    return "\n".join(lines)


def _print_period_row(period: MedicationPeriod, mark: str = "") -> dict:
    slots = ("朝", "昼", "夕", "寝前")
    counts = {slot: 0 for slot in slots}
    start_point = period.start.toordinal() * 4 + slots.index(period.start_slot)
    end_point = period.end.toordinal() * 4 + slots.index(period.end_slot)
    for point in range(start_point, end_point + 1):
        slot = slots[point % 4]
        if slot in period.active_slots:
            counts[slot] += 1
    short_slot = {"朝": "朝", "昼": "昼", "夕": "夕", "寝前": "寝"}
    return {
        "hospital": period.hospital,
        "mark": mark,
        "period": (
            f"{period.start.month}/{period.start.day}{short_slot[period.start_slot]}-"
            f"{period.end.month}/{period.end.day}{short_slot[period.end_slot]}"
        ),
        "active_slots": list(period.active_slots),
        "counts": counts,
    }


def _release_all_sorting_rows(result: PackagingResult, symbols: dict[str, str]) -> list[dict]:
    """全量交付を、合包期間の前・中・後の仕分け単位に分ける。"""
    slots = ("朝", "昼", "夕", "寝前")

    def points(period: MedicationPeriod) -> set[int]:
        low = period.start.toordinal() * 4 + slots.index(period.start_slot)
        high = period.end.toordinal() * 4 + slots.index(period.end_slot)
        return {point for point in range(low, high + 1) if slots[point % 4] in period.active_slots}

    source_points = [(period, points(period)) for period in result.assessment.sources]
    source_points = [(period, values) for period, values in source_points if values]
    if not source_points:
        return []
    common_low = max(min(values) for _period, values in source_points)
    common_high = min(max(values) for _period, values in source_points)
    overall_low = min(min(values) for _period, values in source_points)
    overall_high = max(max(values) for _period, values in source_points)
    boundaries = {overall_low, common_low, common_high + 1, overall_high + 1}
    # Within the before/after portions, separate days when the participating
    # hospitals change; otherwise a shorter course appears to last as long as
    # the longest course in the same printed row.
    for _period, values in source_points:
        if min(values) < common_low:
            boundaries.add((min(values) // 4) * 4)
        if max(values) > common_high:
            boundaries.add((max(values) // 4 + 1) * 4)
    boundaries = sorted(point for point in boundaries if overall_low <= point <= overall_high + 1)
    ranges = [(low, end - 1) for low, end in zip(boundaries, boundaries[1:])]
    rows = []
    for low, high in ranges:
        if high < low:
            continue
        included = [(period, {point for point in values if low <= point <= high}) for period, values in source_points]
        included = [(period, values) for period, values in included if values]
        all_points = set().union(*(values for _period, values in included)) if included else set()
        if not all_points:
            continue
        first, last = min(all_points), max(all_points)
        first_day, first_slot_index = divmod(first, 4)
        last_day, last_slot_index = divmod(last, 4)
        counts = {slot: sum(point % 4 == index for point in all_points) for index, slot in enumerate(slots)}
        marks = "".join(dict.fromkeys(symbols[period.hospital] for period, _values in included))
        rows.append({
            "hospital": f"仕分け{len(rows) + 1}",
            "symbols": marks,
            "period": (
                f"{date.fromordinal(first_day).month}/{date.fromordinal(first_day).day}"
                f"{'寝' if slots[first_slot_index] == '寝前' else slots[first_slot_index]}-"
                f"{date.fromordinal(last_day).month}/{date.fromordinal(last_day).day}"
                f"{'寝' if slots[last_slot_index] == '寝前' else slots[last_slot_index]}"
            ),
            "active_slots": [slot for slot in slots if counts[slot]],
            "counts": counts,
        })
    return rows


def _inventory_print_table_data(record: dict) -> dict | None:
    """日数延長をPDF/ODT共通の表で表す。

    所持薬は「受付前+今回受付」、薬局預かり分は連結後の数量とし、
    従来ODTの表現と同じ意味を保つ。
    """
    changes = record.get("inventory_changes") or ()
    if not changes:
        return None
    hospitals = [change.get("after", {}).get("hospital", "") for change in changes]
    symbols = _hospital_symbol_map(hospitals)
    sources = []
    for change in changes:
        before = _record_to_period(change["before"])
        incoming = _record_to_period(change["incoming"])
        before_row = _print_period_row(before)
        incoming_row = _print_period_row(incoming)
        active_slots = [slot for slot in ("朝", "昼", "夕", "寝前") if slot in before.active_slots or slot in incoming.active_slots]
        counts = {}
        for slot in ("朝", "昼", "夕", "寝前"):
            parts = [
                str(row["counts"][slot])
                for row in (before_row, incoming_row)
                if slot in row["active_slots"] and row["counts"][slot]
            ]
            counts[slot] = "+".join(parts) if parts else 0
        sources.append({
            "hospital": before.hospital,
            "mark": symbols.get(before.hospital, ""),
            "period": f"{before_row['period']},{incoming_row['period']}",
            "active_slots": active_slots,
            "counts": counts,
        })
    remaining = [
        _print_period_row(_record_to_period(value), symbols.get(value.get("hospital", ""), ""))
        for value in record.get("result", {}).get("uncombined", ())
    ]
    return {
        "release_all": False,
        "sources": sources,
        "executed": None,
        "uncombined": remaining,
        "intermittent_all": [],
        "intermittent": [],
        "intermittent_remaining": [],
    }


def _hospital_symbol_map(hospitals) -> dict[str, str]:
    symbols = ("◇", "☆", "△", "○", "□", "▽")
    result: dict[str, str] = {}
    for hospital in hospitals:
        if hospital in result:
            continue
        index = len(result)
        result[hospital] = symbols[index] if index < len(symbols) else f"◇{index - len(symbols) + 1}"
    return result


def _intermittent_identity(item: dict) -> tuple[str, str, str]:
    """Identify the same intermittent medicine across remaining and forecast records."""
    return tuple(str(item.get(key, "")).strip() for key in ("hospital", "drug", "slot"))


def main() -> None:
    # ビルド後に、画面を開かず主要モジュールの同梱を確認する。
    if "--self-test" in sys.argv:
        _self_test()
        return
    app = MedicationSupportApp()
    startup_path = _startup_import_path(sys.argv[1:])
    if startup_path is not None:
        app.after_idle(lambda value=startup_path: app.load_previous_s(value))
    app.mainloop()


def _self_test() -> None:
    """Verify that a relocated executable can write JSON and create a PDF."""
    parent = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    with TemporaryDirectory(prefix="medi_sup_self_test_", dir=parent) as directory:
        target = Path(directory)
        record_path = save_record(
            {"workflow": "SOAP", "patient": "self_test", "service_date": "2000-01-01"}, target
        )
        if load_record(record_path).get("patient") != "self_test":
            raise RuntimeError("JSON自己検査に失敗しました。")
        pdf_path = export_90mm_report(
            "自己検査様　【外来服薬支援】　実施日 2000-01-01",
            target / "self_test.pdf",
        )
        if not pdf_path.read_bytes().startswith(b"%PDF"):
            raise RuntimeError("PDF自己検査に失敗しました。")


def _startup_import_path(arguments) -> Path | None:
    """実行ファイル自体へドロップされた記録を返す。"""
    for argument in arguments:
        candidate = Path(argument)
        if candidate.is_file() and candidate.suffix.lower() in (".json", ".odt"):
            return candidate.resolve()
    return None


def _normalized_delivery_mode(value: str) -> str:
    return DELIVERY_RELEASE if value in (DELIVERY_RELEASE, "すべて払い出し") else DELIVERY_STORE


def _normalized_unmatched_mode(value: str) -> str:
    legacy_release = ("すべて渡す", "重ならない服用時点もすべて渡す")
    return UNMATCHED_RELEASE if value in (UNMATCHED_RELEASE, *legacy_release) else UNMATCHED_STORE


if __name__ == "__main__":
    main()
