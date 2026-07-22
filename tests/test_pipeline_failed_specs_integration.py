"""End-to-end test of main() with glab network calls mocked out.

Covers the logic that only lives inside main() and isn't reachable through
the pure helper functions: retry-attempt matching ("Passed on retry"),
first-failed-job-url lookup, and the pipeline-suffixed default filenames.
"""
import contextlib
import csv
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pipeline_failed_specs as pfs  # noqa: E402

from fixtures import gitlab_line, build_log  # noqa: E402

RUN_A = "test/cypress/integration/run/a.cy.js"
RUN_B = "test/cypress/integration/run/b.cy.js"

FIRST_ATTEMPT_TRACE = build_log(
    gitlab_line(f"[SPEC START] {RUN_A}"),
    gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✖ FAILED"),
    gitlab_line(f"[SPEC START] {RUN_B}"),
    gitlab_line("Running after_script"),  # crashed mid-spec, no [SPEC END]
)

RETRY_ATTEMPT_TRACE = build_log(
    gitlab_line(f"[SPEC START] {RUN_A}"),
    gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✔ PASSED"),
)

JOBS = [
    {
        "id": 100,
        "name": "cypress-run 1/2",
        "status": "failed",
        "created_at": "2026-06-30T16:00:00.000Z",
    },
    {
        "id": 200,
        "name": "cypress-run 1/2",
        "status": "success",
        "created_at": "2026-06-30T17:00:00.000Z",
    },
]

TRACES = {100: FIRST_ATTEMPT_TRACE, 200: RETRY_ATTEMPT_TRACE}


def fake_fetch_all_jobs(project, pipeline_id):
    return JOBS


def fake_fetch_job_trace(project, job_id):
    return TRACES[job_id]


class MainIntegrationTests(unittest.TestCase):
    def run_main_in_tmpdir(self, argv_tail):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with patch.object(sys, "argv", ["pipeline_failed_specs.py", *argv_tail]), \
             patch.object(pfs, "fetch_all_jobs", fake_fetch_all_jobs), \
             patch.object(pfs, "fetch_job_trace", fake_fetch_job_trace), \
             patch.object(pfs, "CYPRESS_INTEGRATION_DIR", None):
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()):
                    pfs.main()
            finally:
                os.chdir(cwd)
        return Path(tmp)

    def test_default_filenames_include_pipeline_id(self):
        tmp = self.run_main_in_tmpdir(["999888777"])
        self.assertTrue((tmp / "failed_specs_999888777.csv").exists())
        self.assertTrue((tmp / "failed_specs_unique_999888777.csv").exists())

    def test_unique_csv_column_order(self):
        """The unique CSV ships all six columns in a fixed order, with empty
        placeholders for the two annotate-filled columns."""
        tmp = self.run_main_in_tmpdir(["999888777"])
        with open(tmp / "failed_specs_unique_999888777.csv", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
        self.assertEqual(
            header,
            [
                "Failed spec", "Passed on retry", "New failure", "bug_likelihood_(AI)",
                "Note", "failure_cause", "first_failed_job_url",
            ],
        )

    def test_retry_detection_and_missing_output_note(self):
        tmp = self.run_main_in_tmpdir(["999888777"])
        with open(tmp / "failed_specs_unique_999888777.csv", newline="") as fh:
            rows = {r["Failed spec"]: r for r in csv.DictReader(fh)}

        # a.cy.js failed on attempt #100, passed on the later attempt #200
        self.assertIn("yes", rows["a.cy.js"]["Passed on retry"])
        self.assertIn("#200", rows["a.cy.js"]["Passed on retry"])
        self.assertEqual(
            rows["a.cy.js"]["first_failed_job_url"],
            "https://gitlab.com/ternandsparrow/paratoo-fdcp/-/jobs/100",
        )
        self.assertEqual(rows["a.cy.js"]["Note"], "")

        # b.cy.js crashed mid-spec on attempt #100 - unknown outcome, flagged
        self.assertEqual(rows["b.cy.js"]["Note"], pfs.MISSING_OUTPUT_NOTE)

        # New failure defaults to N/A until a comparison step runs
        self.assertEqual(rows["a.cy.js"]["New failure"], "N/A")
        self.assertEqual(rows["b.cy.js"]["New failure"], "N/A")

    def test_custom_output_paths_override_defaults(self):
        tmp = self.run_main_in_tmpdir(
            ["999888777", "-o", "custom.csv", "-u", "custom_unique.csv"]
        )
        self.assertTrue((tmp / "custom.csv").exists())
        self.assertTrue((tmp / "custom_unique.csv").exists())
        self.assertFalse((tmp / "failed_specs_999888777.csv").exists())


if __name__ == "__main__":
    unittest.main()
