import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import error_kind_enforce as eke  # noqa: E402

HEADER = [
    "Failed spec", "Passed on retry", "New failure", "bug_likelihood_(AI)",
    "Note", "failure_cause", "first_failed_job_url",
]


def row(spec, bug, cause):
    return [spec, "no", "no", bug, "", cause, "http://x/1"]


class EnforceTests(unittest.TestCase):
    def kinds(self, mapping):
        return {s: {"error_kind": k, "first_error": e} for s, (k, e) in mapping.items()}

    def test_value_mismatch_low_raised_to_medium(self):
        data = [row("a.cy.js", "LOW", "some cause")]
        corr = eke.enforce(HEADER, data, self.kinds({"a.cy.js": ("value-mismatch", "expected X to deeply equal Y")}))
        self.assertEqual(data[0][3], "MEDIUM")
        self.assertTrue(any("-> MEDIUM" in c for c in corr))

    def test_app_error_blank_raised_to_medium(self):
        data = [row("a.cy.js", "", "cause")]
        eke.enforce(HEADER, data, self.kinds({"a.cy.js": ("app-error", "Failed to publish")}))
        self.assertEqual(data[0][3], "MEDIUM")

    def test_high_is_left_untouched(self):
        data = [row("a.cy.js", "HIGH", "real bug cause")]
        corr = eke.enforce(HEADER, data, self.kinds({"a.cy.js": ("value-mismatch", "expected X to equal Y")}))
        self.assertEqual(data[0][3], "HIGH")
        self.assertEqual(corr, [])

    def test_element_timeout_glitch_not_touched(self):
        # genuine interaction glitch stays LOW — no false positive
        data = [row("a.cy.js", "LOW", "dropdown options never rendered")]
        corr = eke.enforce(HEADER, data, self.kinds({"a.cy.js": ("element-timeout", "never found")}))
        self.assertEqual(data[0][3], "LOW")
        self.assertEqual(corr, [])

    def test_glitch_mislabel_cause_replaced_for_value_mismatch(self):
        data = [row("a.cy.js", "LOW", "element covered by popup/overlay on click: 'Cypress TEST Project 2' button")]
        eke.enforce(HEADER, data, self.kinds({"a.cy.js": ("value-mismatch", "AssertionError: expected {..} to deeply equal {..}")}))
        cause = data[0][5]
        self.assertTrue(cause.startswith("value-mismatch:"))
        self.assertIn("deeply equal", cause)
        self.assertIn("auto-corrected", cause)

    def test_cypress_glitch_prefix_and_never_found_detected(self):
        # the agent's mislabels are written as "Cypress glitch: ... never found"
        for cause in (
            "Cypress glitch: `[data-cy='slope']` field never found within 30s",
            "Species-list field failed to open in time (interaction-timing)",
        ):
            data = [row("a.cy.js", "LOW", cause)]
            eke.enforce(HEADER, data, self.kinds({"a.cy.js": ("value-mismatch", "expected {..} to deeply equal {..}")}))
            self.assertTrue(data[0][5].startswith("value-mismatch:"), cause)
            self.assertEqual(data[0][3], "MEDIUM")

    def test_non_glitch_cause_kept_but_still_floored(self):
        # app-error with a legitimate (non-glitch) description keeps its cause
        data = [row("a.cy.js", "LOW", "publish flake: duplicate/already-submitted (pre-existing)")]
        eke.enforce(HEADER, data, self.kinds({"a.cy.js": ("app-error", "Failed to publish ... is not unique")}))
        self.assertEqual(data[0][3], "MEDIUM")
        self.assertIn("publish flake", data[0][5])

    def test_enforcement_is_idempotent(self):
        data = [row("a.cy.js", "LOW", "element covered by popup overlay q-card")]
        kinds = self.kinds({"a.cy.js": ("value-mismatch", "expected X to deeply equal Y")})
        eke.enforce(HEADER, data, kinds)
        first = data[0][5]
        # second pass must not re-wrap the already-corrected cause
        corr2 = eke.enforce(HEADER, data, kinds)
        self.assertEqual(data[0][5], first)
        self.assertEqual(corr2, [])

    def test_spec_not_in_failures_raw_untouched(self):
        data = [row("a.cy.js", "LOW", "whatever")]
        corr = eke.enforce(HEADER, data, {})
        self.assertEqual(data[0][3], "LOW")
        self.assertEqual(corr, [])

    def test_no_bug_column_is_noop(self):
        # per-job sheet has no bug_likelihood column
        header = ["Job", "Failed spec", "Note"]
        data = [["#1: run", "a.cy.js", ""]]
        corr = eke.enforce(header, data, self.kinds({"a.cy.js": ("value-mismatch", "x")}))
        self.assertEqual(corr, [])


class DiscoverAndLoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def test_discover_by_pid(self):
        (self.d / "failures_raw_123.json").write_text('{"specs": []}')
        csv_path = self.d / "failed_specs_unique_123.csv"
        found = eke.discover_failures_raw(csv_path)
        self.assertEqual(found.name, "failures_raw_123.json")

    def test_discover_single_fallback(self):
        (self.d / "failures_raw_999.json").write_text('{"specs": []}')
        csv_path = self.d / "failed_specs_unique_no_pid.csv"
        found = eke.discover_failures_raw(csv_path)
        self.assertIsNotNone(found)

    def test_discover_none_when_absent(self):
        self.assertIsNone(eke.discover_failures_raw(self.d / "failed_specs_unique_123.csv"))

    def test_load_error_kinds(self):
        p = self.d / "failures_raw_1.json"
        p.write_text(json.dumps({"specs": [
            {"spec": "a.cy.js", "error_kind": "value-mismatch", "first_error": "boom"},
        ]}))
        kinds = eke.load_error_kinds(p)
        self.assertEqual(kinds["a.cy.js"]["error_kind"], "value-mismatch")
        self.assertEqual(kinds["a.cy.js"]["first_error"], "boom")

    def test_apply_to_csv_rows_auto_discovers(self):
        (self.d / "failures_raw_5.json").write_text(json.dumps({"specs": [
            {"spec": "a.cy.js", "error_kind": "value-mismatch", "first_error": "expected X to equal Y"},
        ]}))
        csv_path = self.d / "failed_specs_unique_5.csv"
        data = [row("a.cy.js", "LOW", "dropdown never opened")]
        corr = eke.apply_to_csv_rows(csv_path, HEADER, data)
        self.assertEqual(data[0][3], "MEDIUM")
        self.assertTrue(corr)


if __name__ == "__main__":
    unittest.main()
