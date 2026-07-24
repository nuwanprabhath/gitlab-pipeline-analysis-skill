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
    "Note", "Locally reproducible", "failure_cause", "cypress_url",
    "first_failed_job_url", "second_failed_job_url", "third_failed_job_url",
]
JOB = "https://gitlab.com/x/-/jobs/"


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


def mk(spec, passed="no", newfail="no", bug="LOW", cause="c",
       cypress="", first="", second="", third=""):
    return [spec, passed, newfail, bug, "", "", cause, cypress, first, second, third]


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

    def export(self, cause_jobs=None):
        out = self.csv_path.with_suffix(".xlsx")
        header, data = export_xlsx.load_csv(self.csv_path)
        sheet = export_xlsx.build_sheet(header, data, "s", cause_jobs=cause_jobs)
        xlsx.write_workbook(out, [sheet])
        return out

    def test_high_bug_likelihood_cell_is_red(self):
        self.write([mk("a.cy.js", bug="HIGH", first=JOB + "1")])
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["bug_likelihood_(AI)"], ("HIGH", str(xlsx.STYLE_RED)))

    def test_new_failure_yes_cell_is_red(self):
        self.write([mk("a.cy.js", newfail="yes", first=JOB + "1")])
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["New failure"], ("yes", str(xlsx.STYLE_RED)))

    def test_passed_on_retry_row_is_green(self):
        self.write([mk("a.cy.js", passed="yes (2) (#9)", first=JOB + "1")])
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["Failed spec"][1], str(xlsx.STYLE_GREEN))
        self.assertEqual(cells["a.cy.js"]["bug_likelihood_(AI)"][1], str(xlsx.STYLE_GREEN))
        # url cell in a green row uses the link-green style
        self.assertEqual(cells["a.cy.js"]["first_failed_job_url"][1], str(xlsx.STYLE_LINK_GREEN))

    def test_red_wins_over_green_in_a_flaky_new_failure_row(self):
        self.write([mk("a.cy.js", passed="yes (2) (#9)", newfail="yes", first=JOB + "1")])
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["New failure"][1], str(xlsx.STYLE_RED))
        self.assertEqual(cells["a.cy.js"]["Failed spec"][1], str(xlsx.STYLE_GREEN))

    def test_job_url_shows_job_number_as_link_text(self):
        self.write([mk("a.cy.js", first=JOB + "15479301209")])
        out = self.export()
        with zipfile.ZipFile(out) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('HYPERLINK("https://gitlab.com/x/-/jobs/15479301209","15479301209")', sheet)

    def test_failure_cause_job_cell_is_red_the_others_are_not(self):
        # spec failed in 3 attempts; cause is the SECOND one -> that cell red
        self.write([mk("a.cy.js", first=JOB + "100", second=JOB + "200", third=JOB + "300")])
        cells = parse_styles(self.export(cause_jobs={"a.cy.js": "200"}))
        self.assertEqual(cells["a.cy.js"]["first_failed_job_url"][1], str(xlsx.STYLE_LINK))
        self.assertEqual(cells["a.cy.js"]["second_failed_job_url"][1], str(xlsx.STYLE_LINK_RED))
        self.assertEqual(cells["a.cy.js"]["third_failed_job_url"][1], str(xlsx.STYLE_LINK))

    def test_passed_on_retry_cell_links_to_passed_job(self):
        self.write([mk("a.cy.js", passed="yes (2) (#15505213166)", first=JOB + "100")])
        out = self.export()
        with zipfile.ZipFile(out) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        # whole cell links to the passed job's URL; text keeps the yes (N) (#id)
        self.assertIn(
            'HYPERLINK("https://gitlab.com/x/-/jobs/15505213166","yes (2) (#15505213166)")',
            sheet,
        )
        cells = parse_styles(out)
        # flaky row is green, so the link uses the link-green style
        self.assertEqual(cells["a.cy.js"]["Passed on retry"][1], str(xlsx.STYLE_LINK_GREEN))

    def test_passed_on_retry_no_is_not_a_link(self):
        self.write([mk("a.cy.js", passed="no", first=JOB + "100")])
        out = self.export()
        cells = parse_styles(out)
        # "no" stays plain text (no hyperlink style)
        self.assertEqual(cells["a.cy.js"]["Passed on retry"], ("no", str(xlsx.STYLE_DEFAULT)))

    def test_cypress_url_links_with_job_number_text(self):
        self.write([mk("a.cy.js", cypress="https://cloud.cypress.io/projects/6b9ofw/runs/12361",
                       first=JOB + "100")])
        out = self.export(cause_jobs={"a.cy.js": "100"})
        with zipfile.ZipFile(out) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('HYPERLINK("https://cloud.cypress.io/projects/6b9ofw/runs/12361","100")', sheet)

    def test_empty_job_url_cells_are_blank(self):
        self.write([mk("a.cy.js", first=JOB + "100")])  # no second/third
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["second_failed_job_url"][0], "")
        self.assertEqual(cells["a.cy.js"]["third_failed_job_url"][0], "")

    def test_rows_sorted_alphabetically_specs_empty_last(self):
        self.write([
            mk("zebra.cy.js", first=JOB + "1"),
            mk("alpha.cy.js", first=JOB + "2"),
            mk(""),  # non-cypress job, empty spec
            mk("mid.cy.js", first=JOB + "3"),
        ])
        back = xlsx.read_sheet(self.export())
        specs = [r[0] for r in back[1:]]
        self.assertEqual(specs, ["alpha.cy.js", "mid.cy.js", "zebra.cy.js", ""])

    def test_cli_default_output_path(self):
        self.write([mk("a.cy.js", first=JOB + "1")])
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--csv", str(self.csv_path)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.csv_path.with_suffix(".xlsx").exists())
        self.assertTrue(self.csv_path.exists())  # source kept by default

    def test_remove_source_deletes_csv_after_export(self):
        self.write([mk("a.cy.js", first=JOB + "1")])
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--csv", str(self.csv_path), "--remove-source"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.csv_path.with_suffix(".xlsx")
        self.assertTrue(out.exists())
        self.assertFalse(self.csv_path.exists())  # source removed

    def test_remove_source_keeps_output_when_csv_is_the_target(self):
        self.write([mk("a.cy.js", first=JOB + "1")])
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
