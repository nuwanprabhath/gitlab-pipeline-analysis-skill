#!/usr/bin/env python3
"""
Minimal, dependency-free .xlsx (Excel/OOXML SpreadsheetML) reader + writer.

Only the small subset this skill needs is implemented — enough to emit a
formatted workbook (per-cell background fills, clickable hyperlinks, a bold
header, column widths, frozen header row) and to read a column back out of a
previously-written sheet. No third-party packages, so it runs on any OS with
a stock Python 3, matching the rest of the skill.

An .xlsx file is just a ZIP of XML parts; this writes the handful of parts
Excel requires and nothing more.

Public API:
  Cell(value, style=STYLE_DEFAULT, hyperlink=False)
  Sheet(name, rows, col_widths=None, freeze_header=True)
  write_workbook(path, sheets)
  read_sheet(path, sheet_index=0)  -> list[list[str]]  (rows of cell strings)
"""
import xml.etree.ElementTree as ET
import zipfile
from xml.sax.saxutils import escape, quoteattr

# Cell style indices — must line up with the <cellXfs> order in _STYLES_XML.
STYLE_DEFAULT = 0
STYLE_HEADER = 1        # bold
STYLE_RED = 2           # red background
STYLE_GREEN = 3         # green background
STYLE_LINK = 4          # blue underlined (hyperlink), no fill
STYLE_LINK_GREEN = 5    # hyperlink font on a green background
STYLE_LINK_RED = 6      # hyperlink font on a red background


class Cell:
    __slots__ = ("value", "style", "hyperlink", "display")

    def __init__(self, value="", style=STYLE_DEFAULT, hyperlink=False, display=None):
        # `value` is the cell text (and, for hyperlinks, the link target).
        # `display` overrides the shown text for a hyperlink (e.g. show a job
        # number while linking to the full URL).
        self.value = "" if value is None else str(value)
        self.style = style
        self.hyperlink = hyperlink
        self.display = display


class Sheet:
    def __init__(self, name, rows, col_widths=None, freeze_header=True):
        # rows: list of list of Cell (or raw values, coerced to default Cells)
        self.name = name
        self.rows = [
            [c if isinstance(c, Cell) else Cell(c) for c in row] for row in rows
        ]
        self.col_widths = col_widths
        self.freeze_header = freeze_header


def _col_letter(idx):
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    n = idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# Sheet names have a few illegal characters and a 31-char limit in Excel.
_INVALID_SHEET_CHARS = set(r'\/?*[]:')


def _safe_sheet_name(name, taken):
    cleaned = "".join("_" if ch in _INVALID_SHEET_CHARS else ch for ch in name)[:31]
    cleaned = cleaned or "Sheet"
    base = cleaned
    i = 2
    while cleaned.lower() in taken:
        suffix = f" ({i})"
        cleaned = base[: 31 - len(suffix)] + suffix
        i += 1
    taken.add(cleaned.lower())
    return cleaned


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "{sheet_overrides}"
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)

# Fills: 0 none, 1 gray125 (Excel reserves index 1), 2 red, 3 green.
_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="3">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    '<font><u/><color rgb="FF0563C1"/><sz val="11"/><name val="Calibri"/></font>'
    "</fonts>"
    '<fills count="4">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFF0000"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF92D050"/><bgColor indexed="64"/></patternFill></fill>'
    "</fills>"
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="7">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="2" borderId="0" xfId="0" applyFill="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


def _workbook_xml(sheet_names):
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
    ]
    for i, name in enumerate(sheet_names, start=1):
        parts.append(f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>')
    parts.append("</sheets></workbook>")
    return "".join(parts)


def _workbook_rels(n_sheets):
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    ]
    for i in range(1, n_sheets + 1):
        parts.append(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
        )
    parts.append(
        f'<Relationship Id="rId{n_sheets + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    parts.append("</Relationships>")
    return "".join(parts)


def _cell_xml(ref, cell):
    if cell.hyperlink and cell.value:
        # HYPERLINK() formula keeps the file self-contained (no per-sheet rels)
        # while still rendering a clickable link in Excel/LibreOffice. `display`
        # sets the shown text (e.g. a job number) while the target stays the URL.
        url = cell.value.replace('"', '""')
        shown = cell.display if cell.display is not None else cell.value
        shown_esc = str(shown).replace('"', '""')
        formula = f'HYPERLINK("{url}","{shown_esc}")'
        return (
            f'<c r="{ref}" s="{cell.style}" t="str">'
            f"<f>{escape(formula)}</f><v>{escape(str(shown))}</v></c>"
        )
    if cell.value == "":
        return f'<c r="{ref}" s="{cell.style}"/>'
    text = escape(cell.value)
    space = ' xml:space="preserve"' if cell.value != cell.value.strip() else ""
    return (
        f'<c r="{ref}" s="{cell.style}" t="inlineStr">'
        f"<is><t{space}>{text}</t></is></c>"
    )


def _sheet_xml(sheet):
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    ]
    # showGridLines="0" hides Excel's default cell gridlines, so the sheet is
    # clean to read and copy-pastes into other spreadsheets without stray
    # borders. (No cell borders are ever applied by the styles either.)
    pane = (
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        if sheet.freeze_header and sheet.rows
        else ""
    )
    parts.append(
        f'<sheetViews><sheetView showGridLines="0" workbookViewId="0">{pane}'
        "</sheetView></sheetViews>"
    )
    if sheet.col_widths:
        cols = ["<cols>"]
        for i, w in enumerate(sheet.col_widths, start=1):
            cols.append(f'<col min="{i}" max="{i}" width="{w:.1f}" customWidth="1"/>')
        cols.append("</cols>")
        parts.append("".join(cols))
    parts.append("<sheetData>")
    for r, row in enumerate(sheet.rows, start=1):
        parts.append(f'<row r="{r}">')
        for c, cell in enumerate(row):
            parts.append(_cell_xml(f"{_col_letter(c)}{r}", cell))
        parts.append("</row>")
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def _auto_widths(sheet, cap=80.0, min_w=8.0):
    widths = []
    for col in range(max((len(r) for r in sheet.rows), default=0)):
        longest = 0
        for row in sheet.rows:
            if col < len(row):
                cell = row[col]
                # size to the shown text (a hyperlink shows `display`, not the URL)
                shown = cell.display if (cell.hyperlink and cell.display is not None) else cell.value
                longest = max(longest, len(str(shown)))
        widths.append(max(min_w, min(cap, longest + 2)))
    return widths


def write_workbook(path, sheets):
    """Write a list of Sheet objects to an .xlsx file at `path`."""
    taken = set()
    names = [_safe_sheet_name(s.name, taken) for s in sheets]
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES.format(sheet_overrides=sheet_overrides))
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", _workbook_xml(names))
        z.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        z.writestr("xl/styles.xml", _STYLES_XML)
        for i, sheet in enumerate(sheets, start=1):
            if sheet.col_widths is None:
                sheet.col_widths = _auto_widths(sheet)
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheet))


# --- reader (only what compare_new_failures.py needs) ---

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _localname(tag):
    return tag.rsplit("}", 1)[-1]


def _col_index(ref):
    letters = "".join(ch for ch in ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - 64)
    return idx - 1


def read_sheet(path, sheet_index=0):
    """Return the given sheet's cells as a list of rows (list of str).

    Reads inline strings and cached formula/number values (`<v>`). Shared
    strings are not emitted by this writer, so they're not needed here.
    """
    with zipfile.ZipFile(path) as z:
        names = sorted(
            n for n in z.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        )
        # sort numerically by the trailing index so sheet10 doesn't precede sheet2
        names.sort(key=lambda n: int("".join(ch for ch in n.split("/")[-1] if ch.isdigit()) or 0))
        if sheet_index >= len(names):
            return []
        data = z.read(names[sheet_index])

    root = ET.fromstring(data)
    rows = []
    for row_el in root.iter(f"{{{_MAIN_NS}}}row"):
        cells = {}
        maxc = -1
        for c_el in row_el.findall(f"{{{_MAIN_NS}}}c"):
            ref = c_el.get("r") or ""
            ci = _col_index(ref) if ref else len(cells)
            value = ""
            is_el = c_el.find(f"{{{_MAIN_NS}}}is")
            if is_el is not None:
                value = "".join(t.text or "" for t in is_el.iter(f"{{{_MAIN_NS}}}t"))
            else:
                v_el = c_el.find(f"{{{_MAIN_NS}}}v")
                if v_el is not None:
                    value = v_el.text or ""
            cells[ci] = value
            maxc = max(maxc, ci)
        rows.append([cells.get(i, "") for i in range(maxc + 1)])
    return rows
