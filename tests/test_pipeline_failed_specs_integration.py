"""End-to-end test of pipeline_failed_specs.main() with glab calls mocked.

Covers the spec_runs model: all specs × all cypress attempts, chronological
first/second job & cypress columns, JOB CRASHED note, passed-on-retry, the
all_specs CSV, and the spec_runs sidecar.
"""
import contextlib
import csv
import io
import json
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

RUN_A = "test/cypress/integration/run/a.cy.js"   # fails attempt 1, passes attempt 2 (flaky)
RUN_B = "test/cypress/integration/run/b.cy.js"   # crashes attempt 1 (no [SPEC END])
RUN_C = "test/cypress/integration/run/c.cy.js"   # passes attempt 1 only (never failed)

FIRST_ATTEMPT = build_log(
    gitlab_line("  Run URL:  https://cloud.cypress.io/projects/aa/runs/100"),
    gitlab_line(f"[SPEC START] {RUN_A}"),
    gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✖ FAILED"),
    gitlab_line(f"[SPEC START] {RUN_C}"),
    gitlab_line(f"[SPEC END]   {RUN_C} | duration: 1m | ✔ PASSED"),
    gitlab_line(f"[SPEC START] {RUN_B}"),
    gitlab_line("Running after_script"),  # crashed mid-spec, no [SPEC END]
)
RETRY_ATTEMPT = build_log(
    gitlab_line("  Run URL:  https://cloud.cypress.io/projects/aa/runs/200"),
    gitlab_line(f"[SPEC START] {RUN_A}"),
    gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✔ PASSED"),
    gitlab_line(f"[SPEC START] {RUN_B}"),
    gitlab_line(f"[SPEC END]   {RUN_B} | duration: 1m | ✖ FAILED"),
)

JOBS = [
    {"id": 100, "name": "cypress-run 1/2", "status": "failed", "created_at": "2026-06-30T16:00:00Z"},
    {"id": 200, "name": "cypress-run 1/2", "status": "failed", "created_at": "2026-06-30T17:00:00Z"},
    {"id": 300, "name": "commitlint", "status": "failed", "created_at": "2026-06-30T15:00:00Z"},
]
TRACES = {100: FIRST_ATTEMPT, 200: RETRY_ATTEMPT}


class MainIntegrationTests(unittest.TestCase):
    def run_main(self, argv_tail):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with patch.object(sys, "argv", ["pipeline_failed_specs.py", *argv_tail]), \
             patch.object(pfs, "fetch_all_jobs", lambda p, pid: JOBS), \
             patch.object(pfs, "fetch_job_trace", lambda p, jid: TRACES[jid]), \
             patch.object(pfs, "CYPRESS_INTEGRATION_DIR", None):
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    pfs.main()
            finally:
                os.chdir(cwd)
        return Path(tmp)

    def unique_rows(self, tmp, pid="999888777"):
        with open(tmp / f"failed_specs_unique_{pid}.csv", newline="") as fh:
            return {r["Failed spec"]: r for r in csv.DictReader(fh)}

    def test_default_filenames_and_sidecar(self):
        tmp = self.run_main(["999888777"])
        self.assertTrue((tmp / "all_specs_999888777.csv").exists())
        self.assertTrue((tmp / "failed_specs_unique_999888777.csv").exists())
        self.assertTrue((tmp / "spec_runs_999888777.json").exists())

    def test_unique_csv_column_order(self):
        tmp = self.run_main(["999888777"])
        with open(tmp / "failed_specs_unique_999888777.csv", newline="") as fh:
            header = next(csv.reader(fh))
        self.assertEqual(
            header,
            [
                "Failed spec", "Passed on retry", "New failure", "bug_likelihood_(AI)",
                "Note", "Locally reproducible", "failure_cause",
                "first_cypress_url", "second_cypress_url", "first_job_url", "second_job_url",
            ],
        )

    def test_chronological_jobs_and_crash_note(self):
        rows = self.unique_rows(self.run_main(["999888777"]))
        # a.cy.js: failed attempt 100, passed attempt 200 -> flaky, both jobs listed
        a = rows["a.cy.js"]
        self.assertTrue(a["Passed on retry"].startswith("yes"))
        self.assertIn("#200", a["Passed on retry"])
        self.assertTrue(a["first_job_url"].endswith("/jobs/100"))
        self.assertTrue(a["second_job_url"].endswith("/jobs/200"))
        self.assertTrue(a["first_cypress_url"].endswith("/runs/100"))
        self.assertEqual(a["Note"], "")
        # b.cy.js: crashed attempt 100 -> JOB CRASHED
        self.assertEqual(rows["b.cy.js"]["Note"], "JOB CRASHED")
        self.assertEqual(pfs.MISSING_OUTPUT_NOTE, "JOB CRASHED")

    def test_all_specs_includes_passing_spec(self):
        tmp = self.run_main(["999888777"])
        with open(tmp / "all_specs_999888777.csv", newline="") as fh:
            specs = {r["Spec"]: r for r in csv.DictReader(fh)}
        # c.cy.js only ever passed, so it's absent from the failed sheet but
        # present in all_specs
        self.assertIn("c.cy.js", specs)
        self.assertNotIn("c.cy.js", self.unique_rows(tmp))
        self.assertTrue(specs["c.cy.js"]["first_job_url"].endswith("/jobs/100"))

    def test_sidecar_has_per_attempt_status(self):
        tmp = self.run_main(["999888777"])
        sr = json.loads((tmp / "spec_runs_999888777.json").read_text())
        statuses = [r["status"] for r in sr["a.cy.js"]["runs"]]
        self.assertEqual(statuses, ["FAILED", "PASSED"])
        self.assertEqual(sr["b.cy.js"]["runs"][0]["status"], "MISSING")

    def test_non_cypress_jobs_ignored(self):
        # commitlint (non-cypress) must not appear as a spec
        tmp = self.run_main(["999888777"])
        with open(tmp / "all_specs_999888777.csv", newline="") as fh:
            specs = [r["Spec"] for r in csv.DictReader(fh)]
        self.assertEqual(set(specs), {"a.cy.js", "b.cy.js", "c.cy.js"})


if __name__ == "__main__":
    unittest.main()
