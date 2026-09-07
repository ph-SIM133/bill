from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from .model import ValidationError


NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
}
SLOTS = ("朝", "昼", "夕", "寝前")


@dataclass(frozen=True)
class OdtImportResult:
    patient: str
    held: tuple[dict, ...]
    forecasts: tuple[dict, ...]
    warnings: tuple[str, ...]
    metadata: dict[str, str] = field(default_factory=dict)


def import_previous_odt(path: str | Path) -> OdtImportResult:
    source = Path(path)
    try:
        with zipfile.ZipFile(source) as archive:
            xml = archive.read("content.xml")
            metadata = _read_metadata(archive.read("meta.xml")) if "meta.xml" in archive.namelist() else {}
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"ODTを読み込めません：{exc}") from exc
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValidationError(f"ODTの文書構造を解析できません：{exc}") from exc

    body = root.find(".//office:text", NS)
    if body is None:
        raise ValidationError("ODT本文が見つかりません。")
    # Appended reports are separated by our explicit page-break paragraph.
    # Never fall through into a historical stock table when the newest has none.
    latest = ET.Element(body.tag)
    for child in body:
        if child.get(f"{{{NS['text']}}}style-name") == "PageBreak":
            break
        latest.append(child)
    body = latest
    patient = _patient_name(body)
    base_year = _service_year(body)
    # v0.12.2 and earlier wrote an inventory-extension report as text above
    # the historical tables.  Prefer that newest carry-over snapshot when it
    # exists; otherwise the loader would silently fall back to an older table.
    text_inventory = _find_text_inventory_carryover(body, base_year)
    if text_inventory:
        return OdtImportResult(patient, tuple(text_inventory), (), (), metadata)
    table_match = _find_uncompleted_table(body)
    if table_match is None:
        post_states = _next_creation_forecasts(metadata) or _post_delivery_forecasts(metadata)
        if post_states:
            return OdtImportResult(patient, (), tuple(post_states), (), metadata)
        if metadata.get("DateManagement") == "0":
            return OdtImportResult(patient, (), (), (), metadata)
        raise ValidationError("旧票の「未実施分」表が見つかりません。")
    table, first_data_row = table_match

    held: list[dict] = []
    forecasts: list[dict] = []
    warnings: list[str] = []
    rows = table.findall("table:table-row", NS)
    for row_number, row in enumerate(rows[first_data_row:], start=first_data_row + 1):
        cells = row.findall("table:table-cell", NS)
        if len(cells) < 5:
            continue
        paragraphs = [_text(paragraph).strip() for paragraph in cells[0].findall("text:p", NS)]
        paragraphs = [value for value in paragraphs if value]
        if not paragraphs:
            continue
        hospital, period_text = _identity_and_period(paragraphs)
        values = [_text(cell).strip() for cell in cells[1:5]]
        active_slots = tuple(slot for slot, value in zip(SLOTS, values) if value not in ("", "-"))
        if not active_slots:
            continue
        counts = [_number(value) for value in values if value not in ("", "-")]
        try:
            start, start_slot, end, end_slot = _parse_period(
                period_text, base_year, active_slots[0], active_slots[-1]
            )
        except ValidationError as exc:
            warnings.append(f"{hospital}（表{row_number}行目）：{exc}")
            continue
        if not counts or max(counts) == 0 or end is None:
            forecasts.append(
                {
                    "hospital": hospital,
                    "start": start.isoformat(),
                    "start_slot": start_slot,
                    "end": "",
                    "days": "",
                    "end_slot": "寝前",
                    "active_slots": list(active_slots),
                }
            )
            continue
        held.append(
            {
                "hospital": hospital,
                "start": start.isoformat(),
                "start_slot": start_slot,
                "end": end.isoformat(),
                "end_slot": end_slot,
                "days": max(counts),
                "active_slots": list(active_slots),
            }
        )
    known = {item["hospital"] for item in (*held, *forecasts)}
    forecasts.extend(item for item in _next_creation_forecasts(metadata) if item["hospital"] not in known)
    if not held and not forecasts:
        raise ValidationError("未実施分から次回へ引き継げる薬を抽出できませんでした。")
    return OdtImportResult(patient, tuple(held), tuple(forecasts), tuple(warnings), metadata)


def _next_creation_forecasts(metadata: dict[str, str]) -> list[dict]:
    try:
        values = json.loads(metadata.get("NextCreations", "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    return [dict(item, end="", days="") for item in values
            if isinstance(item, dict) and item.get("hospital") and item.get("start")]


def _post_delivery_forecasts(metadata: dict[str, str]) -> list[dict]:
    try:
        states = json.loads(metadata.get("PostDeliveryStates", "[]"))
    except (TypeError, ValueError):
        return []
    return [
        {
            "hospital": str(item.get("hospital", "")),
            "start": str(item.get("next_start", "")),
            "start_slot": str(item.get("next_start_slot", "朝")),
            "end": "",
            "days": "",
            "end_slot": "寝前",
            "active_slots": list(item.get("active_slots", ("朝", "昼", "夕", "寝前"))),
        }
        for item in states
        if isinstance(item, dict) and item.get("hospital") and item.get("next_start")
    ]


def _read_metadata(xml: bytes) -> dict[str, str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    result = {}
    name_key = f"{{{NS['meta']}}}name"
    for item in root.findall(".//meta:user-defined", NS):
        name = item.get(name_key, "")
        if name.startswith("MediSup."):
            result[name[len("MediSup."):]] = _text(item).strip()
    return result


def _find_text_inventory_carryover(body: ET.Element, base_year: int) -> list[dict]:
    paragraphs = [_text(value).strip() for value in body.findall("text:p", NS)]
    try:
        start = next(index for index, value in enumerate(paragraphs) if "次回へ引き継ぐ薬局預かり" in value)
    except StopIteration:
        return []
    result = []
    pattern = re.compile(
        r"^(.*?)\s+((?:\d{4}/)?\d{1,2}/\d{1,2}(?:朝|昼|夕|寝前)[～~-]"
        r"(?:\d{4}/)?\d{1,2}/\d{1,2}(?:朝|昼|夕|寝前))\s+(\d+)日分（([^）]+)）"
    )
    for value in paragraphs[start + 1:]:
        if value.startswith("★") or "【外来服薬支援】" in value:
            break
        match = pattern.search(value)
        if not match:
            continue
        hospital, period_text, days_text, slots_text = match.groups()
        active_slots = tuple(
            slot for token, slot in (("朝", "朝"), ("昼", "昼"), ("夕", "夕"), ("寝", "寝前"))
            if token in slots_text
        )
        if not active_slots:
            continue
        try:
            period_start, start_slot, period_end, end_slot = _parse_period(
                period_text, base_year, active_slots[0], active_slots[-1]
            )
        except ValidationError:
            continue
        if period_end is None:
            continue
        result.append({
            "hospital": hospital.strip(),
            "start": period_start.isoformat(),
            "start_slot": start_slot,
            "end": period_end.isoformat(),
            "end_slot": end_slot,
            "days": int(days_text),
            "active_slots": list(active_slots),
        })
    return result


def _find_uncompleted_table(body: ET.Element) -> tuple[ET.Element, int] | None:
    # Current exports name the carry-over table explicitly.  Reports are
    # prepended, so the first matching table is the newest record.
    table_name = f"{{{NS['table']}}}name"
    for table in body.findall("table:table", NS):
        if table.get(table_name, "").endswith("MediSupRemaining"):
            return table, 1
    # Some historical sheets place the 未実施分 heading and rows inside the 所持薬 table.
    # Check this first so a newer combined table wins over older separate tables later in the file.
    for table in body.findall("table:table", NS):
        rows = table.findall("table:table-row", NS)
        for index, row in enumerate(rows):
            if "★未実施分" in _text(row).replace(" ", ""):
                return table, index + 1
    waiting = False
    table_tag = f"{{{NS['table']}}}table"
    paragraph_tag = f"{{{NS['text']}}}p"
    for child in body:
        if child.tag == paragraph_tag:
            value = _text(child).replace(" ", "")
            if "★未実施分" in value or "薬局預かり分" in value:
                waiting = True
        elif waiting and child.tag == table_tag:
            return child, 1
    return None


def _patient_name(body: ET.Element) -> str:
    for paragraph in body.findall("text:p", NS):
        value = _text(paragraph).strip()
        if "【外来服薬支援】" in value:
            return value.split("様", 1)[0].strip()
    return ""


def _service_year(body: ET.Element) -> int:
    for paragraph in body.findall("text:p", NS):
        value = _text(paragraph)
        if "【外来服薬支援】" not in value:
            continue
        match = re.search(r"(?:実施日\D*)?(\d{2,4})[/.年]", value)
        if match:
            year = int(match.group(1))
            return year + 2000 if year < 100 else year
    return date.today().year


def _identity_and_period(paragraphs: list[str]) -> tuple[str, str]:
    combined = "".join(paragraphs)
    date_pattern = r"(?:\d{4}/)?(?:1[0-2]|[1-9])/(?:3[01]|[12]\d|[1-9])"
    starts = [index for index in range(len(combined)) if re.match(date_pattern, combined[index:])]
    if not starts:
        return combined, ""
    period_pattern = rf"{date_pattern}[^\d/]*-(?:{date_pattern}[^\d/]*)?"
    start_index = next(
        (index for index in starts if re.fullmatch(period_pattern, combined[index:])),
        starts[-1],
    )
    hospital = combined[:start_index].strip()
    period = combined[start_index:].strip()
    # Current external sheets prefix institutions with a stable monochrome
    # origin symbol.  It is presentation metadata, not part of the name.
    hospital = re.sub(r"^[◇☆△○□▽]+", "", hospital).strip()
    # Old sheets sometimes prefixed institutions with row numbers (1AHP, 2Bメンタル).
    hospital = re.sub(r"^\d+(?=[A-Za-zＡ-Ｚａ-ｚ])", "", hospital)
    return hospital, period


def _parse_period(value: str, base_year: int, default_start_slot: str, default_end_slot: str):
    normalized = (
        value.replace("～", "-")
        .replace("−", "-")
        .replace("ー", "-")
        .replace("ﾈﾙ", "寝前")
        .replace("ネル", "寝前")
        .replace("ｱｻ", "朝")
        .replace("アサ", "朝")
        .replace("ﾋﾙ", "昼")
        .replace("ﾕｳ", "夕")
        .replace(" ", "")
    )
    parts = normalized.split("-", 1)
    start, start_slot = _date_slot(parts[0], base_year, default_start_slot)
    if len(parts) == 1 or not parts[1]:
        return start, start_slot, None, default_end_slot
    end, end_slot = _date_slot(parts[1], base_year, default_end_slot)
    if end < start:
        end = date(end.year + 1, end.month, end.day)
    return start, start_slot, end, end_slot


def _date_slot(value: str, base_year: int, default_slot: str) -> tuple[date, str]:
    slot = next((slot for slot in ("寝前", "朝", "昼", "夕") if slot in value), None)
    if slot is None:
        slot = default_slot
    numbers = [int(number) for number in re.findall(r"\d+", value)]
    try:
        if len(numbers) >= 3:
            year, month, day = numbers[-3:]
            if year < 100:
                year += 2000
        elif len(numbers) == 2:
            year, (month, day) = base_year, numbers
        else:
            raise ValueError
        return date(year, month, day), slot
    except ValueError as exc:
        raise ValidationError(f"期間「{value}」の日付を判定できません。") from exc


def _number(value: str) -> int:
    match = re.search(r"-?\d+", value)
    return int(match.group()) if match else 0


def _text(element: ET.Element) -> str:
    return "".join(element.itertext())
