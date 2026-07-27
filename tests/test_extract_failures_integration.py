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


if __name__ == "__main__":
    unittest.main()
