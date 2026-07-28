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
    "Note", "Locally reproducible", "failure_cause",
    "first_cypress_url", "second_cypress_url", "first_job_url", "second_job_url",
]
JOB = "https://gitlab.com/x/-/jobs/"
CY = "https://cloud.cypress.io/projects/aa/runs/"


def runs(*rs):
    """Build a spec_runs entry: rs is a sequence of (job_id, status)."""
    return [{"job_id": str(j), "job_name": "cypress-run 1/8", "status": s} for j, s in rs]


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


def mk(spec, passed="no", newfail="no", bug="LOW", cause="c", note="",
       first_cyp="", second_cyp="", first="", second=""):
    return [spec, passed, newfail, bug, note, "", cause, first_cyp, second_cyp, first, second]


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

    def export(self, cause_jobs=None, spec_runs=None):
        out = self.csv_path.with_suffix(".xlsx")
        header, data = export_xlsx.load_csv(self.csv_path)
        sheet = export_xlsx.build_sheet(
            header, data, "s", cause_jobs=cause_jobs, spec_runs=spec_runs
        )
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
        # first attempt failed, second passed -> flaky (green row)
        self.write([mk("a.cy.js", passed="yes (2) (#2)", first=JOB + "1", second=JOB + "2")])
        sr = {"a.cy.js": runs(("1", "FAILED"), ("2", "PASSED"))}
        cells = parse_styles(self.export(spec_runs=sr))
        self.assertEqual(cells["a.cy.js"]["Failed spec"][1], str(xlsx.STYLE_GREEN))
        self.assertEqual(cells["a.cy.js"]["bug_likelihood_(AI)"][1], str(xlsx.STYLE_GREEN))
        # failed first attempt -> red; passed second attempt -> green link
        self.assertEqual(cells["a.cy.js"]["first_job_url"][1], str(xlsx.STYLE_LINK_RED))
        self.assertEqual(cells["a.cy.js"]["second_job_url"][1], str(xlsx.STYLE_LINK_GREEN))

    def test_red_wins_over_green_in_a_flaky_new_failure_row(self):
        self.write([mk("a.cy.js", passed="yes (2) (#9)", newfail="yes", first=JOB + "1")])
        cells = parse_styles(self.export())
        self.assertEqual(cells["a.cy.js"]["New failure"][1], str(xlsx.STYLE_RED))
        self.assertEqual(cells["a.cy.js"]["Failed spec"][1], str(xlsx.STYLE_GREEN))

    def test_job_url_shows_job_number_as_link_text(self):
        self.write([mk("a.cy.js", first=JOB + "15479301209")])
        out = self.export(spec_runs={"a.cy.js": runs(("15479301209", "FAILED"))})
        with zipfile.ZipFile(out) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('HYPERLINK("https://gitlab.com/x/-/jobs/15479301209","15479301209")', sheet)

    def test_failure_cause_job_cell_is_orange_and_bold(self):
        # first attempt passed, second failed and is the cause -> orange+bold
        # (orange, not red, so the bold link text stays legible)
        self.write([mk("a.cy.js", first=JOB + "100", second=JOB + "200")])
        sr = {"a.cy.js": runs(("100", "PASSED"), ("200", "FAILED"))}
        cells = parse_styles(self.export(cause_jobs={"a.cy.js": "200"}, spec_runs=sr))
        self.assertEqual(cells["a.cy.js"]["first_job_url"][1], str(xlsx.STYLE_LINK))
        self.assertEqual(cells["a.cy.js"]["second_job_url"][1], str(xlsx.STYLE_LINK_ORANGE_BOLD))

    def test_passed_on_retry_cell_links_to_passed_job(self):
        self.write([mk("a.cy.js", passed="yes (2) (#15505213166)",
                       first=JOB + "1", second=JOB + "15505213166")])
        sr = {"a.cy.js": runs(("1", "FAILED"), ("15505213166", "PASSED"))}
        out = self.export(spec_runs=sr)
        with zipfile.ZipFile(out) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn(
            'HYPERLINK("https://gitlab.com/x/-/jobs/15505213166","yes (2) (#15505213166)")',
            sheet,
        )
        cells = parse_styles(out)
        self.assertEqual(cells["a.cy.js"]["Passed on retry"][1], str(xlsx.STYLE_LINK_GREEN))

    def test_passed_on_retry_no_is_not_a_link(self):
        self.write([mk("a.cy.js", passed="no", first=JOB + "100")])
        cells = parse_styles(self.export(spec_runs={"a.cy.js": runs(("100", "FAILED"))}))
        self.assertEqual(cells["a.cy.js"]["Passed on retry"], ("no", str(xlsx.STYLE_DEFAULT)))

    def test_cypress_cell_red_when_that_attempt_failed(self):
        self.write([mk("a.cy.js", first_cyp=CY + "12361", first=JOB + "100")])
        sr = {"a.cy.js": runs(("100", "FAILED"))}
        out = self.export(spec_runs=sr)
        with zipfile.ZipFile(out) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        # cypress link target is the cloud URL; shown text is the job number
        self.assertIn(f'HYPERLINK("{CY}12361","100")', sheet)
        cells = parse_styles(out)
        self.assertEqual(cells["a.cy.js"]["first_cypress_url"][1], str(xlsx.STYLE_LINK_RED))

    def test_every_url_cell_is_a_clickable_hyperlink(self):
        # Guard: any first/second job or cypress cell holding a URL MUST render
        # as a HYPERLINK formula, never plain text. (Regression guard for the
        # "none of the links are clickable" bug.)
        self.write([
            mk("a.cy.js", first_cyp=CY + "1", second_cyp=CY + "2",
               first=JOB + "100", second=JOB + "200"),
            mk("b.cy.js", first_cyp=CY + "3", first=JOB + "300"),
        ])
        sr = {
            "a.cy.js": runs(("100", "FAILED"), ("200", "PASSED")),
            "b.cy.js": runs(("300", "FAILED")),
        }
        out = self.export(spec_runs=sr)
        import xml.etree.ElementTree as ET
        NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        with zipfile.ZipFile(out) as z:
            root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        header = [c.find(f"{NS}is/{NS}t").text for c in
                  list(root.iter(f"{NS}row"))[0].findall(f"{NS}c")]
        url_cols = {chr(ord("A") + i) for i, h in enumerate(header)
                    if h in ("first_job_url", "second_job_url",
                             "first_cypress_url", "second_cypress_url")}
        checked = 0
        for row_el in list(root.iter(f"{NS}row"))[1:]:
            for c in row_el.findall(f"{NS}c"):
                col = "".join(ch for ch in c.get("r") if ch.isalpha())
                if col in url_cols and (c.find(f"{NS}v") is not None or c.find(f"{NS}is") is not None):
                    self.assertIsNotNone(
                        c.find(f"{NS}f"),
                        f"cell {c.get('r')} has a value but no HYPERLINK formula (not clickable)",
                    )
                    self.assertIn("HYPERLINK(", c.find(f"{NS}f").text)
                    checked += 1
        self.assertEqual(checked, 6)  # a: 4 urls, b: 2 urls

    def test_note_job_crashed_is_red(self):
        self.write([mk("a.cy.js", note="JOB CRASHED", first=JOB + "100")])
        cells = parse_styles(self.export(spec_runs={"a.cy.js": runs(("100", "MISSING"))}))
        self.assertEqual(cells["a.cy.js"]["Note"], ("JOB CRASHED", str(xlsx.STYLE_RED)))

    def test_empty_job_url_cells_are_blank(self):
        self.write([mk("a.cy.js", first=JOB + "100")])  # no second
        cells = parse_styles(self.export(spec_runs={"a.cy.js": runs(("100", "FAILED"))}))
        self.assertEqual(cells["a.cy.js"]["second_job_url"][0], "")

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

    def test_all_specs_sheet_job_cell_shows_name_and_status_colour(self):
        # all_specs sheet: no failure_cause column; job cells show job name and
        # are red if that attempt failed, green if it passed. No bold.
        header = ["Spec", "first_job_url", "second_job_url"]
        with open(self.csv_path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerow(["a.cy.js", JOB + "100", JOB + "200"])
        out = self.csv_path.with_suffix(".xlsx")
        h, data = export_xlsx.load_csv(self.csv_path)
        sr = {"a.cy.js": runs(("100", "PASSED"), ("200", "FAILED"))}
        sheet = export_xlsx.build_sheet(h, data, "s", cause_jobs={"a.cy.js": "200"}, spec_runs=sr)
        xlsx.write_workbook(out, [sheet])
        with zipfile.ZipFile(out) as z:
            xml = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('HYPERLINK("https://gitlab.com/x/-/jobs/100","100 (cypress-run 1/8)")', xml)
        back = xlsx.read_sheet(out)
        # passed attempt green, failed attempt red (NOT bold even though it's the cause job)
        self.assertEqual(back[1][1], "100 (cypress-run 1/8)")
        # verify styles via the raw sheet
        import xml.etree.ElementTree as ET
        NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        cells = {c.get("r"): c.get("s") for c in ET.fromstring(xml).iter(f"{NS}c")}
        self.assertEqual(cells["B2"], str(xlsx.STYLE_LINK_GREEN))  # passed
        self.assertEqual(cells["C2"], str(xlsx.STYLE_LINK_RED))    # failed, no bold in all_specs

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
