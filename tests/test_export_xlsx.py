import csv
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import export_xlsx  # noqa: E402
import xlsx  # noqa: E402

SCRIPT = SCRIPTS_DIR / "export_xlsx.py"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

HEADER = [
    "Failed spec", "Passed on retry", "New failure", "bug_likelihood_(AI)",
    "Note", "failure_cause", "first_failed_job_url",
]


def parse_styles(xlsx_path):
    """Return {spec_name: {col_header: (value, style)}} for the first sheet."""
    with zipfile.ZipFile(xlsx_path) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

    def col_idx(ref):
        letters = "".join(c for c in ref if c.isalpha())
        n = 0
        for c in letters:
            n = n * 26 + (ord(c) - 64)
        return n - 1

    rows = []
    for row_el in root.iter(f"{NS}row"):
        cells = {}
        for c in row_el.findall(f"{NS}c"):
            ci = col_idx(c.get("r"))
            style = c.get("s")
            is_el = c.find(f"{NS}is")
            if is_el is not None:
                val = "".join(t.text or "" for t in is_el.iter(f"{NS}t"))
            else:
                v = c.find(f"{NS}v")
                val = v.text if v is not None else ""
            cells[ci] = (val, style)
        rows.append(cells)

    header = [rows[0][i][0] for i in range(len(HEADER))]
    out = {}
    for cells in rows[1:]:
        spec = cells.get(0, ("", None))[0]
        out[spec] = {header[i]: cells.get(i, ("", None)) for i in range(len(header))}
    return out


class ExportXlsxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv_path = Path(self.tmp.name) / "failed_specs_unique_1.csv"

    def write(self, rows):
        with open(self.csv_path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(rows)

    def export(self):
        out = self.csv_path.with_suffix(".xlsx")
        header, data = export_xlsx.load_csv(self.csv_path)
        sheet = export_xlsx.build_sheet(header, data, "s")
        xlsx.write_workbook(out, [sheet])
        return out

    def test_high_bug_likelihood_cell_is_red(self):
        self.write([["a.cy.js", "no", "no", "HIGH", "", "cause", "http://x/1"]])
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["bug_likelihood_(AI)"], ("HIGH", str(xlsx.STYLE_RED)))

    def test_new_failure_yes_cell_is_red(self):
        self.write([["a.cy.js", "no", "yes", "LOW", "", "cause", "http://x/1"]])
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["New failure"], ("yes", str(xlsx.STYLE_RED)))

    def test_passed_on_retry_row_is_green(self):
        self.write([["a.cy.js", "yes (2) (#9)", "no", "LOW", "", "flaky", "http://x/1"]])
        cells = parse_styles(self.export())
        # non-url, non-red cells in the row are green
        self.assertEqual(cells["a.cy.js"]["Failed spec"][1], str(xlsx.STYLE_GREEN))
        self.assertEqual(cells["a.cy.js"]["bug_likelihood_(AI)"][1], str(xlsx.STYLE_GREEN))
        # url cell in a green row uses the link-green style
        self.assertEqual(cells["a.cy.js"]["first_failed_job_url"][1], str(xlsx.STYLE_LINK_GREEN))

    def test_red_wins_over_green_in_a_flaky_new_failure_row(self):
        self.write([["a.cy.js", "yes (2) (#9)", "yes", "LOW", "", "flaky", "http://x/1"]])
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["New failure"][1], str(xlsx.STYLE_RED))
        self.assertEqual(cells["a.cy.js"]["Failed spec"][1], str(xlsx.STYLE_GREEN))

    def test_url_cell_is_hyperlink(self):
        self.write([["a.cy.js", "no", "no", "LOW", "", "cause", "https://gitlab.com/x/-/jobs/9"]])
        out = self.export()
        with zipfile.ZipFile(out) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("HYPERLINK(", sheet)
        self.assertIn("https://gitlab.com/x/-/jobs/9", sheet)

    def test_rows_sorted_alphabetically_specs_empty_last(self):
        self.write([
            ["zebra.cy.js", "no", "no", "LOW", "", "c", "http://x/1"],
            ["alpha.cy.js", "no", "no", "LOW", "", "c", "http://x/2"],
            ["", "no", "", "", "", "", ""],  # non-cypress job, empty spec
            ["mid.cy.js", "no", "no", "LOW", "", "c", "http://x/3"],
        ])
        out = self.export()
        back = xlsx.read_sheet(out)
        specs = [r[0] for r in back[1:]]
        self.assertEqual(specs, ["alpha.cy.js", "mid.cy.js", "zebra.cy.js", ""])

    def test_cli_default_output_path(self):
        self.write([["a.cy.js", "no", "no", "LOW", "", "c", "http://x/1"]])
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--csv", str(self.csv_path)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.csv_path.with_suffix(".xlsx").exists())
        self.assertTrue(self.csv_path.exists())  # source kept by default

    def test_remove_source_deletes_csv_after_export(self):
        self.write([["a.cy.js", "no", "no", "LOW", "", "c", "http://x/1"]])
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--csv", str(self.csv_path), "--remove-source"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.csv_path.with_suffix(".xlsx")
        self.assertTrue(out.exists())
        self.assertFalse(self.csv_path.exists())  # source removed

    def test_remove_source_keeps_output_when_csv_is_the_target(self):
        # guard: never delete the file we just wrote if paths coincide
        self.write([["a.cy.js", "no", "no", "LOW", "", "c", "http://x/1"]])
        out = self.csv_path.with_suffix(".xlsx")
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--csv", str(self.csv_path),
             "-o", str(out), "--remove-source"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
