import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "annotate_failure_cause.py"


def run_annotate(*args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )
    return result


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


class AnnotateFailureCauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv_path = Path(self.tmp.name) / "failed_specs_unique.csv"
        self.mapping_path = Path(self.tmp.name) / "mapping.json"
        with open(self.csv_path, "w", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["Failed spec", "Passed on retry", "first_failed_job_url", "Note"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "Failed spec": "a.cy.js",
                    "Passed on retry": "no",
                    "first_failed_job_url": "https://gitlab.com/x/-/jobs/1",
                    "Note": "",
                }
            )
            writer.writerow(
                {
                    "Failed spec": "b.cy.js",
                    "Passed on retry": "yes (2) (#2)",
                    "first_failed_job_url": "https://gitlab.com/x/-/jobs/2",
                    "Note": "",
                }
            )

    def write_mapping(self, mapping):
        with open(self.mapping_path, "w") as fh:
            json.dump(mapping, fh)

    def test_adds_failure_cause_column(self):
        self.write_mapping({"a.cy.js": "dropdown timeout", "b.cy.js": "flaky (passed on retry)"})
        result = run_annotate("--mapping", str(self.mapping_path), "--csv", str(self.csv_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = read_csv(self.csv_path)
        self.assertEqual(rows[0]["failure_cause"], "dropdown timeout")
        self.assertEqual(rows[1]["failure_cause"], "flaky (passed on retry)")

    def test_missing_mapping_entry_falls_back_to_default(self):
        self.write_mapping({"a.cy.js": "dropdown timeout"})
        result = run_annotate("--mapping", str(self.mapping_path), "--csv", str(self.csv_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = read_csv(self.csv_path)
        self.assertEqual(rows[1]["failure_cause"], "UNCLASSIFIED")
        self.assertIn("b.cy.js", result.stderr)

    def test_custom_default_label(self):
        self.write_mapping({})
        result = run_annotate(
            "--mapping", str(self.mapping_path), "--csv", str(self.csv_path),
            "--default", "TODO",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = read_csv(self.csv_path)
        self.assertTrue(all(r["failure_cause"] == "TODO" for r in rows))

    def test_rerun_refreshes_existing_column_in_place(self):
        self.write_mapping({"a.cy.js": "first label", "b.cy.js": "first label"})
        run_annotate("--mapping", str(self.mapping_path), "--csv", str(self.csv_path))
        self.write_mapping({"a.cy.js": "updated label", "b.cy.js": "first label"})
        result = run_annotate("--mapping", str(self.mapping_path), "--csv", str(self.csv_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = read_csv(self.csv_path)
        self.assertEqual(rows[0]["failure_cause"], "updated label")
        # Column shouldn't be duplicated on re-run
        self.assertEqual(
            list(rows[0].keys()),
            ["Failed spec", "Passed on retry", "first_failed_job_url", "Note", "failure_cause"],
        )

    def test_custom_output_path_leaves_source_untouched(self):
        self.write_mapping({"a.cy.js": "x", "b.cy.js": "y"})
        out_path = Path(self.tmp.name) / "annotated.csv"
        result = run_annotate(
            "--mapping", str(self.mapping_path), "--csv", str(self.csv_path),
            "-o", str(out_path),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out_path.exists())
        original_rows = read_csv(self.csv_path)
        self.assertNotIn("failure_cause", original_rows[0])


if __name__ == "__main__":
    unittest.main()
