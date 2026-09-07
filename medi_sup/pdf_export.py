from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm


PAGE_WIDTH = 90 * mm
MARGIN_X = 4 * mm
MARGIN_Y = 5 * mm
FONT_NAME = "MediSupJapanese"
FONT_BOLD = "MediSupJapaneseBold"
MAX_DRIVER_PRINTABLE_HEIGHT_MM = 196.765


def estimate_90mm_height(
    text: str,
    table_data: dict | None = None,
) -> float:
    """Return the generated page height in millimetres without creating a PDF."""
    _register_fonts()
    printable_text = _header_and_tail(text) if table_data else text
    table_height = _table_height(table_data) if table_data else 0.0
    row_count = 0
    for source_line in printable_text.rstrip().splitlines():
        bold = source_line.startswith("★") or source_line.startswith("■")
        row_count += len(_wrap(
            source_line,
            PAGE_WIDTH - 2 * MARGIN_X,
            8.2 if not bold else 9.2,
            FONT_BOLD if bold else FONT_NAME,
        )) or 1
    page_height = max(65 * mm, 2 * MARGIN_Y + row_count * 4.3 * mm + table_height + 7 * mm)
    return page_height / mm


def export_90mm_report(
    text: str,
    output_path: str | Path,
    table_data: dict | None = None,
    pharmacy_name: str = "",
) -> Path:
    """Create a compact, variable-height 90 mm report for a roll printer."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _register_fonts()

    printable_text = text
    table_height = 0.0
    if table_data:
        printable_text = _header_and_tail(text)
        table_height = _table_height(table_data)
    rows: list[tuple[str, bool]] = []
    source_lines = printable_text.rstrip().splitlines()
    header_row_count = 0
    for source_index, source_line in enumerate(source_lines):
        bold = source_line.startswith("★") or source_line.startswith("■")
        wrapped = _wrap(
            source_line,
            PAGE_WIDTH - 2 * MARGIN_X,
            8.2 if not bold else 9.2,
            FONT_BOLD if bold else FONT_NAME,
        )
        wrapped = wrapped or [""]
        rows.extend((line, bold) for line in wrapped)
        if source_index == 0:
            header_row_count = len(wrapped)

    line_height = 4.3 * mm
    page_height = max(65 * mm, 2 * MARGIN_Y + len(rows) * line_height + table_height + 7 * mm)
    temporary = output.with_suffix(".pdf.tmp")
    document = canvas.Canvas(str(temporary), pagesize=(PAGE_WIDTH, page_height), pageCompression=1)
    document.setTitle("外来服薬支援 実施報告")
    y = page_height - MARGIN_Y
    table_drawn = False
    for row_index, (line, heading) in enumerate(rows):
        font_size = 9.2 if heading else 8.2
        document.setFont(FONT_BOLD if heading else FONT_NAME, font_size)
        document.drawString(MARGIN_X, y, line)
        y -= line_height
        if table_data and not table_drawn and row_index + 1 >= header_row_count:
            y -= 0.8 * mm
            y = _draw_tables(document, y, table_data)
            table_drawn = True
    document.setLineWidth(0.3)
    document.line(MARGIN_X, 3.5 * mm, PAGE_WIDTH - MARGIN_X, 3.5 * mm)
    document.setFont(FONT_NAME, 6.5)
    footer = f"{pharmacy_name.strip()}　外来服薬支援" if pharmacy_name.strip() else "外来服薬支援"
    document.drawRightString(PAGE_WIDTH - MARGIN_X, 1.3 * mm, footer)
    try:
        document.save()
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _header_and_tail(text: str) -> str:
    lines = text.rstrip().splitlines()
    tail_start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("★薬局コメント") or line.startswith("■薬局コメント") or line.startswith("★受診日情報") or line.startswith("★臨時追加実施") or line.startswith("★処方変更・残薬確認")
        ),
        None,
    )
    if tail_start is None:
        return "\n".join(lines[:2])
    # Table output already represents S/O/P. Keep only the patient header here;
    # otherwise the internal text report is printed a second time below the table.
    header = [next((line for line in lines if line.strip()), "")]
    return "\n".join((*header, *lines[tail_start:]))


def _table_height(data: dict) -> float:
    source_height = sum(_period_row_height(row) for row in data.get("sources", ())) / mm
    remainder_height = sum(_period_row_height(row) for row in data.get("uncombined", ())) / mm
    # Includes the compact title-to-table spacing and the deliberate separation
    # before the result row and before the lower (unexecuted) table.
    intermittent_height = sum(
        _intermittent_height(data.get(key, ()), titled=False)
        for key in ("intermittent_all", "intermittent", "intermittent_remaining")
    )
    lower_height = (11 + remainder_height) if remainder_height else 0
    delivery_title = 4.2 if data.get("executed") else 0
    delivery_height = sum(
        _period_row_height(row, delivery=True)
        for row in ((data.get("executed"),) if data.get("executed") else ())
        + tuple(data.get("executed_extra", ()))
    ) / mm
    return (12 + source_height + lower_height + delivery_title + delivery_height) * mm + intermittent_height


def _draw_tables(document: canvas.Canvas, y: float, data: dict) -> float:
    source_title = "■所持薬（単位：包）"
    y = _draw_table(document, y, source_title, data.get("sources", ()), None)
    if data.get("intermittent_all"):
        y -= 1.5 * mm
        y = _draw_intermittent_table(document, y, data["intermittent_all"])
    if data.get("executed"):
        y -= 4.2 * mm
        document.setFont(FONT_BOLD, 9.2)
        document.drawString(MARGIN_X, y, "■今回お渡し分")
        y -= 3.4 * mm
        y = _draw_delivery_summary(document, y, data["executed"])
        for summary in data.get("executed_extra", ()):
            y = _draw_delivery_summary(document, y, summary)
    if data.get("intermittent"):
        y -= 1.5 * mm
        y = _draw_intermittent_table(document, y, data["intermittent"])
    # Separate the two table groups.  The following title itself is kept tight
    # against its table so it visually belongs to the lower table.
    if data.get("uncombined"):
        y -= 4.2 * mm
        y = _draw_table(document, y, "■薬局預かり分（単位：包）", data.get("uncombined", ()), None)
    if data.get("intermittent_remaining"):
        y -= 4.2 * mm
        y = _draw_intermittent_table(document, y, data["intermittent_remaining"])
    # Keep the pharmacy comment directly below the final table.
    return y - 4.0 * mm


def _intermittent_height(items, titled: bool = True) -> float:
    if not items:
        return 0.0
    height = (8.3 if titled else 1.5) * mm
    available = PAGE_WIDTH - 2 * MARGIN_X - 2.4 * mm
    for item in items:
        title_lines = _wrap(str(item.get("title", "")), available, 7.2, FONT_NAME)
        date_lines = _wrap("服用日：" + str(item.get("dates", "")), available, 7.2, FONT_NAME)
        height += max(10 * mm, (len(title_lines) + len(date_lines)) * 3.5 * mm + 2.5 * mm)
    return height


def _draw_delivery_summary(document: canvas.Canvas, y: float, summary: dict) -> float:
    left = MARGIN_X
    widths = (38 * mm, 11.5 * mm, 11.5 * mm, 11.5 * mm, 11.5 * mm)
    cells = (
        f"{summary.get('hospital', '今回お渡し分')}（{summary.get('symbols', '')}）\n{summary['period']}",
        _count_cell(summary, "朝"), _count_cell(summary, "昼"),
        _count_cell(summary, "夕"), _count_cell(summary, "寝前"),
    )
    height = _period_row_height(summary, delivery=True)
    _draw_grid_row(document, left, y, widths, height, cells, False, fill=True)
    return y - height


def _draw_intermittent_table(document: canvas.Canvas, y: float, items, title: str = "") -> float:
    left = MARGIN_X
    width = PAGE_WIDTH - 2 * MARGIN_X
    if title:
        document.setFont(FONT_BOLD, 9.2)
        document.drawString(left, y, title)
        y -= 4.2 * mm
    available = width - 2.4 * mm
    for item in items:
        title_lines = _wrap(str(item.get("title", "")), available, 7.2, FONT_NAME)
        date_lines = _wrap("服用日：" + str(item.get("dates", "")), available, 7.2, FONT_NAME)
        lines = title_lines + date_lines
        height = max(10 * mm, len(lines) * 3.5 * mm + 2.5 * mm)
        document.setLineWidth(0.35)
        document.rect(left, y - height, width, height, fill=0, stroke=1)
        document.setFont(FONT_NAME, 7.2)
        baseline = y - 3.4 * mm
        for line in lines:
            document.drawString(left + 1.2 * mm, baseline, line)
            baseline -= 3.5 * mm
        y -= height
    return y


def _draw_table(document: canvas.Canvas, y: float, title: str, rows: list[dict] | tuple[dict, ...], summary: dict | None) -> float:
    left = MARGIN_X
    widths = (38 * mm, 11.5 * mm, 11.5 * mm, 11.5 * mm, 11.5 * mm)
    total_width = sum(widths)
    document.setFont(FONT_BOLD, 9.2)
    document.drawString(left, y, title)
    y -= 3.1 * mm
    header_height = 7 * mm
    row_height = 10 * mm
    _draw_grid_row(document, left, y, widths, header_height, ("医療機関・期間", "朝", "昼", "夕", "寝前"), True)
    y -= header_height
    for row in rows:
        cells = (
            f"{row.get('mark', '')}{row['hospital']}\n{row['period']}",
            _count_cell(row, "朝"),
            _count_cell(row, "昼"),
            _count_cell(row, "夕"),
            _count_cell(row, "寝前"),
        )
        height = _period_row_height(row)
        _draw_grid_row(document, left, y, widths, height, cells, False)
        y -= height
    if summary:
        # The execution result is the most important boundary in the upper
        # table. Give it breathing room without introducing another heading.
        y -= 1.8 * mm
        cells = (
            f"■今回お渡し分\n{summary['period']}",
            _count_cell(summary, "朝"),
            _count_cell(summary, "昼"),
            _count_cell(summary, "夕"),
            _count_cell(summary, "寝前"),
        )
        _draw_grid_row(document, left, y, widths, row_height, cells, False, fill=True)
        y -= row_height
    return y


def _draw_grid_row(document, left, top, widths, height, cells, heading, fill=False) -> None:
    x = left
    # Thermal output is pure monochrome; emphasize the result with weight, not gray fill.
    document.setLineWidth(0.8 if fill else 0.35)
    for width in widths:
        document.rect(x, top - height, width, height, fill=0, stroke=1)
        x += width
    font_name = FONT_BOLD if heading or fill else FONT_NAME
    font_size = 7.2 if not heading else 7.5
    document.setFont(font_name, font_size)
    x = left
    for index, (width, cell) in enumerate(zip(widths, cells)):
        parts = str(cell).split("\n")
        if index == 0 and not heading:
            parts = [line for part in parts for line in _wrap(part, width - 2.4 * mm, font_size, font_name)]
        for line_index, part in enumerate(parts):
            if index == 0:
                _draw_fitted_text(
                    document, x + 1.2 * mm, top - (3.3 + line_index * 3.5) * mm,
                    part, width - 2.4 * mm, font_name, font_size,
                )
            else:
                document.drawCentredString(x + width / 2, top - (4.5 + line_index * 3.5) * mm, part)
        x += width


def _draw_fitted_text(document, x: float, y: float, text: str, width: float,
                      font_name: str, font_size: float) -> None:
    """Fit a long institution name inside its table cell without overlap."""
    size = font_size
    while size > 5.2 and pdfmetrics.stringWidth(text, font_name, size) > width:
        size -= 0.2
    value = text
    if pdfmetrics.stringWidth(value, font_name, size) > width:
        suffix = "…"
        while value and pdfmetrics.stringWidth(value + suffix, font_name, size) > width:
            value = value[:-1]
        value += suffix
    document.setFont(font_name, size)
    document.drawString(x, y, value)
    document.setFont(font_name, font_size)


def _period_row_height(row: dict, delivery: bool = False) -> float:
    first = (
        f"{row.get('hospital', '今回お渡し分')}（{row.get('symbols', '')}）\n{row.get('period', '')}"
        if delivery else f"{row.get('mark', '')}{row.get('hospital', '')}\n{row.get('period', '')}"
    )
    width = 38 * mm - 2.4 * mm
    line_count = sum(len(_wrap(part, width, 7.2, FONT_BOLD if delivery else FONT_NAME))
                     for part in first.split("\n"))
    return max(10 * mm, (line_count * 3.5 + 2.5) * mm)


def _count_cell(row: dict, slot: str) -> str:
    active = row.get("active_slots", ())
    if slot not in active:
        return "-"
    return str(row.get("counts", {}).get(slot, 0))


def _register_fonts() -> None:
    regular = Path("C:/Windows/Fonts/meiryo.ttc")
    bold = Path("C:/Windows/Fonts/meiryob.ttc")
    if regular.exists() and bold.exists():
        if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(regular)))
        if FONT_BOLD not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
        return
    # Fallback for non-Windows development environments.
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    globals()["FONT_NAME"] = "HeiseiKakuGo-W5"
    globals()["FONT_BOLD"] = "HeiseiKakuGo-W5"


def _wrap(text: str, available_width: float, font_size: float, font_name: str) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > available_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
