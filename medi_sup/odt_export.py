from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def export_or_append_odt(
    text: str,
    output_path: str | Path,
    table_data: dict | None = None,
    pharmacy_name: str = "",
    metadata: dict | None = None,
) -> tuple[Path, int]:
    """Create an editable ODT, or insert this report as the first page."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pages = 1
    if output.exists():
        with ZipFile(output, "r") as source:
            members = {name: source.read(name) for name in source.namelist()}
        root = ET.fromstring(members["content.xml"])
        _ensure_page_break_style(root)
        _ensure_table_styles(root)
        body = root.find(f"{{{NS['office']}}}body/{{{NS['office']}}}text")
        if body is None:
            raise ValueError("ODT本文を確認できません。別の保存先を指定してください。")
        pages = len(body.findall(f"{{{NS['text']}}}p[@{{{NS['text']}}}style-name='PageBreak']")) + 2
        elements = _report_elements(text, table_data, pharmacy_name)
        elements.append(ET.Element(f"{{{NS['text']}}}p", {f"{{{NS['text']}}}style-name": "PageBreak"}))
        for element in reversed(elements):
            body.insert(0, element)
        members["content.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    else:
        members = _new_document(text, table_data, pharmacy_name)
    _update_metadata(members, metadata or {})
    temporary = output.with_suffix(".odt.tmp")
    try:
        with ZipFile(temporary, "w") as target:
            target.writestr("mimetype", b"application/vnd.oasis.opendocument.text", compress_type=ZIP_STORED)
            for name, value in members.items():
                if name != "mimetype":
                    target.writestr(name, value, compress_type=ZIP_DEFLATED)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output, pages


def _update_metadata(members: dict[str, bytes], values: dict) -> None:
    """印刷されないODT文書プロパティに、最新票の計算条件を保存する。"""
    if "meta.xml" in members:
        root = ET.fromstring(members["meta.xml"])
        office_meta = root.find(f"{{{NS['office']}}}meta")
        if office_meta is None:
            office_meta = ET.SubElement(root, f"{{{NS['office']}}}meta")
    else:
        root = ET.Element(f"{{{NS['office']}}}document-meta", {f"{{{NS['office']}}}version": "1.2"})
        office_meta = ET.SubElement(root, f"{{{NS['office']}}}meta")
    prefix = "MediSup."
    name_key = f"{{{NS['meta']}}}name"
    for child in list(office_meta):
        if child.tag == f"{{{NS['meta']}}}user-defined" and child.get(name_key, "").startswith(prefix):
            office_meta.remove(child)
    for name, value in values.items():
        item = ET.SubElement(office_meta, f"{{{NS['meta']}}}user-defined", {
            name_key: prefix + str(name),
            f"{{{NS['meta']}}}value-type": "string",
        })
        item.text = str(value)
    members["meta.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    manifest_name = "META-INF/manifest.xml"
    manifest = members.get(manifest_name, b"")
    if b'full-path="meta.xml"' not in manifest:
        manifest = manifest.replace(
            b"</manifest:manifest>",
            b' <manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>\n</manifest:manifest>',
        )
        members[manifest_name] = manifest


def _ensure_page_break_style(root: ET.Element) -> None:
    automatic = root.find(f"{{{NS['office']}}}automatic-styles")
    if automatic is None:
        automatic = ET.Element(f"{{{NS['office']}}}automatic-styles")
        root.insert(0, automatic)
    existing = automatic.find(f"{{{NS['style']}}}style[@{{{NS['style']}}}name='PageBreak']")
    if existing is not None:
        return
    style = ET.SubElement(automatic, f"{{{NS['style']}}}style", {
        f"{{{NS['style']}}}name": "PageBreak",
        f"{{{NS['style']}}}family": "paragraph",
    })
    properties = ET.SubElement(style, f"{{{NS['style']}}}paragraph-properties")
    properties.set(f"{{{NS['fo']}}}break-before", "page")


def _ensure_table_styles(root: ET.Element) -> None:
    automatic = root.find(f"{{{NS['office']}}}automatic-styles")
    if automatic is None:
        automatic = ET.Element(f"{{{NS['office']}}}automatic-styles")
        root.insert(0, automatic)
    definitions = (
        ("MediFirstColumn", "table-column", "table-column-properties", {f"{{{NS['style']}}}column-width": "38mm"}),
        ("MediCountColumn", "table-column", "table-column-properties", {f"{{{NS['style']}}}column-width": "11.5mm"}),
        ("MediInterTitle", "table-column", "table-column-properties", {f"{{{NS['style']}}}column-width": "40mm"}),
        ("MediInterDates", "table-column", "table-column-properties", {f"{{{NS['style']}}}column-width": "42mm"}),
        ("MediCell", "table-cell", "table-cell-properties", {
            f"{{{NS['fo']}}}border": "0.3mm solid #000000",
            f"{{{NS['fo']}}}padding": "0.8mm",
            f"{{{NS['style']}}}vertical-align": "middle",
        }),
    )
    for name, family, property_name, attributes in definitions:
        if automatic.find(f"{{{NS['style']}}}style[@{{{NS['style']}}}name='{name}']") is not None:
            continue
        style = ET.SubElement(automatic, f"{{{NS['style']}}}style", {
            f"{{{NS['style']}}}name": name,
            f"{{{NS['style']}}}family": family,
        })
        ET.SubElement(style, f"{{{NS['style']}}}{property_name}", attributes)


def _append_report(body: ET.Element, text: str) -> None:
    for paragraph in _report_elements(text):
        body.append(paragraph)


def _report_elements(text: str, table_data: dict | None = None, pharmacy_name: str = "") -> list[ET.Element]:
    elements = []
    printable_text = _header_and_tail(text) if table_data else text
    lines = printable_text.rstrip().splitlines()
    for index, line in enumerate(lines):
        style_name = "Heading" if line.startswith(("★", "■")) else "BodyText"
        paragraph = ET.Element(f"{{{NS['text']}}}p", {f"{{{NS['text']}}}style-name": style_name})
        paragraph.text = line
        elements.append(paragraph)
        if table_data and index == 0:
            elements.extend(_table_elements(table_data))
    if pharmacy_name.strip():
        paragraph = ET.Element(f"{{{NS['text']}}}p", {f"{{{NS['text']}}}style-name": "Footer"})
        paragraph.text = f"{pharmacy_name.strip()}　外来服薬支援"
        elements.append(paragraph)
    return elements


def _new_document(text: str, table_data: dict | None = None, pharmacy_name: str = "") -> dict[str, bytes]:
    root = ET.Element(f"{{{NS['office']}}}document-content", {f"{{{NS['office']}}}version": "1.2"})
    automatic = ET.SubElement(root, f"{{{NS['office']}}}automatic-styles")
    for name, bold, page_break in (("BodyText", False, False), ("Heading", True, False), ("Footer", False, False), ("PageBreak", False, True)):
        style = ET.SubElement(automatic, f"{{{NS['style']}}}style", {
            f"{{{NS['style']}}}name": name,
            f"{{{NS['style']}}}family": "paragraph",
        })
        paragraph_properties = ET.SubElement(style, f"{{{NS['style']}}}paragraph-properties")
        if page_break:
            paragraph_properties.set(f"{{{NS['fo']}}}break-before", "page")
        text_properties = ET.SubElement(style, f"{{{NS['style']}}}text-properties")
        text_properties.set(f"{{{NS['fo']}}}font-size", "10pt")
        if bold:
            text_properties.set(f"{{{NS['fo']}}}font-weight", "bold")
    _ensure_table_styles(root)
    office_body = ET.SubElement(root, f"{{{NS['office']}}}body")
    body = ET.SubElement(office_body, f"{{{NS['office']}}}text")
    for element in _report_elements(text, table_data, pharmacy_name):
        body.append(element)
    content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    manifest = b'''<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
</manifest:manifest>'''
    return {"content.xml": content, "META-INF/manifest.xml": manifest}


def _header_and_tail(text: str) -> str:
    lines = text.rstrip().splitlines()
    tail_start = next((
        index for index, line in enumerate(lines)
        if line.startswith("★薬局コメント") or line.startswith("■薬局コメント") or line.startswith("★受診日情報")
        or line.startswith("★臨時追加実施")
        or line.startswith("★処方変更・残薬確認")
    ), None)
    header = next((line for line in lines if line.strip()), "")
    return "\n".join([header] if tail_start is None else [header, *lines[tail_start:]])


def _table_elements(data: dict) -> list[ET.Element]:
    elements: list[ET.Element] = []
    source_title = "■所持薬（単位：包）"
    elements.extend(_period_table(source_title, data.get("sources", ()), "Held"))
    elements.extend(_intermittent_tables("", data.get("intermittent_all", ()), "IntermittentAll"))
    executed = data.get("executed")
    if executed:
        elements.append(_paragraph("■今回お渡し分", "Heading"))
        delivered_rows = (executed, *data.get("executed_extra", ()))
        elements.extend(_period_table("", delivered_rows, "Delivered"))
    elements.extend(_intermittent_tables("", data.get("intermittent", ()), "IntermittentDelivered"))
    if data.get("uncombined"):
        elements.extend(_period_table("■薬局預かり分（単位：包）", data.get("uncombined", ()), "Remaining"))
    elements.extend(_intermittent_tables("", data.get("intermittent_remaining", ()), "IntermittentRemaining"))
    return elements


def _paragraph(value: str, style: str = "BodyText") -> ET.Element:
    element = ET.Element(f"{{{NS['text']}}}p", {f"{{{NS['text']}}}style-name": style})
    element.text = value
    return element


def _period_table(title: str, rows, name: str) -> list[ET.Element]:
    if title == "" and not rows:
        return []
    elements = []
    if title:
        elements.append(_paragraph(title, "Heading"))
    table = ET.Element(f"{{{NS['table']}}}table", {f"{{{NS['table']}}}name": f"MediSup{name}"})
    ET.SubElement(table, f"{{{NS['table']}}}table-column", {f"{{{NS['table']}}}style-name": "MediFirstColumn"})
    ET.SubElement(table, f"{{{NS['table']}}}table-column", {
        f"{{{NS['table']}}}style-name": "MediCountColumn",
        f"{{{NS['table']}}}number-columns-repeated": "4",
    })
    if title:
        _add_row(table, ("医療機関・期間", "朝", "昼", "夕", "寝前"), heading=True)
    for row in rows:
        hospital = row.get("hospital", "")
        if hospital in ("今回お渡し分", "合包部分"):
            first = f"{hospital}（{row.get('symbols', '')}）\n{row.get('period', '')}"
        else:
            first = f"{row.get('mark', '')}{hospital}\n{row.get('period', '')}"
        active = row.get("active_slots", ())
        counts = row.get("counts", {})
        values = [first]
        for slot in ("朝", "昼", "夕", "寝前"):
            values.append(str(counts.get(slot, 0)) if slot in active else "-")
        _add_row(table, values)
    elements.append(table)
    return elements


def _add_row(table: ET.Element, values, heading: bool = False) -> None:
    row = ET.SubElement(table, f"{{{NS['table']}}}table-row")
    for value in values:
        cell = ET.SubElement(row, f"{{{NS['table']}}}table-cell", {
            f"{{{NS['office']}}}value-type": "string",
            f"{{{NS['table']}}}style-name": "MediCell",
        })
        for line in str(value).split("\n"):
            cell.append(_paragraph(line, "Heading" if heading else "BodyText"))


def _intermittent_tables(title: str, items, name: str) -> list[ET.Element]:
    if not items:
        return []
    elements = [_paragraph(title, "Heading")] if title else []
    table = ET.Element(f"{{{NS['table']}}}table", {f"{{{NS['table']}}}name": f"MediSup{name}"})
    ET.SubElement(table, f"{{{NS['table']}}}table-column", {f"{{{NS['table']}}}style-name": "MediInterTitle"})
    ET.SubElement(table, f"{{{NS['table']}}}table-column", {f"{{{NS['table']}}}style-name": "MediInterDates"})
    for item in items:
        _add_row(table, (item.get("title", ""), f"服用日：{item.get('dates', '')}"))
    elements.append(table)
    return elements
