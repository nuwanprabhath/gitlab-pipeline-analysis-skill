import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import extract_failures as ef  # noqa: E402

from fixtures import gitlab_line, build_log  # noqa: E402


class ParsePipelineIdTests(unittest.TestCase):
    def test_numeric_id(self):
        self.assertEqual(ef.parse_pipeline_id("2640757838"), "2640757838")

    def test_full_pipeline_url(self):
        url = "https://gitlab.com/ternandsparrow/paratoo-fdcp/-/pipelines/2466892610"
        self.assertEqual(ef.parse_pipeline_id(url), "2466892610")

    def test_invalid_input_raises(self):
        with self.assertRaises(SystemExit):
            ef.parse_pipeline_id("not-a-pipeline-id")


class CleanTests(unittest.TestCase):
    def test_strips_ansi_and_gitlab_prefix(self):
        raw = gitlab_line("\x1b[90mRunning:  foo.cy.js\x1b[39m")
        self.assertEqual(ef.clean(raw), "Running:  foo.cy.js")


class ParseSpecFailuresTests(unittest.TestCase):
    def test_captures_first_test_and_first_error(self):
        log = build_log(
            gitlab_line("  Running:  foo.cy.js                                   (1 of 1)"),
            gitlab_line("  1) Some Suite"),
            gitlab_line("       do the thing"),
            gitlab_line("     AssertionError: Timed out retrying after 30000ms: Expected to find element"),
        )
        result = ef.parse_spec_failures(log, job_id=123, job_name="cypress-run 1/1")
        self.assertIn("foo.cy.js", result)
        rec = result["foo.cy.js"]
        self.assertEqual(rec["job_id"], 123)
        self.assertEqual(rec["job_name"], "cypress-run 1/1")
        self.assertEqual(rec["first_test"], "Some Suite")
        self.assertIn("AssertionError", rec["first_error"])
        self.assertEqual(rec["signatures"], [rec["first_error"]])

    def test_collects_multiple_distinct_signatures(self):
        log = build_log(
            gitlab_line("  Running:  foo.cy.js"),
            gitlab_line("  1) first test"),
            gitlab_line("     AssertionError: expected 1 to equal 2"),
            gitlab_line("  2) second test"),
            gitlab_line("     TypeError: Cannot read properties of undefined (reading 'x')"),
        )
        result = ef.parse_spec_failures(log, job_id=1, job_name="job")
        rec = result["foo.cy.js"]
        self.assertEqual(len(rec["signatures"]), 2)
        self.assertTrue(any("expected 1 to equal 2" in s for s in rec["signatures"]))
        self.assertTrue(any("Cannot read" in s for s in rec["signatures"]))

    def test_no_error_line_leaves_first_error_none_but_no_crash(self):
        log = build_log(
            gitlab_line("  Running:  foo.cy.js"),
            gitlab_line("  1) a test with no captured error line"),
            gitlab_line("     (nothing matching ERR_RE here)"),
        )
        result = ef.parse_spec_failures(log, job_id=1, job_name="job")
        rec = result["foo.cy.js"]
        self.assertIsNone(rec["first_error"])
        self.assertEqual(rec["signatures"], [])

    def test_multiple_specs_are_scoped_independently(self):
        log = build_log(
            gitlab_line("  Running:  foo.cy.js"),
            gitlab_line("  1) foo test"),
            gitlab_line("     AssertionError: foo failed"),
            gitlab_line("  Running:  bar.cy.js"),
            gitlab_line("  1) bar test"),
            gitlab_line("     AssertionError: bar failed"),
        )
        result = ef.parse_spec_failures(log, job_id=1, job_name="job")
        self.assertEqual(set(result.keys()), {"foo.cy.js", "bar.cy.js"})
        self.assertIn("foo failed", result["foo.cy.js"]["first_error"])
        self.assertIn("bar failed", result["bar.cy.js"]["first_error"])


if __name__ == "__main__":
    unittest.main()
