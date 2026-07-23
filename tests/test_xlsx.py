import sys
import tempfile
import unittest
import xml.dom.minidom as minidom
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import xlsx  # noqa: E402


class ColLetterTests(unittest.TestCase):
    def test_letters(self):
        self.assertEqual(xlsx._col_letter(0), "A")
        self.assertEqual(xlsx._col_letter(25), "Z")
        self.assertEqual(xlsx._col_letter(26), "AA")
        self.assertEqual(xlsx._col_letter(27), "AB")


class WriteReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "out.xlsx"

    def test_all_parts_are_wellformed_xml(self):
        rows = [[xlsx.Cell("h1", xlsx.STYLE_HEADER), xlsx.Cell("h2", xlsx.STYLE_HEADER)],
                [xlsx.Cell("a"), xlsx.Cell("b")]]
        xlsx.write_workbook(self.path, [xlsx.Sheet("Sheet1", rows)])
        with zipfile.ZipFile(self.path) as z:
            self.assertIsNone(z.testzip())
            for name in z.namelist():
                if name.endswith(".xml") or name.endswith(".rels"):
                    minidom.parseString(z.read(name))  # raises on malformed XML

    def test_roundtrip_values(self):
        rows = [
            [xlsx.Cell("Failed spec"), xlsx.Cell("val")],
            [xlsx.Cell("a.cy.js"), xlsx.Cell("x")],
            [xlsx.Cell("cover+floristics.cy.js"), xlsx.Cell("y")],
        ]
        xlsx.write_workbook(self.path, [xlsx.Sheet("S", rows)])
        back = xlsx.read_sheet(self.path)
        self.assertEqual(back[0], ["Failed spec", "val"])
        self.assertEqual(back[1], ["a.cy.js", "x"])
        self.assertEqual(back[2][0], "cover+floristics.cy.js")

    def test_special_characters_escaped_and_roundtrip(self):
        rows = [[xlsx.Cell('a "q" & <b>.cy.js')]]
        xlsx.write_workbook(self.path, [xlsx.Sheet("S", rows)])
        self.assertEqual(xlsx.read_sheet(self.path)[0][0], 'a "q" & <b>.cy.js')

    def test_hyperlink_cell_uses_formula_and_caches_value(self):
        url = "https://gitlab.com/x/-/jobs/1"
        rows = [[xlsx.Cell(url, xlsx.STYLE_LINK, hyperlink=True)]]
        xlsx.write_workbook(self.path, [xlsx.Sheet("S", rows)])
        with zipfile.ZipFile(self.path) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("HYPERLINK(", sheet)
        self.assertIn(url, sheet)
        # cached value is readable back
        self.assertEqual(xlsx.read_sheet(self.path)[0][0], url)

    def test_styles_applied_to_cells(self):
        rows = [[xlsx.Cell("HIGH", xlsx.STYLE_RED), xlsx.Cell("ok", xlsx.STYLE_GREEN)]]
        xlsx.write_workbook(self.path, [xlsx.Sheet("S", rows)])
        with zipfile.ZipFile(self.path) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('s="2"', sheet)  # red
        self.assertIn('s="3"', sheet)  # green

    def test_gridlines_disabled_and_no_cell_borders(self):
        xlsx.write_workbook(self.path, [xlsx.Sheet("S", [[xlsx.Cell("a")]])])
        with zipfile.ZipFile(self.path) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
            styles = z.read("xl/styles.xml").decode()
        self.assertIn('showGridLines="0"', sheet)
        # no cellXf applies a border (applyBorder is never set)
        self.assertNotIn("applyBorder", styles)

    def test_multiple_sheets(self):
        xlsx.write_workbook(
            self.path,
            [xlsx.Sheet("First", [[xlsx.Cell("1")]]), xlsx.Sheet("Second", [[xlsx.Cell("2")]])],
        )
        self.assertEqual(xlsx.read_sheet(self.path, 0)[0][0], "1")
        self.assertEqual(xlsx.read_sheet(self.path, 1)[0][0], "2")

    def test_invalid_sheet_name_sanitized(self):
        # illegal chars and >31 chars get cleaned; file must still be valid
        xlsx.write_workbook(self.path, [xlsx.Sheet("a/b:c*" + "x" * 40, [[xlsx.Cell("v")]])])
        with zipfile.ZipFile(self.path) as z:
            root = minidom.parseString(z.read("xl/workbook.xml"))
        name = root.getElementsByTagName("sheet")[0].getAttribute("name")
        self.assertEqual(len(name), 31)  # truncated to Excel's limit
        self.assertFalse(set(name) & set(r'\/?*[]:'))  # no illegal chars
        self.assertEqual(xlsx.read_sheet(self.path)[0][0], "v")


if __name__ == "__main__":
    unittest.main()
