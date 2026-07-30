"""Tests for automation/summarize_report.py — the one-line count that goes
into the "analysis finished" desktop notification.

This reads the exported workbook, so it must tolerate the sheet's real shape:
decorated spec names, N/A placeholders, and columns being added or reordered.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import xlsx  # noqa: E402

SCRIPT = REPO / "automation" / "summarize_report.py"

FULL_HEADER = [
    "Failed spec", "Passed on retry", "New failure", "bug_likelihood_(AI)",
    "Note", "Locally reproducible", "failure_cause",
]


def run(path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True
    )


def write_sheet(path, header, rows):
    xlsx.write_workbook(path, [xlsx.Sheet("s", [
        [xlsx.Cell(c) for c in header],
        *[[xlsx.Cell(c) for c in r] for r in rows],
    ])])


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _book(self, rows, header=None):
        p = Path(self.tmp.name) / "failed_specs_unique_1.xlsx"
        write_sheet(p, header or FULL_HEADER, rows)
        return p

    def _row(self, spec, new="N/A", likelihood=""):
        return [spec, "no", new, likelihood, "", "", "cause"]

    def test_counts_totals_and_high(self):
        p = self._book([
            self._row("a.cy.js [run 3/8]", likelihood="HIGH"),
            self._row("b.cy.js [run 1/8]", likelihood="HIGH"),
            self._row("c.cy.js", likelihood="LOW"),
        ])
        r = run(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "3 unique failures — 2 HIGH")

    def test_counts_new_failures(self):
        p = self._book([
            self._row("a.cy.js", new="yes", likelihood="HIGH"),
            self._row("b.cy.js", new="no", likelihood="MEDIUM"),
        ])
        self.assertEqual(run(p).stdout.strip(), "2 unique failures — 1 HIGH, 1 new")

    def test_na_new_failure_is_not_counted_as_new(self):
        """First-ever run leaves the column N/A — that is not a regression."""
        p = self._book([self._row("a.cy.js", new="N/A"), self._row("b.cy.js")])
        self.assertEqual(run(p).stdout.strip(), "2 unique failures")

    def test_singular_wording_for_one_failure(self):
        p = self._book([self._row("a.cy.js")])
        self.assertEqual(run(p).stdout.strip(), "1 unique failure")

    def test_green_pipeline_reports_no_failures(self):
        p = self._book([])
        self.assertEqual(run(p).stdout.strip(), "no failures")

    def test_likelihood_matching_is_case_insensitive(self):
        p = self._book([self._row("a.cy.js", new="YES", likelihood="high")])
        self.assertEqual(run(p).stdout.strip(), "1 unique failure — 1 HIGH, 1 new")

    def test_blank_trailing_rows_are_ignored(self):
        p = self._book([self._row("a.cy.js"), ["", "", "", "", "", "", ""]])
        self.assertEqual(run(p).stdout.strip(), "1 unique failure")

    def test_survives_reordered_and_missing_columns(self):
        """Only 'Failed spec' is guaranteed; the rest may move or vanish."""
        p = self._book(
            [["HIGH", "a.cy.js"], ["LOW", "b.cy.js"]],
            header=["bug_likelihood_(AI)", "Failed spec"],
        )
        self.assertEqual(run(p).stdout.strip(), "2 unique failures — 1 HIGH")

    def test_missing_optional_columns_do_not_crash(self):
        p = self._book([["a.cy.js"], ["b.cy.js"]], header=["Failed spec"])
        r = run(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "2 unique failures")

    def test_missing_file_exits_nonzero(self):
        r = run(Path(self.tmp.name) / "nope.xlsx")
        self.assertNotEqual(r.returncode, 0)

    def test_requires_exactly_one_argument(self):
        r = subprocess.run([sys.executable, str(SCRIPT)],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage", r.stderr)


if __name__ == "__main__":
    unittest.main()
