"""Tests for automation/find_nightly_pipeline.py — resolving "the latest
nightly pipeline" without hitting the network.

`glab_api` is stubbed with canned pipeline pages, so these run offline. The
pagination cases exist because the first version scanned only one page: on a
busy repo the nightly is buried under dozens of MR pipelines, and client-side
filtering (ref prefix, or a window with no trigger user) silently found nothing.
"""

import contextlib
import datetime
import io
import sys
import unittest
import urllib.parse
from pathlib import Path

AUTOMATION_DIR = Path(__file__).resolve().parent.parent / "automation"
sys.path.insert(0, str(AUTOMATION_DIR))

import find_nightly_pipeline as fnp  # noqa: E402


def at_local_hour(hour, days_ago=0, minute=0):
    """ISO-8601 UTC string for a moment that is `hour` in the LOCAL timezone,
    so window tests don't depend on where they run."""
    now = datetime.datetime.now().astimezone()
    local = (now - datetime.timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pipeline(pid, created_at=None, ref="dev/1.0.11", **extra):
    p = {"id": pid, "ref": ref, "created_at": created_at or at_local_hour(0)}
    p.update(extra)
    return p


class FakeApi:
    """Stands in for glab_api: serves pages and records requested paths."""

    def __init__(self, pages):
        # pages: list of lists, one per page (1-indexed by the ?page= param)
        self.pages = pages
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        query = urllib.parse.parse_qs(urllib.parse.urlparse("?" + path.split("?", 1)[1]).query) \
            if "?" in path else {}
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["100"])[0])
        if page > len(self.pages):
            return []
        # GitLab honours per_page; the caller shrinks it as the scan budget
        # runs down, so the fake must too or budget tests are meaningless.
        return self.pages[page - 1][:per_page]

    def query_of(self, index=0):
        return urllib.parse.parse_qs(self.paths[index].split("?", 1)[1])


def run_main(api, *argv):
    """Run find_nightly_pipeline.main() with a stubbed API.

    Returns (stdout, exit_message). exit_message is None on success.
    """
    original_api, original_argv = fnp.glab_api, sys.argv
    fnp.glab_api = api
    sys.argv = ["find_nightly_pipeline.py", *argv]
    out = io.StringIO()
    message = None
    try:
        with contextlib.redirect_stdout(out):
            fnp.main()
    except SystemExit as exc:
        message = str(exc.code)
    finally:
        fnp.glab_api, sys.argv = original_api, original_argv
    return out.getvalue().strip(), message


BASE = ("-p", "group/proj", "--max-age-hours", "0")


class InWindowTests(unittest.TestCase):
    def test_plain_window(self):
        self.assertTrue(fnp.in_window(at_local_hour(3), 2, 5))
        self.assertFalse(fnp.in_window(at_local_hour(6), 2, 5))

    def test_window_wrapping_midnight(self):
        # The default nightly window: 23:00 -> 02:00
        for hour in (23, 0, 1):
            self.assertTrue(fnp.in_window(at_local_hour(hour), 23, 2), hour)
        for hour in (2, 3, 12, 22):
            self.assertFalse(fnp.in_window(at_local_hour(hour), 23, 2), hour)

    def test_boundaries_are_start_inclusive_end_exclusive(self):
        self.assertTrue(fnp.in_window(at_local_hour(23), 23, 2))
        self.assertFalse(fnp.in_window(at_local_hour(2), 23, 2))


class TriggerWindowModeTests(unittest.TestCase):
    def test_picks_newest_pipeline_inside_the_window(self):
        api = FakeApi([[
            pipeline(300, at_local_hour(14)),   # afternoon — not the nightly
            pipeline(200, at_local_hour(0)),    # overnight — this one
            pipeline(100, at_local_hour(1, days_ago=1)),
        ]])
        out, err = run_main(api, *BASE, "-m", "trigger_window",
                            "--trigger-user", "tomantonic")
        self.assertIsNone(err)
        self.assertEqual(out, "200")

    def test_trigger_user_is_filtered_server_side(self):
        api = FakeApi([[pipeline(200)]])
        run_main(api, *BASE, "-m", "trigger_window", "--trigger-user", "tomantonic")
        self.assertEqual(api.query_of()["username"], ["tomantonic"])

    def test_no_trigger_user_sends_no_username_filter(self):
        api = FakeApi([[pipeline(200)]])
        run_main(api, *BASE, "-m", "trigger_window", "--trigger-user", "")
        self.assertNotIn("username", api.query_of())

    def test_nothing_in_window_exits_nonzero(self):
        api = FakeApi([[pipeline(300, at_local_hour(14))]])
        out, err = run_main(api, *BASE, "-m", "trigger_window",
                            "--trigger-user", "tomantonic")
        self.assertEqual(out, "")
        self.assertIn("no pipeline matched", err)


class RefModeTests(unittest.TestCase):
    def test_exact_ref_is_filtered_server_side(self):
        api = FakeApi([[pipeline(200, ref="dev/1.0.11")]])
        out, err = run_main(api, *BASE, "-m", "ref", "--ref", "dev/1.0.11")
        self.assertIsNone(err)
        self.assertEqual(out, "200")
        self.assertEqual(api.query_of()["ref"], ["dev/1.0.11"])

    def test_prefix_ref_is_filtered_client_side(self):
        api = FakeApi([[
            pipeline(300, ref="feature/x"),
            pipeline(200, ref="dev/1.0.11"),
        ]])
        out, err = run_main(api, *BASE, "-m", "ref", "--ref", "dev/*")
        self.assertIsNone(err)
        self.assertEqual(out, "200")
        # A prefix can't be a server-side ref filter.
        self.assertNotIn("ref", api.query_of())

    def test_prefix_ref_pages_past_a_full_page_of_non_matches(self):
        """Regression: the nightly sits below 100 MR pipelines on a busy repo.
        Scanning only the first page reported 'no pipeline matched'."""
        page1 = [pipeline(1000 + i, ref="feature/x") for i in range(100)]
        page2 = [pipeline(500, ref="feature/y"), pipeline(499, ref="dev/1.0.11")]
        api = FakeApi([page1, page2])
        out, err = run_main(api, *BASE, "-m", "ref", "--ref", "dev/*", "--scan", "300")
        self.assertIsNone(err)
        self.assertEqual(out, "499")
        self.assertEqual(len(api.paths), 2, "should have fetched a second page")

    def test_scan_budget_caps_paging(self):
        api = FakeApi([[pipeline(1000 + i, ref="feature/x") for i in range(100)]] * 5)
        out, err = run_main(api, *BASE, "-m", "ref", "--ref", "dev/*", "--scan", "150")
        self.assertIn("scanned 150", err)
        self.assertEqual(len(api.paths), 2)

    def test_exhausted_results_stop_paging(self):
        """An empty page ends the scan instead of looping to the budget."""
        api = FakeApi([[pipeline(900, ref="feature/x")]])
        out, err = run_main(api, *BASE, "-m", "ref", "--ref", "dev/*", "--scan", "300")
        self.assertIn("no pipeline matched", err)
        self.assertEqual(len(api.paths), 2)  # one real page, one empty

    def test_missing_ref_is_an_error(self):
        out, err = run_main(FakeApi([[]]), *BASE, "-m", "ref", "--ref", "")
        self.assertIn("requires --ref", err)


class ScheduleIdModeTests(unittest.TestCase):
    def test_uses_the_schedule_endpoint(self):
        api = FakeApi([[pipeline(200)]])
        out, err = run_main(api, *BASE, "-m", "schedule_id", "--schedule-id", "42")
        self.assertIsNone(err)
        self.assertEqual(out, "200")
        self.assertIn("pipeline_schedules/42/pipelines", api.paths[0])

    def test_takes_the_newest_without_window_filtering(self):
        # A scheduled pipeline is nightly by definition — no hour filter applies.
        api = FakeApi([[pipeline(200, at_local_hour(14))]])
        out, err = run_main(api, *BASE, "-m", "schedule_id", "--schedule-id", "42")
        self.assertEqual(out, "200")

    def test_missing_schedule_id_is_an_error(self):
        out, err = run_main(FakeApi([[]]), *BASE, "-m", "schedule_id")
        self.assertIn("requires --schedule-id", err)


class MaxAgeGuardTests(unittest.TestCase):
    """Without this guard a night the pipeline didn't run silently re-reports
    yesterday's pipeline as if it were fresh."""

    def test_rejects_a_stale_match(self):
        api = FakeApi([[pipeline(200, at_local_hour(0, days_ago=3))]])
        out, err = run_main(api, "-p", "group/proj", "-m", "trigger_window",
                            "--trigger-user", "u",
                            # all-day window: isolate the age guard from the
                            # hour filter, whatever time the suite runs at
                            "--window-start-hour", "0", "--window-end-hour", "24",
                            "--max-age-hours", "24")
        self.assertEqual(out, "")
        self.assertIn("--max-age-hours", err)
        self.assertIn("#200", err)

    def test_accepts_a_fresh_match(self):
        recent = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        api = FakeApi([[pipeline(200, recent)]])
        out, err = run_main(api, "-p", "group/proj", "-m", "trigger_window",
                            "--trigger-user", "u",
                            # all-day window: isolate the age guard from the
                            # hour filter, whatever time the suite runs at
                            "--window-start-hour", "0", "--window-end-hour", "24",
                            "--max-age-hours", "24")
        self.assertIsNone(err)
        self.assertEqual(out, "200")

    def test_zero_disables_the_check(self):
        api = FakeApi([[pipeline(200, at_local_hour(0, days_ago=30))]])
        out, err = run_main(api, "-p", "group/proj", "-m", "trigger_window",
                            "--trigger-user", "u",
                            # all-day window: isolate the age guard from the
                            # hour filter, whatever time the suite runs at
                            "--window-start-hour", "0", "--window-end-hour", "24",
                            "--max-age-hours", "0")
        self.assertIsNone(err)
        self.assertEqual(out, "200")


class ProjectEncodingTests(unittest.TestCase):
    def test_project_path_is_url_encoded(self):
        api = FakeApi([[pipeline(200)]])
        run_main(api, *BASE, "-m", "trigger_window", "--trigger-user", "u")
        self.assertIn("projects/group%2Fproj/pipelines", api.paths[0])


if __name__ == "__main__":
    unittest.main()
