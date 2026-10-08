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
             patch.object(pfs, "fetch_bridges", lambda p, pid: []), \
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


class GatherCypressJobsTests(unittest.TestCase):
    """gather_cypress_jobs must reach into downstream child pipelines (offline
    suite) and tag their jobs with the node index from the bridge name."""

    def test_offline_child_pipeline_jobs_are_collected_and_node_tagged(self):
        parent_jobs = [
            {"id": 100, "name": "cypress-run 1/2", "created_at": "2026-06-30T10:00:00Z"},
            {"id": 300, "name": "commitlint", "created_at": "2026-06-30T09:00:00Z"},
        ]
        child_jobs = {
            555: [{"id": 5551, "name": "cypress-offline-child", "created_at": "2026-06-30T11:00:00Z"},
                  {"id": 5552, "name": "deploy-offline-stack", "created_at": "2026-06-30T10:30:00Z"}],
        }
        bridges = [
            {"name": "cypress-offline-node-2", "downstream_pipeline": {"id": 555}},
            {"name": "some-skipped-bridge", "downstream_pipeline": None},  # never triggered
        ]

        def fake_all_jobs(project, pid):
            return parent_jobs if str(pid) == "999" else child_jobs.get(pid, [])

        with patch.object(pfs, "fetch_all_jobs", fake_all_jobs), \
             patch.object(pfs, "fetch_bridges", lambda p, pid: bridges):
            jobs = pfs.gather_cypress_jobs("proj", "999")

        by_id = {j["id"]: j for j in jobs}
        self.assertIn(100, by_id)            # parent cypress job kept
        self.assertNotIn(300, by_id)         # non-cypress parent job dropped
        self.assertIn(5551, by_id)           # child offline cypress job pulled in
        self.assertNotIn(5552, by_id)        # non-cypress child job dropped
        self.assertEqual(by_id[5551]["_offline_node"], "2")  # node from bridge name
        # sorted chronologically
        self.assertEqual([j["id"] for j in jobs], [100, 5551])

    def test_fetch_bridges_includes_retried(self):
        """Re-running a bridge replaces it in the default listing, hiding the
        failed child pipeline; include_retried=true keeps both."""
        seen = []
        with patch.object(pfs, "glab_paginated", lambda path: seen.append(path) or []):
            pfs.fetch_bridges("group/proj", "999")
        self.assertEqual(len(seen), 1)
        self.assertIn("include_retried=true", seen[0])

    def test_retried_bridge_children_are_both_gathered(self):
        parent_jobs = []
        child_jobs = {
            71: [{"id": 710, "name": "cypress-offline-child", "created_at": "2026-10-08T08:37:23Z"}],
            72: [{"id": 720, "name": "cypress-offline-child", "created_at": "2026-10-08T20:40:10Z"}],
        }
        bridges = [  # newest attempt first, as GitLab returns them
            {"name": "cypress-offline-node-1", "downstream_pipeline": {"id": 72}},
            {"name": "cypress-offline-node-1", "downstream_pipeline": {"id": 71}},
        ]

        def fake_all_jobs(project, pid):
            return parent_jobs if str(pid) == "999" else child_jobs.get(pid, [])

        with patch.object(pfs, "fetch_all_jobs", fake_all_jobs), \
             patch.object(pfs, "fetch_bridges", lambda p, pid: bridges):
            jobs = pfs.gather_cypress_jobs("proj", "999")
        self.assertEqual([j["id"] for j in jobs], [710, 720])
        self.assertEqual({j["_offline_node"] for j in jobs}, {"1"})


ABORTED_TRACE = build_log(
    gitlab_line("→ Running 1 offline specs"),
    gitlab_line("[FAILED] [15:01:15] Cypress verification timed out."),
    gitlab_line("Cypress verification timed out."),
    gitlab_line("→ Cypress exited with code: 1"),
    gitlab_line("ERROR: Job failed: exit code 1"),
)
OFFLINE_OK_TRACE = build_log(
    gitlab_line("[SPEC START] test/cypress/integration/offline/offline-pro.cy.js"),
    gitlab_line("[SPEC END]   test/cypress/integration/offline/offline-pro.cy.js | d: 1m | ✔ PASSED"),
)
PARENT_JOBS = [
    {"id": 10, "name": "cypress-priority 1/6", "status": "success", "created_at": "2026-09-24T14:00:00Z"},
]
BRIDGES = [
    {"name": "cypress-offline-node-1", "downstream_pipeline": {"id": 71}},
    {"name": "cypress-offline-node-3", "downstream_pipeline": {"id": 73}},
    {"name": "cypress-offline-node-5", "downstream_pipeline": {"id": 75}},
]
CHILD_JOBS = {
    71: [{"id": 711, "name": "cypress-offline-child", "status": "failed", "created_at": "2026-09-24T15:00:00Z"}],
    73: [{"id": 731, "name": "cypress-offline-child-3: [pair]", "status": "failed", "created_at": "2026-09-24T15:00:01Z"},
         {"id": 732, "name": "cypress-offline-child-3: [solo]", "status": "failed", "created_at": "2026-09-24T15:00:02Z"}],
    75: [{"id": 751, "name": "cypress-offline-child", "status": "success", "created_at": "2026-09-24T15:00:03Z"}],
}
OFFLINE_TRACES = {10: "", 711: ABORTED_TRACE, 731: ABORTED_TRACE, 732: ABORTED_TRACE, 751: OFFLINE_OK_TRACE}
PLANNED = {
    ("1", ""): ["test/cypress/integration/offline/offline-floristics.cy.js"],
    ("3", "pair"): ["test/cypress/integration/offline/a.cy.js", "test/cypress/integration/offline/b.cy.js"],
    ("3", "solo"): [],  # table lookup failed -> must still surface the job
}


class NoSpecsRanTests(unittest.TestCase):
    """A failed Cypress job that never reached its first spec must still show up.

    Regression: pipeline 2879264052 -- 7 offline child jobs died on "Cypress
    verification timed out." before any [SPEC START]; the report listed zero
    offline failures because rows were only ever created from spec markers.
    """

    def run_main(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def all_jobs(project, pid):
            return PARENT_JOBS if str(pid) == "555" else CHILD_JOBS[int(pid)]

        with patch.object(sys, "argv", ["pipeline_failed_specs.py", "555"]), \
             patch.object(pfs, "fetch_all_jobs", all_jobs), \
             patch.object(pfs, "fetch_bridges", lambda p, pid: BRIDGES), \
             patch.object(pfs, "fetch_job_trace", lambda p, jid: OFFLINE_TRACES[jid]), \
             patch.object(pfs, "fetch_pipeline_sha", lambda p, pid: "sha1"), \
             patch.object(pfs, "planned_offline_specs",
                          lambda proj, sha, node, group: PLANNED[(node, group)]), \
             patch.object(pfs, "CYPRESS_INTEGRATION_DIR", None):
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    pfs.main()
            finally:
                os.chdir(cwd)
        tmp = Path(tmp)
        with open(tmp / "failed_specs_unique_555.csv", newline="") as fh:
            rows = {r["Failed spec"]: r for r in csv.DictReader(fh)}
        sidecar = json.loads((tmp / "spec_runs_555.json").read_text())
        return rows, sidecar

    def test_planned_specs_become_failed_rows(self):
        rows, _ = self.run_main()
        for spec in ("offline-floristics.cy.js", "a.cy.js", "b.cy.js"):
            self.assertIn(spec, rows)
            self.assertEqual(rows[spec]["Note"], "NO SPECS RAN: Cypress verification timed out.")
            self.assertEqual(rows[spec]["Passed on retry"], "no")
        self.assertTrue(rows["a.cy.js"]["first_job_url"].endswith("/jobs/731"))

    def test_unresolvable_job_still_gets_a_row(self):
        rows, _ = self.run_main()
        key = "cypress-offline-child-3: [solo] node-3 (no specs ran)"
        self.assertIn(key, rows)
        self.assertTrue(rows[key]["Note"].startswith("NO SPECS RAN"))
        self.assertTrue(rows[key]["first_job_url"].endswith("/jobs/732"))

    def test_sidecar_marks_not_run_with_node(self):
        _, sidecar = self.run_main()
        run = sidecar["offline-floristics.cy.js"]["runs"][0]
        self.assertEqual(run["status"], "NOT_RUN")
        self.assertEqual(run["node"], "1")
        self.assertEqual(run["abort_reason"], "Cypress verification timed out.")

    def test_passing_offline_spec_is_not_a_failure(self):
        rows, sidecar = self.run_main()
        self.assertNotIn("offline-pro.cy.js", rows)
        self.assertEqual(sidecar["offline-pro.cy.js"]["runs"][0]["status"], "PASSED")


if __name__ == "__main__":
    unittest.main()
