from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")


def calculate_dose_dates(start: date, count: int, rule: str, interval_days: int = 1,
                         weekdays: Iterable[int] = ()) -> list[date]:
    if count <= 0:
        raise ValueError("包数は1以上で入力してください。")
    if rule == "指定日数おき":
        if interval_days < 1:
            raise ValueError("服用間隔は1日以上で入力してください。")
        step = interval_days + 1
        return [start + timedelta(days=step * index) for index in range(count)]
    if rule == "曜日指定":
        selected = set(int(value) for value in weekdays)
        if not selected:
            raise ValueError("服用する曜日を1つ以上選択してください。")
        result = []
        current = start
        while len(result) < count:
            if current.weekday() in selected:
                result.append(current)
            current += timedelta(days=1)
        return result
    if rule == "月1回":
        result = []
        current = start
        target_day = start.day
        for _ in range(count):
            result.append(current)
            year, month = current.year, current.month + 1
            if month == 13:
                year, month = year + 1, 1
            import calendar
            current = date(year, month, min(target_day, calendar.monthrange(year, month)[1]))
        return result
    raise ValueError("服用規則を選択してください。")


def rule_label(item: dict) -> str:
    rule = item.get("rule", "")
    if rule == "指定日数おき":
        days = int(item.get("interval_days", 1))
        return "隔日" if days == 1 else f"{days}日おき"
    if rule == "曜日指定":
        return "・".join(WEEKDAYS[int(value)] for value in item.get("weekdays", ()))
    return "月1回"


def format_dates(values: Iterable[date]) -> str:
    return ",".join(f"{value.month}/{value.day}" for value in values)


def format_dates_compact(values: Iterable[date]) -> str:
    dates = list(values)
    if len(dates) <= 4:
        return format_dates(dates)
    return f"{format_dates(dates[:2])}～{format_dates(dates[-2:])}"


def next_dose_dates(item: dict, count: int = 2) -> list[date]:
    current = item_dates(item)
    if not current or count <= 0:
        return []
    last = current[-1]
    rule = str(item.get("rule", ""))
    if rule == "指定日数おき":
        step = int(item.get("interval_days", 1)) + 1
        return [last + timedelta(days=step * index) for index in range(1, count + 1)]
    if rule == "曜日指定":
        selected = set(int(value) for value in item.get("weekdays", ()))
        result = []
        candidate = last + timedelta(days=1)
        while len(result) < count:
            if candidate.weekday() in selected:
                result.append(candidate)
            candidate += timedelta(days=1)
        return result
    if rule == "月1回":
        seed = dict(item)
        seed.pop("dose_dates", None)
        seed["first_date"] = last.isoformat()
        seed["count"] = count + 1
        return item_dates(seed)[1:]
    return []


def item_dates(item: dict) -> list[date]:
    if item.get("dose_dates"):
        return [date.fromisoformat(str(value)) for value in item["dose_dates"]]
    start = date.fromisoformat(str(item["first_date"]))
    return calculate_dose_dates(
        start,
        int(item["count"]),
        str(item["rule"]),
        int(item.get("interval_days", 1)),
        item.get("weekdays", ()),
    )


def merge_intermittent_extension(held: dict, incoming: dict) -> dict:
    """同一間欠薬の受付前在庫と今回受付分を、一続きの予定として統合する。"""
    base_fields = ("hospital", "drug")
    if any(str(held.get(key, "")).strip() != str(incoming.get(key, "")).strip() for key in base_fields):
        raise ValueError("異なる間欠薬は日数延長として統合できません。")
    schedule_fields = ("slot", "rule", "interval_days")
    same_schedule = all(held.get(key, 1) == incoming.get(key, 1) for key in schedule_fields)
    same_weekdays = tuple(sorted(held.get("weekdays", ()))) == tuple(sorted(incoming.get("weekdays", ())))
    if not same_schedule or not same_weekdays:
        raise ValueError(
            f"{held.get('hospital', '')} {held.get('drug', '')}の服用時点または服用規則が変わっています。"
            "同じ在庫へ統合せず、処理内容を確認してください。"
        )
    held_dates = item_dates(held)
    incoming_dates = item_dates(incoming)
    expected = next_dose_dates(held, 1)
    if not expected or incoming_dates[0] != expected[0]:
        relation = "重複" if incoming_dates[0] <= held_dates[-1] else "空白"
        raise ValueError(
            f"{held.get('hospital', '')} {held.get('drug', '')}の服用日に{relation}があります。"
            f"次回は{format_dates(expected)}、今回受付は{format_dates(incoming_dates[:1])}です。"
        )
    merged_dates = [*held_dates, *incoming_dates]
    merged = dict(held)
    merged.update({
        "first_date": merged_dates[0].isoformat(),
        "count": len(merged_dates),
        "dose_dates": [value.isoformat() for value in merged_dates],
        "extension": {
            "before_count": len(held_dates),
            "incoming_count": len(incoming_dates),
            "after_count": len(merged_dates),
        },
    })
    return merged


def build_intermittent_report(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["", "■間欠服用薬"]
    for item in items:
        dates = item_dates(item)
        detail = f"{item['hospital']}　{item['drug']}　{item['slot']}　{item['count']}包（{rule_label(item)}）"
        lines.extend((detail, f"服用日：{format_dates_compact(dates)}"))
        if item.get("note"):
            lines.append(f"備考：{item['note']}")
    return "\n".join(lines) + "\n"
