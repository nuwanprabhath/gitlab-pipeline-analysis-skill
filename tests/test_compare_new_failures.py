import csv
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import compare_new_failures as cnf  # noqa: E402

SCRIPT = SCRIPTS_DIR / "compare_new_failures.py"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


class ReadFailedSpecsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_reads_failed_spec_column_by_name(self):
        p = Path(self.tmp.name) / "prev.csv"
        # "Failed spec" not in first position — must still be found by name
        write_csv(p, ["idx", "Failed spec", "other"], [["1", "a.cy.js", "x"], ["2", "b.cy.js", "y"]])
        self.assertEqual(cnf.read_failed_specs(p), {"a.cy.js", "b.cy.js"})

    def test_ignores_blank_rows_and_specs(self):
        p = Path(self.tmp.name) / "prev.csv"
        write_csv(p, ["Failed spec"], [["a.cy.js"], [""], ["  "]])
        self.assertEqual(cnf.read_failed_specs(p), {"a.cy.js"})


class MarkNewFailuresTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.current = Path(self.tmp.name) / "failed_specs_unique_222.csv"
        # Canonical 7-column layout with New failure defaulting to N/A
        write_csv(
            self.current,
            ["Failed spec", "Passed on retry", "New failure", "bug_likelihood_(AI)",
             "Note", "failure_cause", "first_failed_job_url"],
            [
                ["a.cy.js", "no", "N/A", "", "", "", "url1"],
                ["b.cy.js", "no", "N/A", "", "", "", "url2"],
            ],
        )

    def test_yes_for_new_no_for_preexisting(self):
        # previous run had only b.cy.js -> a is new, b is pre-existing
        cnf.mark_new_failures(self.current, previous_specs={"b.cy.js"})
        rows = {r["Failed spec"]: r for r in read_csv(self.current)}
        self.assertEqual(rows["a.cy.js"]["New failure"], "yes")
        self.assertEqual(rows["b.cy.js"]["New failure"], "no")

    def test_none_previous_marks_all_na(self):
        cnf.mark_new_failures(self.current, previous_specs=None)
        rows = read_csv(self.current)
        self.assertTrue(all(r["New failure"] == "N/A" for r in rows))

    def test_inserts_column_at_third_position_when_absent(self):
        # A CSV WITHOUT the New failure column (e.g. an older/hand-made file)
        minimal = Path(self.tmp.name) / "failed_specs_unique_333.csv"
        write_csv(minimal, ["Failed spec", "Passed on retry", "first_failed_job_url"],
                  [["a.cy.js", "no", "url1"]])
        cnf.mark_new_failures(minimal, previous_specs=set())
        with open(minimal, newline="") as fh:
            header = next(csv.reader(fh))
        self.assertEqual(header[2], "New failure")
        self.assertEqual(
            header, ["Failed spec", "Passed on retry", "New failure", "first_failed_job_url"]
        )

    def test_survives_previous_with_different_columns(self):
        # previous CSV has a totally different shape but still a Failed spec col
        prev = Path(self.tmp.name) / "failed_specs_unique_111.csv"
        write_csv(prev, ["Failed spec", "some_old_column"], [["a.cy.js", "whatever"]])
        cnf.mark_new_failures(self.current, previous_specs=cnf.read_failed_specs(prev))
        rows = {r["Failed spec"]: r for r in read_csv(self.current)}
        self.assertEqual(rows["a.cy.js"]["New failure"], "no")   # in previous
        self.assertEqual(rows["b.cy.js"]["New failure"], "yes")  # not in previous


class FindPreviousTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _touch(self, name):
        p = Path(self.tmp.name) / name
        write_csv(p, ["Failed spec"], [["a.cy.js"]])
        return p

    def test_returns_none_when_only_current_exists(self):
        cur = self._touch("failed_specs_unique_999.csv")
        self.assertIsNone(cnf.find_previous_unique_csv(cur))

    def test_picks_most_recent_other_file(self):
        older = self._touch("failed_specs_unique_100.csv")
        time.sleep(0.01)
        newer = self._touch("failed_specs_unique_200.csv")
        time.sleep(0.01)
        cur = self._touch("failed_specs_unique_999.csv")
        # bump mtimes explicitly so ordering is unambiguous across filesystems
        os.utime(older, (1000, 1000))
        os.utime(newer, (2000, 2000))
        found = cnf.find_previous_unique_csv(cur)
        self.assertEqual(found.name, newer.name)

    def test_excludes_non_unique_csvs(self):
        # a per-job CSV (different prefix) must not be treated as a previous run
        self._touch_named("failed_specs_500.csv")
        cur = self._touch("failed_specs_unique_999.csv")
        self.assertIsNone(cnf.find_previous_unique_csv(cur))

    def _touch_named(self, name):
        p = Path(self.tmp.name) / name
        write_csv(p, ["Failed spec"], [["a.cy.js"]])
        return p


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _unique(self, name, specs):
        p = Path(self.tmp.name) / name
        write_csv(
            p,
            ["Failed spec", "Passed on retry", "New failure", "bug_likelihood_(AI)",
             "Note", "failure_cause", "first_failed_job_url"],
            [[s, "no", "N/A", "", "", "", "url"] for s in specs],
        )
        return p

    def test_detect_only_prints_previous_path(self):
        prev = self._unique("failed_specs_unique_100.csv", ["a.cy.js"])
        cur = self._unique("failed_specs_unique_200.csv", ["a.cy.js", "b.cy.js"])
        os.utime(prev, (1000, 1000))
        r = run_cli("--current", str(cur), "--detect-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(prev))

    def test_detect_only_prints_blank_when_no_previous(self):
        cur = self._unique("failed_specs_unique_200.csv", ["a.cy.js"])
        r = run_cli("--current", str(cur), "--detect-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_full_run_with_explicit_previous(self):
        prev = self._unique("failed_specs_unique_100.csv", ["a.cy.js"])
        cur = self._unique("failed_specs_unique_200.csv", ["a.cy.js", "b.cy.js"])
        r = run_cli("--current", str(cur), "--previous", str(prev))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {row["Failed spec"]: row for row in read_csv(cur)}
        self.assertEqual(rows["a.cy.js"]["New failure"], "no")
        self.assertEqual(rows["b.cy.js"]["New failure"], "yes")

    def test_auto_detect_full_run(self):
        prev = self._unique("failed_specs_unique_100.csv", ["a.cy.js"])
        cur = self._unique("failed_specs_unique_200.csv", ["a.cy.js", "b.cy.js"])
        os.utime(prev, (1000, 1000))
        r = run_cli("--current", str(cur))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {row["Failed spec"]: row for row in read_csv(cur)}
        self.assertEqual(rows["b.cy.js"]["New failure"], "yes")

    def test_no_previous_flag_forces_na(self):
        prev = self._unique("failed_specs_unique_100.csv", ["a.cy.js"])
        cur = self._unique("failed_specs_unique_200.csv", ["a.cy.js", "b.cy.js"])
        os.utime(prev, (1000, 1000))
        r = run_cli("--current", str(cur), "--no-previous")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = read_csv(cur)
        self.assertTrue(all(row["New failure"] == "N/A" for row in rows))

    def test_missing_explicit_previous_errors(self):
        cur = self._unique("failed_specs_unique_200.csv", ["a.cy.js"])
        r = run_cli("--current", str(cur), "--previous", str(Path(self.tmp.name) / "nope.csv"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stderr)


if __name__ == "__main__":
    unittest.main()
