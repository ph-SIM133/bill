from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional


SLOTS = ("朝", "昼", "夕", "寝前")
INTERMITTENT_BOUNDARY_HOSPITAL = "__INTERMITTENT_BOUNDARY__"


class ValidationError(ValueError):
    """入力値が業務上の前提を満たさない場合。"""


@dataclass(frozen=True)
class MedicationPeriod:
    hospital: str
    start: date
    end: date
    start_slot: str = "朝"
    end_slot: str = "寝前"
    active_slots: tuple[str, ...] = SLOTS

    def validate(self) -> None:
        if not self.hospital.strip():
            raise ValidationError("医療機関名を入力してください。")
        if self.start_slot not in SLOTS or self.end_slot not in SLOTS:
            raise ValidationError(f"{self.hospital}の開始・終了時点を確認してください。")
        if not self.active_slots or any(slot not in SLOTS for slot in self.active_slots):
            raise ValidationError(f"{self.hospital}の服用時点を1つ以上選択してください。")
        if _point(self.end, self.end_slot) < _point(self.start, self.start_slot):
            raise ValidationError(f"{self.hospital}の終了日は開始日以降にしてください。")
        if _availability_bounds(self) is None:
            raise ValidationError(f"{self.hospital}の期間内に選択された服用時点がありません。")
        if self.days > 366:
            raise ValidationError(
                f"{self.hospital}の期間が{self.days}日あります。開始年・終了年を確認してください。"
            )

    @property
    def days(self) -> int:
        counts = _active_slot_counts(self)
        return max(counts.values(), default=0)


@dataclass(frozen=True)
class PackagingAssessment:
    current: MedicationPeriod
    incoming: MedicationPeriod
    proposed_start: date
    proposed_end: date
    additional_current: tuple[MedicationPeriod, ...] = ()
    additional_incoming: tuple[MedicationPeriod, ...] = ()
    proposed_start_slot: str = "朝"
    proposed_end_slot: str = "寝前"
    original_held: tuple[MedicationPeriod, ...] = ()
    inventory_additions: tuple[MedicationPeriod, ...] = ()

    @property
    def proposed_days(self) -> int:
        units = _point(self.proposed_end, self.proposed_end_slot) - _point(self.proposed_start, self.proposed_start_slot) + 1
        return (units + len(SLOTS) - 1) // len(SLOTS)

    @property
    def sources(self) -> tuple[MedicationPeriod, ...]:
        return (*self.held_sources, *self.incoming_sources)

    @property
    def held_sources(self) -> tuple[MedicationPeriod, ...]:
        return tuple(
            source
            for source in (self.current, *self.additional_current)
            if source.hospital != INTERMITTENT_BOUNDARY_HOSPITAL
        )

    @property
    def incoming_sources(self) -> tuple[MedicationPeriod, ...]:
        return tuple(
            source
            for source in (self.incoming, *self.additional_incoming)
            if source.hospital != INTERMITTENT_BOUNDARY_HOSPITAL
        )


@dataclass(frozen=True)
class PackagingResult:
    assessment: PackagingAssessment
    actual_start: date
    actual_end: date
    uncombined: tuple[MedicationPeriod, ...]
    actual_start_slot: str = "朝"
    actual_end_slot: str = "寝前"
    release_all: bool = False
    released_uncombined: tuple[MedicationPeriod, ...] = ()
    release_unmatched_slots: bool = False

    @property
    def actual_days(self) -> int:
        units = _point(self.actual_end, self.actual_end_slot) - _point(self.actual_start, self.actual_start_slot) + 1
        return (units + len(SLOTS) - 1) // len(SLOTS)


def parse_date(value: str, today: date | None = None) -> date:
    text = str(value).strip()
    if not text:
        raise ValidationError("開始日・終了日を入力してください。")
    base = today or date.today()
    normalized = text.replace(".", "/").replace("-", "/")
    parts = normalized.split("/")
    try:
        if len(parts) == 2:
            return date(base.year, int(parts[0]), int(parts[1]))
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise ValidationError(f"日付「{text}」を確認してください。") from exc
    raise ValidationError(f"日付「{text}」は YYYY-MM-DD または M/D で入力してください。")


def calculate_assessment(
    current: MedicationPeriod,
    incoming: MedicationPeriod,
    additional_current: tuple[MedicationPeriod, ...] = (),
    additional_incoming: tuple[MedicationPeriod, ...] = (),
    inventory_additions: tuple[MedicationPeriod, ...] = (),
) -> PackagingAssessment:
    original_held = (current, *additional_current)
    merged_held = list(original_held)
    for addition in inventory_additions:
        addition.validate()
        matches = [index for index, held in enumerate(merged_held) if held.hospital == addition.hospital]
        if len(matches) != 1:
            raise ValidationError(f"{addition.hospital}の積み増し元Sを確認してください。")
        index = matches[0]
        merged_held[index] = merge_medication_periods(merged_held[index], addition)
    current = merged_held[0]
    additional_current = tuple(merged_held[1:])
    sources = (current, *additional_current, incoming, *additional_incoming)
    for source in sources:
        source.validate()
    bounds = [_availability_bounds(source) for source in sources]
    assert all(value is not None for value in bounds)
    start_point = max(value[0] for value in bounds if value is not None)
    end_point = min(value[1] for value in bounds if value is not None)
    start, start_slot = _from_point(start_point)
    end, end_slot = _from_point(end_point)
    if end_point < start_point:
        raise ValidationError("入力した薬の期間が重ならないため、合包できません。")
    return PackagingAssessment(
        current=current,
        incoming=incoming,
        proposed_start=start,
        proposed_end=end,
        additional_current=additional_current,
        additional_incoming=additional_incoming,
        proposed_start_slot=start_slot,
        proposed_end_slot=end_slot,
        original_held=original_held,
        inventory_additions=inventory_additions,
    )


def confirm_packaging(
    assessment: PackagingAssessment,
    actual_start: date,
    actual_end: date,
    actual_start_slot: str = "朝",
    actual_end_slot: str = "寝前",
    release_all: bool = False,
    release_unmatched_slots: bool = False,
) -> PackagingResult:
    actual_start_point = _point(actual_start, actual_start_slot)
    actual_end_point = _point(actual_end, actual_end_slot)
    if actual_end_point < actual_start_point:
        raise ValidationError("実施終了日は実施開始日以降にしてください。")
    if (
        actual_start_point < _point(assessment.proposed_start, assessment.proposed_start_slot)
        or actual_end_point > _point(assessment.proposed_end, assessment.proposed_end_slot)
    ):
        raise ValidationError("実施期間は合包可能期間の範囲内で入力してください。")
    # 開始日は朝から処理対象とするが、終了日は指定された
    # 服用時点までとする。終了時点より後の薬包は将来分のため預かりに残す。
    processing_start_point = _point(actual_start, "朝")
    processing_end_point = actual_end_point
    uncombined: list[MedicationPeriod] = []
    if release_unmatched_slots and not release_all:
        _delivered, retained = _split_sources_releasing_unmatched(
            assessment.sources, processing_start_point, processing_end_point
        )
        uncombined.extend(retained)
    else:
        for source in assessment.sources:
            source_bounds = _availability_bounds(source)
            assert source_bounds is not None
            source_start, source_end = source_bounds
            if source_start < processing_start_point:
                remainder = _trim_period(source, source_start, processing_start_point - 1)
                if remainder is not None:
                    uncombined.append(remainder)
            if source_end > processing_end_point:
                remainder = _trim_period(source, processing_end_point + 1, source_end)
                if remainder is not None:
                    uncombined.append(remainder)
    remaining = tuple(uncombined)
    return PackagingResult(
        assessment,
        actual_start,
        actual_end,
        () if release_all else remaining,
        actual_start_slot,
        actual_end_slot,
        release_all,
        remaining if release_all else (),
        release_unmatched_slots,
    )


def delivered_periods(result: PackagingResult) -> tuple[MedicationPeriod, ...]:
    """実施日の範囲内で、実際に施設へ交付した薬包を返す。

    合包した服用時点だけでなく、同じ日範囲の単独薬も交付対象とする。
    """
    if result.release_all:
        return result.assessment.sources
    if result.release_unmatched_slots:
        delivered, _retained = _split_sources_releasing_unmatched(
            result.assessment.sources,
            _point(result.actual_start, "朝"),
            _point(result.actual_end, result.actual_end_slot),
        )
        return delivered
    return common_delivery_periods(result)


def _split_sources_releasing_unmatched(
    sources: tuple[MedicationPeriod, ...], low: int, high: int
) -> tuple[tuple[MedicationPeriod, ...], tuple[MedicationPeriod, ...]]:
    """合包時点はお渡し期間内、非共通時点は全期間を交付側へ分ける。"""
    bounds = [_availability_bounds(source) for source in sources]
    shared_slots = {
        slot for slot in SLOTS
        if sum(1 for source in sources if slot in source.active_slots) >= 2
    }
    delivered = []
    retained = []
    for source, source_bounds in zip(sources, bounds):
        assert source_bounds is not None
        source_points = {
            point for point in range(source_bounds[0], source_bounds[1] + 1)
            if _from_point(point)[1] in source.active_slots
        }
        combined_points = {
            point for point in source_points
            if low <= point <= high and _from_point(point)[1] in shared_slots
        }
        single_points = {
            point for point in source_points if _from_point(point)[1] not in shared_slots
        }
        # Keep these as distinct records even when their date ranges match.
        # The external sheet must show the packaged and individually supplied
        # portions on separate rows.
        delivered.extend(_periods_from_points(source, combined_points))
        delivered.extend(_periods_from_points(source, single_points))
        retained.extend(_periods_from_points(source, source_points - combined_points - single_points))
    return tuple(delivered), tuple(retained)


def _periods_from_points(source: MedicationPeriod, points: set[int]) -> tuple[MedicationPeriod, ...]:
    """飛び飛びの包を、日付範囲と服用時点が真に連続する期間へ分割する。"""
    if not points:
        return ()
    runs = []
    for slot in SLOTS:
        dates = sorted(_from_point(point)[0] for point in points if _from_point(point)[1] == slot)
        if not dates:
            continue
        run_start = previous = dates[0]
        for value in dates[1:]:
            if value.toordinal() != previous.toordinal() + 1:
                runs.append((run_start, previous, slot))
                run_start = value
            previous = value
        runs.append((run_start, previous, slot))
    grouped: dict[tuple[date, date], list[str]] = {}
    for start, end, slot in runs:
        grouped.setdefault((start, end), []).append(slot)
    result = []
    for (start, end), slots in sorted(grouped.items(), key=lambda item: (item[0][0], SLOTS.index(item[1][0]))):
        ordered = tuple(slot for slot in SLOTS if slot in slots)
        result.append(MedicationPeriod(source.hospital, start, end, ordered[0], ordered[-1], ordered))
    return tuple(result)


def common_delivery_periods(result: PackagingResult) -> tuple[MedicationPeriod, ...]:
    """合包期間内で今回交付する連続薬を返す。"""
    low = _point(result.actual_start, "朝")
    high = _point(result.actual_end, result.actual_end_slot)
    values = []
    for source in result.assessment.sources:
        source_bounds = _availability_bounds(source)
        assert source_bounds is not None
        delivered = _trim_period(source, max(low, source_bounds[0]), min(high, source_bounds[1]))
        if delivered is not None:
            values.append(delivered)
    return tuple(values)


def delivery_details(result: PackagingResult) -> tuple[dict, ...]:
    """交付薬包を「合包して交付」と「単独で交付」に分ける。"""
    sources = result.assessment.sources
    bounds = [_availability_bounds(source) for source in sources]
    if result.release_all:
        low = min(value[0] for value in bounds if value is not None)
        high = max(value[1] for value in bounds if value is not None)
    else:
        low = _point(result.actual_start, "朝")
        high = _point(result.actual_end, result.actual_end_slot)
    combined = [{slot: 0 for slot in SLOTS} for _source in sources]
    single = [{slot: 0 for slot in SLOTS} for _source in sources]
    for point in range(low, high + 1):
        slot = _from_point(point)[1]
        participants = [
            index
            for index, (source, source_bounds) in enumerate(zip(sources, bounds))
            if source_bounds is not None
            and source_bounds[0] <= point <= source_bounds[1]
            and slot in source.active_slots
        ]
        target = combined if len(participants) >= 2 else single
        for index in participants:
            target[index][slot] += 1
    return tuple(
        {
            "hospital": source.hospital,
            "combined_counts": combined[index],
            "single_counts": single[index],
        }
        for index, source in enumerate(sources)
    )


def audit_result_balance(result: PackagingResult) -> tuple[str, ...]:
    """S+Oと、お渡し分+預かり分の服用時点別包数を照合する。"""
    def totals(periods: tuple[MedicationPeriod, ...]) -> dict[tuple[str, str], int]:
        values: dict[tuple[str, str], int] = {}
        for period in periods:
            for slot, count in period_slot_counts(period).items():
                if count is not None:
                    key = (period.hospital, slot)
                    values[key] = values.get(key, 0) + count
        return values

    sources = result.assessment.sources
    delivered = delivered_periods(result)
    expected = totals(sources)
    actual = totals((*delivered, *result.uncombined))
    issues = []
    for hospital, slot in sorted(set(expected) | set(actual)):
        before = expected.get((hospital, slot), 0)
        after = actual.get((hospital, slot), 0)
        if before != after:
            issues.append(f"{hospital} {slot}：受付総数{before}包に対し、お渡し＋預かりが{after}包です。")
    signatures = [
        (item.hospital, item.start, item.start_slot, item.end, item.end_slot, item.active_slots)
        for item in sources
    ]
    if len(signatures) != len(set(signatures)):
        issues.append("同一医療機関・同一期間・同一服用時点の薬が重複しています。")
    delivery_start = _point(result.actual_start, "朝")
    for item in result.uncombined:
        bounds = _availability_bounds(item)
        if bounds is not None and bounds[1] < delivery_start:
            issues.append(
                f"{item.hospital}：今回お渡し期間より前の薬が預かり分に残ります"
                f"（{_remainder_text(item)}）。"
            )
    return tuple(issues)


def build_period_report(result: PackagingResult, patient: str, facility: str, service_date: str) -> str:
    if not patient.strip():
        raise ValidationError("対象者を入力してください。")
    lines = [f"{patient.strip()}　【外来服薬支援】　実施日 {service_date}"]
    if facility.strip():
        lines.append(f"施設：{facility.strip()}")
    held = tuple(
        source
        for source in (result.assessment.original_held or result.assessment.held_sources)
        if source.hospital != INTERMITTENT_BOUNDARY_HOSPITAL
    )
    incoming_sources = result.assessment.incoming_sources

    lines.extend(["", "★合包待ちの薬"])
    for index, source in enumerate(held):
        label = chr(ord("A") + index)
        source_period = _report_period(source.start, source.end, source.start_slot, source.end_slot)
        lines.append(f"{label}. {source.hospital}　{source_period}　{source.days}日分{_slot_summary(source)}")

    lines.extend(["", "★今回の処方薬"])
    for offset, incoming in enumerate(incoming_sources, start=len(held)):
        incoming_label = chr(ord("A") + offset)
        incoming_period = _report_period(incoming.start, incoming.end, incoming.start_slot, incoming.end_slot)
        lines.append(f"{incoming_label}. {incoming.hospital}　{incoming_period}　{incoming.days}日分{_slot_summary(incoming)}")
    for addition in result.assessment.inventory_additions:
        addition_period = _report_period(addition.start, addition.end, addition.start_slot, addition.end_slot)
        lines.append(
            f"・ {addition.hospital}　{addition_period}　{addition.days}日分{_slot_summary(addition)}"
            "（日数延長・今回合包なし）"
        )
    delivery_heading = "★今回お渡し分（すべて払い出し）" if result.release_all else "★今回お渡し分"
    period_label = "合包期間" if result.release_all else "お渡し期間"
    lines.extend([
        "",
        delivery_heading,
        f"{period_label}：{_md(result.actual_start)}{result.actual_start_slot}～{_md(result.actual_end)}{result.actual_end_slot}",
    ])
    for index, packaged in enumerate(delivered_periods(result)):
        label = chr(ord("A") + index)
        period = _report_period(packaged.start, packaged.end, packaged.start_slot, packaged.end_slot)
        lines.append(f"{label}. {packaged.hospital}　{period}　{packaged.days}日分{_slot_summary(packaged)}")
    lines.extend(["", "★薬局預かり分"])
    if not result.uncombined:
        lines.append("なし")
    else:
        for item in result.uncombined:
            lines.append(f"{item.hospital}　{_remainder_text(item)}　→ 次回へ引継ぎ")
    next_creations = predict_next_creation_details(result)
    if next_creations:
        lines.extend([
            "",
            "★次の処方待ち",
        ])
        for hospital, start, start_slot in next_creations:
            lines.append(f"{hospital}　{_md(start)}{start_slot}から　→ 次回へ引継ぎ")
    return "\n".join(lines)


def period_result_to_dict(result: PackagingResult, patient: str, facility: str, service_date: str) -> dict:
    held_sources = result.assessment.held_sources
    incoming_sources = result.assessment.incoming_sources
    data = {
        "version": 2,
        "workflow": "SOAP",
        "patient": patient.strip(),
        "facility": facility.strip(),
        "service_date": service_date,
        "current": _period_dict(held_sources[0]) if held_sources else {},
        "incoming": _period_dict(incoming_sources[0]) if incoming_sources else {},
        "held": [_period_dict(item) for item in held_sources],
        "incoming_items": [_period_dict(item) for item in incoming_sources],
        "inventory_additions": [_period_dict(item) for item in result.assessment.inventory_additions],
        "assessment": {
            "start": result.assessment.proposed_start.isoformat(),
            "end": result.assessment.proposed_end.isoformat(),
            "days": result.assessment.proposed_days,
        },
        "result": {
            "start": result.actual_start.isoformat(),
            "end": result.actual_end.isoformat(),
            "days": result.actual_days,
            "start_slot": result.actual_start_slot,
            "end_slot": result.actual_end_slot,
            "uncombined": [_period_dict(item) for item in result.uncombined],
            "delivered": [_period_dict(item) for item in delivered_periods(result)],
            "delivery_details": list(delivery_details(result)),
            "delivery_mode": "すべて払い出し" if result.release_all else "薬局保管",
            "unmatched_slot_mode": "重ならない服用時点は合包範囲外とし、すべて渡す" if result.release_unmatched_slots else "お渡し期間に合わせて交付し、残りの薬は薬局保管",
            "released_uncombined": [_period_dict(item) for item in result.released_uncombined],
        },
    }
    # A zero balance still needs a management card, including when all courses
    # finish together. Keep the full dosing schedule on that waiting card.
    remaining_hospitals = {item.hospital for item in result.uncombined}
    exhausted = [source for source in result.assessment.sources
                 if source.hospital not in remaining_hospitals]
    next_creations = tuple(_next_after_source(source) for source in exhausted)
    if next_creations:
        data["next_creations"] = [
            {"hospital": hospital, "start": start.isoformat(), "start_slot": start_slot,
             "active_slots": list(source.active_slots)}
            for source, (hospital, start, start_slot) in zip(exhausted, next_creations)
        ]
        data["next_creation"] = data["next_creations"][0]
    if result.release_all:
        data["post_delivery_states"] = [
            {
                "hospital": source.hospital,
                "status": "全量交付済み・薬局預かりなし",
                "delivered_through": source.end.isoformat(),
                "delivered_through_slot": source.end_slot,
                "next_start": start.isoformat(),
                "next_start_slot": start_slot,
                "active_slots": list(source.active_slots),
            }
            for source, (_hospital, start, start_slot) in zip(result.assessment.sources, next_creations)
        ]
    return data


def predict_next_creation(result: PackagingResult) -> tuple[str, date] | None:
    """互換用：次回作成予想の先頭を返す。"""
    values = predict_next_creations(result)
    return values[0] if values else None


def predict_next_creations(result: PackagingResult) -> tuple[tuple[str, date], ...]:
    """最初に期間が尽きる医療機関ごとの次回開始予想を返す。"""
    return tuple((hospital, start) for hospital, start, _slot in predict_next_creation_details(result))


def predict_next_creation_details(result: PackagingResult) -> tuple[tuple[str, date, str], ...]:
    """最初に期間が尽きる医療機関と、その次に必要な服用時点を返す。"""
    sources = result.assessment.sources
    if not sources:
        return ()
    if result.release_all:
        return tuple(_next_after_source(source) for source in sources)
    source_ends = [(source, _availability_bounds(source)[1]) for source in sources]
    earliest_end = min(end for _source, end in source_ends)
    if all(end == earliest_end for _source, end in source_ends):
        return ()
    values = []
    for source, end in source_ends:
        if end != earliest_end:
            continue
        next_point = end + 1
        while _from_point(next_point)[1] not in source.active_slots:
            next_point += 1
        start, start_slot = _from_point(next_point)
        values.append((source.hospital, start, start_slot))
    return tuple(values)


def _next_after_source(source: MedicationPeriod) -> tuple[str, date, str]:
    bounds = _availability_bounds(source)
    assert bounds is not None
    next_point = bounds[1] + 1
    while _from_point(next_point)[1] not in source.active_slots:
        next_point += 1
    start, start_slot = _from_point(next_point)
    return source.hospital, start, start_slot


def counterpart_hospitals(record: dict, carryover_hospital: str) -> tuple[str, ...]:
    """前回の2医療機関から、未実施薬の相手方を抽出する。"""
    names: list[str] = []
    for key in ("current", "incoming"):
        value = record.get(key, {})
        if not isinstance(value, dict):
            continue
        name = str(value.get("hospital", "")).strip()
        if name and name != carryover_hospital and name not in names:
            names.append(name)
    return tuple(names)


def next_creation_candidates(record: dict) -> tuple[dict, ...]:
    """新旧の保存形式から、前回の次回作成予想を取り出す。"""
    values = record.get("next_creations")
    if isinstance(values, list):
        candidates = [dict(value) for value in values if isinstance(value, dict)]
    else:
        value = record.get("next_creation")
        candidates = [dict(value)] if isinstance(value, dict) else []
    sources = [*record.get("held", []), *record.get("incoming_items", []),
               *record.get("post_delivery_states", [])]
    sources.extend(record.get(key, {}) for key in ("current", "incoming"))
    for candidate in candidates:
        if "active_slots" not in candidate:
            slots = next((item["active_slots"] for item in sources
                          if isinstance(item, dict) and item.get("hospital") == candidate.get("hospital")
                          and item.get("active_slots")), None)
            if slots is not None:
                candidate["active_slots"] = list(slots)
    return tuple(candidates)


def _period_dict(value: MedicationPeriod) -> dict:
    return {
        "hospital": value.hospital,
        "start": value.start.isoformat(),
        "end": value.end.isoformat(),
        "start_slot": value.start_slot,
        "end_slot": value.end_slot,
        "active_slots": list(value.active_slots),
        "days": value.days,
    }


def _md(value: date) -> str:
    return f"{value.month}/{value.day}"


def _report_period(start: date, end: date, start_slot: str = "朝", end_slot: str = "寝前") -> str:
    if start.year == end.year:
        return f"{_md(start)}{start_slot}～{_md(end)}{end_slot}"
    return f"{start.year}/{start.month}/{start.day}{start_slot}～{end.year}/{end.month}/{end.day}{end_slot}"


def _point(value: date, slot: str) -> int:
    return value.toordinal() * len(SLOTS) + SLOTS.index(slot)


def _from_point(value: int) -> tuple[date, str]:
    ordinal, slot_index = divmod(value, len(SLOTS))
    return date.fromordinal(ordinal), SLOTS[slot_index]


def _trim_period(source: MedicationPeriod, low: int, high: int) -> MedicationPeriod | None:
    """指定範囲内に実在する服用時点だけを残した期間を返す。"""
    first = next((value for value in range(low, high + 1) if _from_point(value)[1] in source.active_slots), None)
    last = next((value for value in range(high, low - 1, -1) if _from_point(value)[1] in source.active_slots), None)
    if first is None or last is None:
        return None
    start, start_slot = _from_point(first)
    end, end_slot = _from_point(last)
    return MedicationPeriod(source.hospital, start, end, start_slot, end_slot, source.active_slots)


def remaining_outside_interval(
    source: MedicationPeriod,
    applied_start: date,
    applied_end: date,
    applied_start_slot: str,
    applied_end_slot: str,
) -> tuple[MedicationPeriod, ...]:
    """臨時追加に使用した範囲の前後へ残る薬を返す。"""
    source.validate()
    bounds = _availability_bounds(source)
    assert bounds is not None
    low = _point(applied_start, applied_start_slot)
    high = _point(applied_end, applied_end_slot)
    if high < low or low < bounds[0] or high > bounds[1]:
        raise ValidationError("臨時追加の実施期間は今回処方の範囲内で入力してください。")
    result = []
    before = _trim_period(source, bounds[0], low - 1)
    after = _trim_period(source, high + 1, bounds[1])
    if before is not None:
        result.append(before)
    if after is not None:
        result.append(after)
    return tuple(result)


def _availability_bounds(source: MedicationPeriod) -> tuple[int, int] | None:
    low = _point(source.start, source.start_slot)
    high = _point(source.end, source.end_slot)
    first = next((value for value in range(low, high + 1) if _from_point(value)[1] in source.active_slots), None)
    last = next((value for value in range(high, low - 1, -1) if _from_point(value)[1] in source.active_slots), None)
    if first is None or last is None:
        return None
    return first, last


def _slot_summary(value: MedicationPeriod) -> str:
    ordered = [slot for slot in SLOTS if slot in value.active_slots]
    short = "".join("寝" if slot == "寝前" else slot for slot in ordered)
    return f"（{short}）"


def _remainder_text(value: MedicationPeriod) -> str:
    if value.start == value.end:
        start_index = SLOTS.index(value.start_slot)
        end_index = SLOTS.index(value.end_slot)
        slots = [slot for slot in SLOTS[start_index : end_index + 1] if slot in value.active_slots]
        if slots == list(SLOTS):
            period = _report_period(value.start, value.end, value.start_slot, value.end_slot)
            return f"{period}　1日分"
        if slots:
            return f"{_md(value.start)} {'・'.join(slots)}　{len(slots)}包"
    period = _report_period(value.start, value.end, value.start_slot, value.end_slot)
    return f"{period}　{value.days}日分{_slot_summary(value)}"


def _active_slot_counts(value: MedicationPeriod) -> dict[str, int]:
    counts = {slot: 0 for slot in value.active_slots}
    low = _point(value.start, value.start_slot)
    high = _point(value.end, value.end_slot)
    for point in range(low, high + 1):
        slot = _from_point(point)[1]
        if slot in counts:
            counts[slot] += 1
    return counts


def period_slot_counts(value: MedicationPeriod) -> dict[str, Optional[int]]:
    """期間が表す保持包数を4服用時点すべてについて返す。"""
    active = _active_slot_counts(value)
    return {slot: active.get(slot) if slot in value.active_slots else None for slot in SLOTS}


def continuous_period_from_counts(
    hospital: str,
    start: date,
    start_slot: str,
    active_slots: tuple[str, ...],
    counts: dict[str, int],
) -> Optional[MedicationPeriod]:
    """服用時点別包数が開始点から一続きの期間なら、その期間を返す。"""
    targets = {slot: counts.get(slot, 0) for slot in active_slots}
    if not targets or any(value < 0 for value in targets.values()) or not any(targets.values()):
        return None
    current = {slot: 0 for slot in active_slots}
    point = _point(start, start_slot)
    while _from_point(point)[1] not in active_slots:
        point += 1
    actual_start_point = point
    actual_start, actual_start_slot = _from_point(actual_start_point)
    limit = point + (sum(targets.values()) + 2) * len(SLOTS)
    while point <= limit:
        slot = _from_point(point)[1]
        if slot in current:
            current[slot] += 1
            if current[slot] > targets[slot]:
                return None
            if current == targets:
                end, end_slot = _from_point(point)
                return MedicationPeriod(hospital, actual_start, end, actual_start_slot, end_slot, active_slots)
        point += 1
    return None


def merge_medication_periods(held: MedicationPeriod, addition: MedicationPeriod) -> MedicationPeriod:
    """同一医療機関の連続・重複する処方期間を在庫として統合する。"""
    if held.hospital != addition.hospital:
        raise ValidationError("異なる医療機関の薬は日数延長として統合できません。")
    active_slots = tuple(slot for slot in SLOTS if slot in set(held.active_slots) | set(addition.active_slots))
    held_bounds = _availability_bounds(held)
    addition_bounds = _availability_bounds(addition)
    assert held_bounds is not None and addition_bounds is not None
    earlier, later = (held, addition) if held_bounds[0] <= addition_bounds[0] else (addition, held)
    earlier_bounds = _availability_bounds(earlier)
    later_bounds = _availability_bounds(later)
    assert earlier_bounds is not None and later_bounds is not None
    next_expected = earlier_bounds[1] + 1
    while _from_point(next_expected)[1] not in active_slots:
        next_expected += 1
    if later_bounds[0] > next_expected:
        gap_start, gap_slot = _from_point(next_expected)
        raise ValidationError(
            f"{held.hospital}の積み増し期間に空白があります。{_md(gap_start)}{gap_slot}からを確認してください。"
        )
    start_point = min(held_bounds[0], addition_bounds[0])
    end_point = max(held_bounds[1], addition_bounds[1])
    start, start_slot = _from_point(start_point)
    end, end_slot = _from_point(end_point)
    return MedicationPeriod(held.hospital, start, end, start_slot, end_slot, active_slots)


def build_inventory_update(
    held_periods: tuple[MedicationPeriod, ...],
    additions: tuple[MedicationPeriod, ...],
    patient: str,
    facility: str,
    service_date: str,
) -> tuple[dict, str]:
    """合包を伴わない在庫積み増しの記録と報告を作る。"""
    if not patient.strip():
        raise ValidationError("対象者を入力してください。")
    if not additions:
        raise ValidationError("日数延長にする今回処方を入力してください。")
    merged = list(held_periods)
    changes: list[tuple[MedicationPeriod, MedicationPeriod, MedicationPeriod]] = []
    for addition in additions:
        matches = [index for index, held in enumerate(merged) if held.hospital == addition.hospital]
        if len(matches) != 1:
            raise ValidationError(f"{addition.hospital}の積み増し元Sを確認してください。")
        index = matches[0]
        before = merged[index]
        after = merge_medication_periods(before, addition)
        merged[index] = after
        changes.append((before, addition, after))

    lines = [f"{patient.strip()}　【外来服薬支援】　実施日 {service_date}"]
    if facility.strip():
        lines.append(f"施設：{facility.strip()}")
    lines.extend(["", "★日数を延長しました"])
    for before, addition, after in changes:
        lines.extend(
            [
                f"■ {after.hospital}",
                f"受付前　　　{_period_line(before)}",
                f"今回受付　　{_period_line(addition)}",
                f"受付後　　　{_period_line(after)}",
            ]
        )
    lines.extend(["", "今回は合包を実施していません。", "", "★次回へ引き継ぐ薬局預かり"])
    for item in merged:
        lines.append(f"{item.hospital}　{_period_line(item)}　→ 次回へ引継ぎ")

    data = {
        "version": 2,
        "workflow": "SOAP",
        "operation": "inventory_update",
        "patient": patient.strip(),
        "facility": facility.strip(),
        "service_date": service_date,
        "inventory_additions": [_period_dict(item) for item in additions],
        "inventory_changes": [
            {
                "before": _period_dict(before),
                "incoming": _period_dict(addition),
                "after": _period_dict(after),
            }
            for before, addition, after in changes
        ],
        "result": {"uncombined": [_period_dict(item) for item in merged]},
    }
    return data, "\n".join(lines)


def _period_line(value: MedicationPeriod) -> str:
    period = _report_period(value.start, value.end, value.start_slot, value.end_slot)
    return f"{period}　{value.days}日分{_slot_summary(value)}"


@dataclass
class MedicationRow:
    hospital: str
    start: str = ""
    end: str = ""
    counts: dict[str, Optional[int]] = field(default_factory=lambda: {slot: None for slot in SLOTS})
    use_for_packaging: bool = True

    def validate(self) -> None:
        if not self.hospital.strip():
            raise ValidationError("医療機関名を入力してください。")
        for slot in SLOTS:
            value = self.counts.get(slot)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValidationError(f"{self.hospital}・{slot}の包数は0以上で入力してください。")


@dataclass
class SupportRecord:
    patient: str
    facility: str
    service_date: str = field(default_factory=lambda: date.today().isoformat())
    rows: list[MedicationRow] = field(default_factory=list)
    packaged: dict[str, int] = field(default_factory=lambda: {slot: 0 for slot in SLOTS})
    note: str = ""

    def validate(self) -> None:
        if not self.patient.strip():
            raise ValidationError("対象者を入力してください。")
        if not self.rows:
            raise ValidationError("医療機関を1件以上入力してください。")
        for row in self.rows:
            row.validate()
        for slot in SLOTS:
            amount = self.packaged.get(slot, 0)
            if not isinstance(amount, int) or amount < 0:
                raise ValidationError(f"{slot}の合包数は0以上で入力してください。")
            if amount == 0:
                continue
            participants = [
                row for row in self.rows
                if row.use_for_packaging and row.counts.get(slot) is not None
            ]
            if len(participants) < 2:
                raise ValidationError(f"{slot}は合包に参加する医療機関が2科未満です。")
            shortages = [row.hospital for row in participants if (row.counts.get(slot) or 0) < amount]
            if shortages:
                raise ValidationError(
                    f"{slot}の合包数が所持包数を超えています：{'、'.join(shortages)}"
                )

    def remaining(self) -> list[MedicationRow]:
        self.validate()
        result: list[MedicationRow] = []
        for row in self.rows:
            counts: dict[str, Optional[int]] = {}
            for slot in SLOTS:
                owned = row.counts.get(slot)
                if owned is None:
                    counts[slot] = None
                elif row.use_for_packaging:
                    counts[slot] = owned - self.packaged.get(slot, 0)
                else:
                    counts[slot] = owned
            result.append(
                MedicationRow(
                    hospital=row.hospital,
                    start=row.start,
                    end=row.end,
                    counts=counts,
                    use_for_packaging=row.use_for_packaging,
                )
            )
        return result

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)


def parse_count(value: str) -> Optional[int]:
    text = str(value).strip()
    if text == "-":
        return None
    if text == "":
        raise ValidationError("包数は数値または「-」で入力してください。")
    try:
        amount = int(text)
    except ValueError as exc:
        raise ValidationError(f"包数「{text}」は数値ではありません。") from exc
    if amount < 0:
        raise ValidationError("包数は0以上で入力してください。")
    return amount


def format_count(value: Optional[int]) -> str:
    return "-" if value is None else str(value)


def build_report(record: SupportRecord) -> str:
    remaining = record.remaining()
    lines = [
        f"{record.patient}　【外来服薬支援】　実施日 {record.service_date}",
    ]
    if record.facility.strip():
        lines.append(f"施設：{record.facility.strip()}")
    lines.extend(["", "★所持薬（単位：包）", _header_line()])
    for row in record.rows:
        lines.append(_row_line(row))
    lines.extend(["", "★合包化実施分（単位：回）", _counts_line(record.packaged)])
    lines.extend(["", "★未実施薬（薬局預かり・単位：包）", _header_line()])
    for row in remaining:
        # 初版では寝前から翌朝へまたぐ服用日時点を自動計算しない。
        # 元の期間を残数の期間として誤表示しないよう、未実施欄は包数だけを示す。
        lines.append(_row_line(row, include_period=False))
    participants = [row.hospital for row in record.rows if row.use_for_packaging]
    lines.extend(["", "★今回の実施報告"])
    if any(record.packaged.values()):
        lines.append(f"確認医療機関：{'、'.join(participants)}")
        performed = "／".join(f"{slot}{record.packaged[slot]}回" for slot in SLOTS if record.packaged[slot])
        lines.append(f"合包実施：{performed}")
    else:
        lines.append("合包未実施：今回確認した薬は薬局で預かっています。")
    if record.note.strip():
        lines.extend(["", f"連絡事項：{record.note.strip()}"])
    return "\n".join(lines)


def _header_line() -> str:
    return f"{'医療機関・期間':<24}{'朝':>6}{'昼':>6}{'夕':>6}{'寝前':>6}"


def _counts_line(counts: dict[str, int]) -> str:
    return f"{'':<24}" + "".join(f"{counts.get(slot, 0):>6}" for slot in SLOTS)


def _row_line(row: MedicationRow, include_period: bool = True) -> str:
    period = ""
    if include_period and (row.start or row.end):
        period = f" {row.start or '?'}～{row.end or ''}"
    label = f"{row.hospital}{period}"
    values = "".join(f"{format_count(row.counts.get(slot)):>6}" for slot in SLOTS)
    return f"{label:<24}{values}"
