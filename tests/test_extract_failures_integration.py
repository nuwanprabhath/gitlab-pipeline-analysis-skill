"""Integration test for extract_failures.main()'s multi-attempt aggregation,
with glab/network calls mocked. Verifies a real bug in an earlier attempt is
not masked by a flaky glitch in the latest attempt.
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

# Two attempts of one job. Older attempt: spec fails at 8.2 with a deep-equal
# data mismatch (real bug). Newer attempt: fails earlier at 2.1 with a cy.click
# glitch, never reaching 8.2 (masks the bug if only the latest is read).
OLDER_TRACE = build_log(
    gitlab_line("  Running:  foo.cy.js"),
    gitlab_line("  1) 8.2 Validate data consistency"),
    gitlab_line("     AssertionError: expected { Object } to deeply equal { Object }"),
)
NEWER_TRACE = build_log(
    gitlab_line("  Running:  foo.cy.js"),
    gitlab_line("  1) 2.1 Setup protocol and coordinates"),
    gitlab_line("     CypressError: `cy.click()` failed because this element is hidden from view:"),
)

ATTEMPTS = {
    "cypress-run 1/8": [
        {"id": 100, "name": "cypress-run 1/8", "status": "failed", "created_at": "2026-07-22T14:39:00Z"},
        {"id": 200, "name": "cypress-run 1/8", "status": "failed", "created_at": "2026-07-22T18:54:00Z"},
    ],
    # a job whose latest attempt passed -> flaky, must be skipped entirely
    "cypress-run 2/8": [
        {"id": 300, "name": "cypress-run 2/8", "status": "failed", "created_at": "2026-07-22T14:40:00Z"},
        {"id": 400, "name": "cypress-run 2/8", "status": "success", "created_at": "2026-07-22T18:55:00Z"},
    ],
}
TRACES = {100: OLDER_TRACE, 200: NEWER_TRACE, 300: OLDER_TRACE, 400: ""}


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
        specs = self.run_main()
        rec = specs["foo.cy.js"]
        self.assertEqual(rec["error_kind"], "value-mismatch")
        self.assertEqual(rec["first_test"], "8.2 Validate data consistency")
        self.assertEqual(rec["job_id"], 100)  # the attempt that exposed the bug
        self.assertIn("deeply equal", rec["first_error"])
        # breadcrumb of what the latest attempt showed instead
        self.assertIn("cy.click", rec["latest_attempt_error"])
        self.assertEqual(rec["latest_attempt_job_id"], 200)

    def test_flaky_job_passed_on_latest_is_excluded(self):
        specs = self.run_main()
        # cypress-run 2/8's latest attempt (400) passed -> its spec is not reported
        # (only foo.cy.js from job 1/8 remains; job 2/8 also ran foo but is skipped)
        self.assertEqual(set(specs), {"foo.cy.js"})
        self.assertEqual(specs["foo.cy.js"]["job_id"], 100)


if __name__ == "__main__":
    unittest.main()
