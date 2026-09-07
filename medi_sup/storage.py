from __future__ import annotations

import json
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .version import VERSION


if getattr(sys, "frozen", False):
    # 単体exeでは、配布したexeの位置を保存の基準にする。
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = APP_ROOT / "work_dir" / "medi_sup"
PREFERENCES_FILE = DEFAULT_DATA_DIR / "settings.json"


def patient_directory(patient: str, data_dir: Path | None = None, patient_id: str = "") -> Path:
    """Return the stable folder used for one patient's records and outputs."""
    patient_name = _safe_name(str(patient)) or "record"
    identifier = _safe_name(str(patient_id))
    folder_name = f"{identifier}_{patient_name}" if identifier else patient_name
    return Path(data_dir or DEFAULT_DATA_DIR) / folder_name


def load_preferences(data_dir: Path | None = None) -> dict:
    path = Path(data_dir or DEFAULT_DATA_DIR) / "settings.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_preferences(preferences: dict, data_dir: Path | None = None) -> Path:
    target_dir = Path(data_dir or DEFAULT_DATA_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "settings.json"
    temporary = path.with_suffix(".json.tmp")
    try:
        current = load_preferences(target_dir)
        current.update(preferences)
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def save_error_log(exc_type, exc_value, exc_traceback, data_dir: Path | None = None) -> Path:
    """画面操作中の想定外エラーを、後から確認できるログへ保存する。"""
    target_dir = Path(data_dir or DEFAULT_DATA_DIR) / "error_logs"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = target_dir / f"{stamp}_error.log"
    detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    path.write_text(
        f"{VERSION=}\n{datetime.now().isoformat()}\n\n{detail}",
        encoding="utf-8",
    )
    return path


def save_record(record: dict, data_dir: Path | None = None) -> Path:
    """患者フォルダのlatest.jsonへ保存し、上書き前の内容を退避する。"""
    record.setdefault("app_version", VERSION)
    patient = _safe_name(str(record.get("patient", "record"))) or "record"
    target_dir = patient_directory(patient, data_dir, str(record.get("patient_id", "")))
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = target_dir / "latest.json"
    if path.is_file():
        backup_dir = target_dir / "_backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / f"{stamp}.json")
    temp = path.with_suffix(".json.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return path


def load_record(path: Path | str) -> dict:
    source = Path(path)
    if source.is_dir():
        source = source / "latest.json"
    with source.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("記録ファイルの形式が正しくありません。")
    return data


def _safe_name(value: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if char in invalid else char for char in value.strip())
    return cleaned[:40]
