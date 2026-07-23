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

    def test_captures_spec_line_from_stack_frame(self):
        log = build_log(
            gitlab_line("  Running:  plot-context.cy.js"),
            gitlab_line("  1) Check cannot edit"),
            gitlab_line("     AssertionError: Timed out retrying after 30000ms: expected false to be true"),
            gitlab_line("      at Context.eval (webpack://monitor-webapp/./node_modules/@quasar/quasar-app-extension-testing-e2e-cypress/dist/esm/commands/test-route.js:9:13)"),
            gitlab_line("      at Context.eval (webpack://monitor-webapp/./test/cypress/integration/priority/plot-context.cy.js:383:9)"),
        )
        result = ef.parse_spec_failures(log, job_id=1, job_name="job")
        rec = result["plot-context.cy.js"]
        # The quasar-extension frame is skipped; the spec's own frame wins
        self.assertEqual(rec["first_error_spec_line"], 383)

    def test_spec_line_none_when_no_spec_frame(self):
        log = build_log(
            gitlab_line("  Running:  foo.cy.js"),
            gitlab_line("  1) a test"),
            gitlab_line("     AssertionError: nope"),
        )
        result = ef.parse_spec_failures(log, job_id=1, job_name="job")
        self.assertIsNone(result["foo.cy.js"]["first_error_spec_line"])

    def test_captures_spec_path_from_spec_start_marker(self):
        log = build_log(
            gitlab_line("[SPEC START] test/cypress/integration/priority/foo.cy.js | ts | local"),
            gitlab_line("  Running:  foo.cy.js"),
            gitlab_line("  1) a test"),
            gitlab_line("     AssertionError: nope"),
        )
        result = ef.parse_spec_failures(log, job_id=1, job_name="job")
        self.assertEqual(
            result["foo.cy.js"]["spec_path"],
            "test/cypress/integration/priority/foo.cy.js",
        )


class ClassifyErrorKindTests(unittest.TestCase):
    def test_deep_equal_object_is_value_mismatch(self):
        self.assertEqual(
            ef.classify_error_kind(
                "AssertionError: expected { Object (plot-layout, ...) } to deeply equal { Object (...) }"
            ),
            "value-mismatch",
        )

    def test_count_and_text_mismatches_are_value_mismatch(self):
        self.assertEqual(ef.classify_error_kind("AssertionError: expected 144 to equal 96"), "value-mismatch")
        self.assertEqual(
            ef.classify_error_kind("expected 'Description 2' to equal 'Description 2 (Stratum: Upper Storey)'"),
            "value-mismatch",
        )
        self.assertEqual(ef.classify_error_kind("Not enough elements found. Found '2', expected '3'."), "value-mismatch")

    def test_app_error_states(self):
        self.assertEqual(ef.classify_error_kind("Error: Failed to publish opportunistic surveys. Value ... is not unique."), "app-error")
        self.assertEqual(ef.classify_error_kind("There were errors when checking resolved data: Unregistered model case project_name."), "app-error")

    def test_interaction_timeouts_are_element_timeout(self):
        self.assertEqual(
            ef.classify_error_kind("CypressError: Timed out retrying after 30050ms: `cy.click()` failed because the center of this element is hidden from view:"),
            "element-timeout",
        )
        self.assertEqual(ef.classify_error_kind("AssertionError: Timed out retrying: Expected to find element: `[data-cy=x]`, but never found it."), "element-timeout")
        self.assertEqual(ef.classify_error_kind("DropdownError: Expected to find option but found no matches."), "element-timeout")

    def test_none_and_unknown(self):
        self.assertEqual(ef.classify_error_kind(None), "other")
        self.assertEqual(ef.classify_error_kind("some unrecognised message"), "other")

    def test_record_gets_error_kind_and_value_mismatch_wins_over_earlier_interaction(self):
        # first_error is an interaction timeout, but a later signature is a data
        # mismatch — the record must surface value-mismatch (the bug signal).
        log = build_log(
            gitlab_line("  Running:  foo.cy.js"),
            gitlab_line("  1) first"),
            gitlab_line("     CypressError: `cy.click()` failed because the center of this element is hidden from view:"),
            gitlab_line("  2) second"),
            gitlab_line("     AssertionError: expected { Object } to deeply equal { Object }"),
        )
        rec = ef.parse_spec_failures(log, 1, "job")["foo.cy.js"]
        self.assertEqual(rec["error_kind"], "value-mismatch")

    def test_pure_interaction_spec_stays_element_timeout(self):
        log = build_log(
            gitlab_line("  Running:  foo.cy.js"),
            gitlab_line("  1) first"),
            gitlab_line("     AssertionError: Timed out retrying: Expected to find element: `[data-cy=x]`, but never found it."),
        )
        rec = ef.parse_spec_failures(log, 1, "job")["foo.cy.js"]
        self.assertEqual(rec["error_kind"], "element-timeout")


if __name__ == "__main__":
    unittest.main()
