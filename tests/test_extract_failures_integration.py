"""Integration test for extract_failures.main()'s multi-attempt aggregation,
with glab/network calls mocked. Verifies:
  - a real bug in an earlier attempt is not masked by a flaky glitch in the
    latest attempt (still-failing specs);
  - a spec that PASSED on retry is still captured (from its FIRST failed
    attempt) with its error + cypress link, flagged passed_on_retry.
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import extract_failures as ef  # noqa: E402
from fixtures import gitlab_line, build_log  # noqa: E402

# foo.cy.js (job 1/8): older attempt fails at 8.2 with a deep-equal data
# mismatch (real bug); newer attempt fails earlier at 2.1 with a cy.click
# glitch, masking the bug if only the latest is read.
OLDER_TRACE = build_log(
    gitlab_line("  Run URL:        https://cloud.cypress.io/projects/aa/runs/100"),
    gitlab_line("  Running:  foo.cy.js"),
    gitlab_line("  1) 8.2 Validate data consistency"),
    gitlab_line("     AssertionError: expected { Object } to deeply equal { Object }"),
)
NEWER_TRACE = build_log(
    gitlab_line("  Run URL:        https://cloud.cypress.io/projects/aa/runs/200"),
    gitlab_line("  Running:  foo.cy.js"),
    gitlab_line("  1) 2.1 Setup protocol and coordinates"),
    gitlab_line("     CypressError: `cy.click()` failed because this element is hidden from view:"),
)
# bar.cy.js (job 2/8): fails in the older attempt, passes in the latest -> flaky.
FLAKY_FAIL_TRACE = build_log(
    gitlab_line("  Run URL:        https://cloud.cypress.io/projects/aa/runs/300"),
    gitlab_line("  Running:  bar.cy.js"),
    gitlab_line("  1) some flaky step"),
    gitlab_line("     CypressError: `cy.click()` failed because this element is hidden from view:"),
)

ATTEMPTS = {
    "cypress-run 1/8": [
        {"id": 100, "name": "cypress-run 1/8", "status": "failed", "created_at": "2026-07-22T14:39:00Z"},
        {"id": 200, "name": "cypress-run 1/8", "status": "failed", "created_at": "2026-07-22T18:54:00Z"},
    ],
    "cypress-run 2/8": [
        {"id": 300, "name": "cypress-run 2/8", "status": "failed", "created_at": "2026-07-22T14:40:00Z"},
        {"id": 400, "name": "cypress-run 2/8", "status": "success", "created_at": "2026-07-22T18:55:00Z"},
    ],
}
TRACES = {100: OLDER_TRACE, 200: NEWER_TRACE, 300: FLAKY_FAIL_TRACE, 400: ""}


def fake_glab(path):
    m = re.search(r"/jobs/(\d+)/trace", path)
    if m:
        return TRACES[int(m.group(1))]
    return "{}"


class MultiAttemptTests(unittest.TestCase):
    def run_main(self):
        tmp = tempfile.mkdtemp()
        out = Path(tmp) / "failures_raw_1.json"
        with patch.object(sys, "argv", ["extract_failures.py", "2697519929", "-o", str(out)]), \
             patch.object(ef, "cypress_job_attempts", lambda p, pid: ATTEMPTS), \
             patch.object(ef, "glab", fake_glab), \
             patch.object(ef, "fetch_pipeline_info", lambda p, pid: {"sha": "abc", "web_url": "u"}):
            with contextlib.redirect_stderr(io.StringIO()):
                ef.main()
        data = json.loads(out.read_text())
        os.remove(out)
        return {r["spec"]: r for r in data["specs"]}

    def test_earlier_bug_not_masked_by_latest_glitch(self):
        rec = self.run_main()["foo.cy.js"]
        self.assertEqual(rec["error_kind"], "value-mismatch")
        self.assertEqual(rec["first_test"], "8.2 Validate data consistency")
        self.assertEqual(rec["job_id"], 100)  # the attempt that exposed the bug
        self.assertIn("deeply equal", rec["first_error"])
        self.assertIn("cy.click", rec["latest_attempt_error"])
        self.assertEqual(rec["latest_attempt_job_id"], 200)
        self.assertFalse(rec["passed_on_retry"])
        self.assertEqual(rec["cypress_run_url"], "https://cloud.cypress.io/projects/aa/runs/100")

    def test_flaky_spec_captured_from_first_failed_attempt(self):
        specs = self.run_main()
        self.assertIn("bar.cy.js", specs)  # not dropped just because it passed on retry
        rec = specs["bar.cy.js"]
        self.assertTrue(rec["passed_on_retry"])
        self.assertEqual(rec["job_id"], 300)  # the FIRST failed attempt
        self.assertIn("cy.click", rec["first_error"])
        self.assertEqual(rec["cypress_run_url"], "https://cloud.cypress.io/projects/aa/runs/300")


ABORT_TRACE = build_log(
    gitlab_line("→ Running 2 offline specs"),
    gitlab_line("[FAILED] [15:01:15] Cypress verification timed out."),
    gitlab_line("ERROR: Job failed: exit code 1"),
)
OFFLINE_ATTEMPTS = {
    "cypress-offline-child-3: [pair] [node-3]": [
        {"id": 731, "name": "cypress-offline-child-3: [pair]", "status": "failed",
         "created_at": "2026-09-24T15:00:00Z", "_offline_node": "3"},
    ],
    "cypress-offline-child [node-2]": [
        {"id": 721, "name": "cypress-offline-child", "status": "failed",
         "created_at": "2026-09-24T15:00:00Z", "_offline_node": "2"},
    ],
}


class NoSpecsRanExtractTests(unittest.TestCase):
    """Jobs that died before their first spec must still get failure records,
    or the sheet's rows come out UNCLASSIFIED."""

    def run_main(self):
        tmp = tempfile.mkdtemp()
        out = Path(tmp) / "failures_raw_1.json"
        planned = {("3", "pair"): ["test/cypress/integration/offline/a.cy.js",
                                   "test/cypress/integration/offline/b.cy.js"],
                   ("2", ""): []}
        with patch.object(sys, "argv", ["extract_failures.py", "555", "-o", str(out)]), \
             patch.object(ef, "cypress_job_attempts", lambda p, pid: OFFLINE_ATTEMPTS), \
             patch.object(ef, "glab", lambda path: ABORT_TRACE), \
             patch.object(ef.pfs, "planned_offline_specs",
                          lambda proj, sha, node, group: planned[(node, group)]), \
             patch.object(ef, "fetch_pipeline_info", lambda p, pid: {"sha": "sha1", "web_url": "u"}):
            with contextlib.redirect_stderr(io.StringIO()):
                ef.main()
        data = json.loads(out.read_text())
        os.remove(out)
        return {r["spec"]: r for r in data["specs"]}

    def test_planned_specs_get_job_aborted_records(self):
        specs = self.run_main()
        for spec in ("a.cy.js", "b.cy.js"):
            rec = specs[spec]
            self.assertEqual(rec["error_kind"], "job-aborted")
            self.assertEqual(rec["first_error"], "Cypress verification timed out.")
            self.assertEqual(rec["job_id"], 731)
            self.assertEqual(rec["spec_path"], f"test/cypress/integration/offline/{spec}")

    def test_unresolvable_job_uses_same_key_as_sheet(self):
        specs = self.run_main()
        self.assertIn("cypress-offline-child node-2 (no specs ran)", specs)


class CypressJobAttemptsChildPipelineTests(unittest.TestCase):
    """Offline specs run in child pipelines; classification must see them, and
    same-named child jobs on different nodes must not be merged as retries."""

    def test_follows_bridges_and_keys_by_node(self):
        def fake(path):
            if "/pipelines/555/jobs" in path:
                return json.dumps([{"id": 1, "name": "cypress-run 1/8", "status": "success",
                                    "created_at": "2026-09-24T14:00:00Z"}])
            if "/pipelines/555/bridges" in path:
                return json.dumps([
                    {"name": "cypress-offline-node-1", "downstream_pipeline": {"id": 71}},
                    {"name": "cypress-offline-node-2", "downstream_pipeline": {"id": 72}},
                    {"name": "cypress-offline-node-4", "downstream_pipeline": None},
                ])
            m = re.search(r"/pipelines/(7\d)/jobs", path)
            if m:
                return json.dumps([
                    {"id": int(m.group(1)) * 10, "name": "cypress-offline-child", "status": "failed",
                     "created_at": "2026-09-24T15:00:00Z"},
                    {"id": int(m.group(1)) * 10 + 1, "name": "deploy-offline-stack", "status": "success",
                     "created_at": "2026-09-24T14:59:00Z"},
                ])
            raise AssertionError(path)

        with patch.object(ef, "glab", fake):
            got = ef.cypress_job_attempts("group/proj", "555")
        self.assertEqual(set(got), {"cypress-run 1/8", "cypress-offline-child [node-1]",
                                    "cypress-offline-child [node-2]"})
        self.assertEqual(got["cypress-offline-child [node-2]"][0]["_offline_node"], "2")

    def test_follows_retried_bridges(self):
        """A manually re-run bridge gets a NEW child pipeline; GitLab only lists
        the old bridge (and its failed child) with include_retried=true. Both
        children's jobs must land under the same node key, oldest first."""
        def fake(path):
            if "/pipelines/555/jobs" in path:
                return json.dumps([])
            if "/pipelines/555/bridges" in path:
                latest = [{"name": "cypress-offline-node-1", "downstream_pipeline": {"id": 72}}]
                if "include_retried=true" not in path:
                    return json.dumps(latest)
                return json.dumps(latest + [
                    {"name": "cypress-offline-node-1", "downstream_pipeline": {"id": 71}}])
            if "/pipelines/71/jobs" in path:
                return json.dumps([{"id": 710, "name": "cypress-offline-child", "status": "failed",
                                    "created_at": "2026-10-08T08:37:23Z"}])
            if "/pipelines/72/jobs" in path:
                return json.dumps([{"id": 720, "name": "cypress-offline-child", "status": "success",
                                    "created_at": "2026-10-08T20:40:10Z"}])
            raise AssertionError(path)

        with patch.object(ef, "glab", fake):
            got = ef.cypress_job_attempts("group/proj", "555")
        self.assertEqual([j["id"] for j in got["cypress-offline-child [node-1]"]], [710, 720])


if __name__ == "__main__":
    unittest.main()
