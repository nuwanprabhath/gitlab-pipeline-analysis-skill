import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pipeline_failed_specs as pfs  # noqa: E402

from fixtures import TS, gitlab_line, build_log  # noqa: E402

RUN_A = "test/cypress/integration/run/a.cy.js"
RUN_B = "test/cypress/integration/run/b.cy.js"
PRIORITY_C = "test/cypress/integration/priority/c.cy.js"


class ParsePipelineIdTests(unittest.TestCase):
    def test_numeric_id(self):
        self.assertEqual(pfs.parse_pipeline_id("2640757838"), "2640757838")

    def test_full_pipeline_url(self):
        url = "https://gitlab.com/ternandsparrow/paratoo-fdcp/-/pipelines/2466892610"
        self.assertEqual(pfs.parse_pipeline_id(url), "2466892610")

    def test_invalid_input_raises(self):
        with self.assertRaises(ValueError):
            pfs.parse_pipeline_id("not-a-pipeline-id")


class CleanLogTests(unittest.TestCase):
    def test_strips_ansi_and_gitlab_prefix(self):
        raw = gitlab_line("\x1b[90mRunning:  a.cy.js\x1b[39m")
        self.assertEqual(pfs.clean_log(raw), "Running:  a.cy.js")

    def test_leaves_content_without_prefix_untouched(self):
        self.assertEqual(pfs.clean_log("plain line, no prefix"), "plain line, no prefix")


class ParseSpecEventsTests(unittest.TestCase):
    def test_single_passed_spec(self):
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_A} | {TS} | local: 6/30/2026, 5:55:20 PM"),
            gitlab_line(f"[SPEC END]   {RUN_A} | {TS} | duration: 1m 2s | ✔ PASSED"),
        )
        order, status = pfs.parse_spec_events(log)
        self.assertEqual(order, [RUN_A])
        self.assertEqual(status, {RUN_A: "PASSED"})

    def test_single_failed_spec(self):
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_A}"),
            gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✖ FAILED"),
        )
        order, status = pfs.parse_spec_events(log)
        self.assertEqual(status, {RUN_A: "FAILED"})

    def test_multiple_specs_in_order(self):
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_A}"),
            gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✔ PASSED"),
            gitlab_line(f"[SPEC START] {RUN_B}"),
            gitlab_line(f"[SPEC END]   {RUN_B} | duration: 1m | ✖ FAILED"),
        )
        order, status = pfs.parse_spec_events(log)
        self.assertEqual(order, [RUN_A, RUN_B])
        self.assertEqual(status, {RUN_A: "PASSED", RUN_B: "FAILED"})

    def test_last_spec_crashed_mid_run_is_missing(self):
        """Job gets OOM-killed / times out while the last spec is running:
        [SPEC START] with no matching [SPEC END]."""
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_A}"),
            gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✔ PASSED"),
            gitlab_line(f"[SPEC START] {RUN_B}"),
            gitlab_line("Running after_script"),
        )
        order, status = pfs.parse_spec_events(log)
        self.assertEqual(order, [RUN_A, RUN_B])
        self.assertEqual(status, {RUN_A: "PASSED", RUN_B: "MISSING"})

    def test_no_spec_markers_returns_empty(self):
        log = build_log(gitlab_line("$ commitlint run"), gitlab_line("ERROR: Job failed"))
        order, status = pfs.parse_spec_events(log)
        self.assertEqual(order, [])
        self.assertEqual(status, {})


class ParseFailedSpecsTests(unittest.TestCase):
    def test_returns_only_failed_basenames_in_order(self):
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_A}"),
            gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✔ PASSED"),
            gitlab_line(f"[SPEC START] {PRIORITY_C}"),
            gitlab_line(f"[SPEC END]   {PRIORITY_C} | duration: 1m | ✖ FAILED"),
        )
        self.assertEqual(pfs.parse_failed_specs(log), ["c.cy.js"])

    def test_crashed_spec_is_not_counted_as_failed(self):
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_B}"),
            gitlab_line("Running after_script"),
        )
        self.assertEqual(pfs.parse_failed_specs(log), [])


class FindMissingOutputSpecsTests(unittest.TestCase):
    def test_flags_only_the_crashed_spec(self):
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_A}"),
            gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✔ PASSED"),
            gitlab_line(f"[SPEC START] {RUN_B}"),
            gitlab_line("Running after_script"),
        )
        self.assertEqual(pfs.find_missing_output_specs(log), ["b.cy.js"])

    def test_empty_when_job_finishes_cleanly(self):
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_A}"),
            gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✖ FAILED"),
        )
        self.assertEqual(pfs.find_missing_output_specs(log), [])


class ParseSpecFullPathsTests(unittest.TestCase):
    def test_maps_basename_to_full_path(self):
        log = build_log(
            gitlab_line(f"[SPEC START] {RUN_A}"),
            gitlab_line(f"[SPEC END]   {RUN_A} | duration: 1m | ✔ PASSED"),
            gitlab_line(f"[SPEC START] {PRIORITY_C}"),
            gitlab_line("Running after_script"),
        )
        self.assertEqual(
            pfs.parse_spec_full_paths(log), {"a.cy.js": RUN_A, "c.cy.js": PRIORITY_C}
        )


class ResolveSpecPathsTests(unittest.TestCase):
    def test_prefers_known_paths(self):
        resolved, unresolved = pfs.resolve_spec_paths(
            ["a.cy.js"], integration_dir=None, known_paths={"a.cy.js": RUN_A}
        )
        self.assertEqual(resolved, [RUN_A])
        self.assertEqual(unresolved, [])

    def test_falls_back_to_local_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            integration_dir = Path(tmp) / "test/cypress/integration"
            (integration_dir / "run").mkdir(parents=True)
            (integration_dir / "run" / "a.cy.js").write_text("")
            resolved, unresolved = pfs.resolve_spec_paths(
                ["a.cy.js"], integration_dir=integration_dir, known_paths={}
            )
            self.assertEqual(resolved, ["test/cypress/integration/run/a.cy.js"])
            self.assertEqual(unresolved, [])

    def test_no_checkout_and_no_known_path_falls_back_to_glob(self):
        resolved, unresolved = pfs.resolve_spec_paths(
            ["a.cy.js"], integration_dir=None, known_paths={}
        )
        self.assertEqual(resolved, ["test/cypress/integration/**/a.cy.js"])
        self.assertEqual(unresolved, [])

    def test_checkout_present_but_spec_missing_is_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            integration_dir = Path(tmp) / "test/cypress/integration"
            integration_dir.mkdir(parents=True)
            resolved, unresolved = pfs.resolve_spec_paths(
                ["missing.cy.js"], integration_dir=integration_dir, known_paths={}
            )
            self.assertEqual(resolved, [])
            self.assertEqual(unresolved, ["missing.cy.js"])


class BuildCypressCommandTests(unittest.TestCase):
    def test_empty_list_returns_none(self):
        self.assertIsNone(pfs.build_cypress_command([]))

    def test_joins_spec_paths(self):
        cmd = pfs.build_cypress_command([RUN_A, RUN_B])
        self.assertEqual(
            cmd, f'yarn cypress run --browser chrome --spec "{RUN_A},{RUN_B}"'
        )


if __name__ == "__main__":
    unittest.main()
