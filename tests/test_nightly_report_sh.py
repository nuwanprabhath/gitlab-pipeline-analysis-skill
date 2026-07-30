"""End-to-end tests for automation/nightly_report.sh with stubbed binaries.

`claude`, `glab` and `notify-send` are replaced by shell stubs on PATH, so the
whole unattended flow runs offline in under a second: pipeline resolution,
start/finish notifications, intermediate cleanup, and every failure exit.

Both bugs these cover were found by running the script for real:
  * PATH fallbacks were prepended, so a caller's explicit PATH was ignored;
  * notifications need DBUS_SESSION_BUS_ADDRESS/DISPLAY, absent under cron.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import xlsx  # noqa: E402

RUNNER = REPO / "automation" / "nightly_report.sh"
PID = 999

GLAB_STUB = """#!/usr/bin/env bash
case "$1 $2" in
  "auth status") exit 0 ;;
esac
cat "$STUB_GLAB_PAYLOAD"
"""

# Simulates the skill: drops the two deliverables plus the intermediates that
# the runner is responsible for cleaning up.
CLAUDE_STUB = """#!/usr/bin/env bash
if [ "${STUB_CLAUDE_WRITES:-yes}" = "yes" ]; then
  cp "$STUB_XLSX" "$STUB_OUT_DIR/failed_specs_unique_%(pid)s.xlsx"
  cp "$STUB_XLSX" "$STUB_OUT_DIR/all_specs_%(pid)s.xlsx"
  : > "$STUB_OUT_DIR/failures_raw_%(pid)s.json"
  : > "$STUB_OUT_DIR/mapping_%(pid)s.json"
  : > "$STUB_OUT_DIR/spec_runs_%(pid)s.json"
  : > "$STUB_OUT_DIR/failed_specs_unique_%(pid)s.csv"
fi
echo "$@" > "$STUB_OUT_DIR/claude_args.txt"
exit ${STUB_CLAUDE_RC:-0}
""" % {"pid": PID}

# Bodies are multi-line, so each argument is terminated by its own marker line
# rather than assuming one argument per line.
NOTIFY_STUB = """#!/usr/bin/env bash
{ for a in "$@"; do printf '%s\\n--ARG--\\n' "$a"; done; echo "--END--"; } \
  >> "$STUB_NOTIFY_LOG"
exit ${STUB_NOTIFY_RC:-0}
"""


class NightlyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "reports"
        self.out.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()

        payload = self.root / "pipelines.json"
        payload.write_text(json.dumps([
            {"id": PID, "ref": "dev/1.0.11", "created_at": "2026-07-30T00:07:11Z"}
        ]))

        self.fixture_xlsx = self.root / "fixture.xlsx"
        xlsx.write_workbook(self.fixture_xlsx, [xlsx.Sheet("s", [
            [xlsx.Cell("Failed spec"), xlsx.Cell("New failure"),
             xlsx.Cell("bug_likelihood_(AI)")],
            [xlsx.Cell("a.cy.js [run 3/8]"), xlsx.Cell("yes"), xlsx.Cell("HIGH")],
            [xlsx.Cell("b.cy.js"), xlsx.Cell("no"), xlsx.Cell("LOW")],
        ])])

        self._stub("glab", GLAB_STUB)
        self._stub("claude", CLAUDE_STUB)
        self._stub("notify-send", NOTIFY_STUB)

        # A decoy on the script's own fallback path ($HOME/.local/bin). HOME is
        # redirected into the temp dir, so this never touches the real one. If
        # the script prepends its fallbacks the decoy wins and tests notice —
        # without ever risking a call to the real `claude`.
        self.fallback_bin = self.root / "home" / ".local" / "bin"
        self.fallback_bin.mkdir(parents=True)
        decoy = self.fallback_bin / "claude"
        decoy.write_text(
            '#!/usr/bin/env bash\ntouch "$STUB_OUT_DIR/DECOY_CLAUDE_RAN"\nexit 0\n'
        )
        decoy.chmod(0o755)

        self.notify_log = self.root / "notifications.txt"
        self.config = self.root / "config.env"
        self.write_config()

        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            HOME=str(self.root / "home"),
            NIGHTLY_REPORT_CONFIG=str(self.config),
            STUB_GLAB_PAYLOAD=str(payload),
            STUB_XLSX=str(self.fixture_xlsx),
            STUB_OUT_DIR=str(self.out),
            STUB_NOTIFY_LOG=str(self.notify_log),
        )

    def _stub(self, name, body):
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)

    def write_config(self, **overrides):
        settings = {
            "PROJECT": "group/proj",
            "OUT_DIR": str(self.out),
            "DETECTION_MODE": "trigger_window",
            "TRIGGER_USER": "tomantonic",
            "WINDOW_START_HOUR": "0",
            "WINDOW_END_HOUR": "24",
            "MAX_AGE_HOURS": "0",
            "SKIP_IF_ALREADY_ANALYZED": "yes",
        }
        settings.update(overrides)
        self.config.write_text(
            "\n".join(f'{k}="{v}"' for k, v in settings.items()) + "\n"
        )

    def run_script(self, **env_overrides):
        env = dict(self.env, **env_overrides)
        return subprocess.run(
            ["bash", str(RUNNER)], capture_output=True, text=True, env=env
        )

    def notifications(self):
        """[(urgency, title, body), ...] in the order they were sent."""
        if not self.notify_log.exists():
            return []
        out, args, buf = [], [], []
        for line in self.notify_log.read_text().splitlines():
            if line == "--ARG--":
                args.append("\n".join(buf))
                buf = []
            elif line == "--END--":
                flags = [a for a in args if a.startswith("--")]
                positional = [a for a in args if not a.startswith("--")]
                urgency = next(
                    (f.split("=", 1)[1] for f in flags if f.startswith("--urgency=")),
                    "",
                )
                out.append((urgency, positional[0], positional[1]))
                args = []
            else:
                buf.append(line)
        return out

    # --- happy path ----------------------------------------------------------

    def test_successful_run_produces_reports_and_exits_zero(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.out / f"failed_specs_unique_{PID}.xlsx").exists())
        self.assertTrue((self.out / f"all_specs_{PID}.xlsx").exists())

    def test_intermediates_are_cleaned_up(self):
        """The allowlist denies `rm` to Claude, so the wrapper must clean up."""
        self.run_script()
        for leftover in (
            f"failures_raw_{PID}.json", f"mapping_{PID}.json",
            f"spec_runs_{PID}.json", f"failed_specs_unique_{PID}.csv",
        ):
            self.assertFalse((self.out / leftover).exists(), leftover)

    def test_start_and_finish_notifications(self):
        self.run_script()
        notes = self.notifications()
        self.assertEqual(len(notes), 2, notes)

        urgency, title, body = notes[0]
        self.assertEqual(urgency, "normal")
        self.assertIn(f"#{PID}", title)
        self.assertIn("started", title.lower())
        self.assertIn("group/proj", body)

        urgency, title, body = notes[1]
        self.assertEqual(urgency, "normal")
        self.assertIn(f"#{PID}", title)
        self.assertIn("finished", title.lower())
        # counts come from the real workbook reader
        self.assertIn("2 unique failures", body)
        self.assertIn("1 HIGH", body)
        self.assertIn("1 new", body)
        self.assertIn(str(self.out), body)

    def test_claude_is_invoked_without_bypass_permissions(self):
        self.run_script()
        args = (self.out / "claude_args.txt").read_text()
        self.assertIn("--allowedTools", args)
        self.assertIn("--disallowedTools", args)
        self.assertNotIn("bypassPermissions", args)
        # ticket creation and deletion must stay denied
        self.assertIn("Bash(glab issue:*)", args)
        self.assertIn("Bash(rm:*)", args)

    def test_prompt_carries_the_unattended_overrides(self):
        self.run_script()
        args = (self.out / "claude_args.txt").read_text()
        self.assertIn(str(PID), args)
        self.assertIn("UNATTENDED", args)

    # --- PATH regression -----------------------------------------------------

    def test_callers_path_takes_precedence_over_fallbacks(self):
        """Regression: fallbacks were prepended, so an explicit caller PATH
        lost to $HOME/.local/bin. Found the hard way — it silently bypassed a
        stub and started a real analysis run."""
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(
            (self.out / "claude_args.txt").exists(),
            "the claude on the caller's PATH should have run",
        )
        self.assertFalse(
            (self.out / "DECOY_CLAUDE_RAN").exists(),
            "$HOME/.local/bin decoy won — fallbacks are being prepended again",
        )

    # --- skip path -----------------------------------------------------------

    def test_skips_when_already_analyzed(self):
        shutil.copy(self.fixture_xlsx, self.out / f"failed_specs_unique_{PID}.xlsx")
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.out / "claude_args.txt").exists())
        self.assertIn("already analyzed", self.notifications()[0][1].lower())

    def test_skip_can_be_disabled(self):
        shutil.copy(self.fixture_xlsx, self.out / f"failed_specs_unique_{PID}.xlsx")
        self.write_config(SKIP_IF_ALREADY_ANALYZED="no")
        self.run_script()
        self.assertTrue((self.out / "claude_args.txt").exists())

    # --- failure paths -------------------------------------------------------

    def test_claude_failure_exits_nonzero_with_critical_notification(self):
        r = self.run_script(STUB_CLAUDE_RC="3")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.notifications()[-1][0], "critical")

    def test_missing_report_is_reported_as_failure(self):
        r = self.run_script(STUB_CLAUDE_WRITES="no")
        self.assertNotEqual(r.returncode, 0)
        urgency, title, body = self.notifications()[-1]
        self.assertEqual(urgency, "critical")
        self.assertIn("not produced", body)

    def test_unresolvable_pipeline_exits_nonzero(self):
        empty = self.root / "empty.json"
        empty.write_text("[]")
        r = self.run_script(STUB_GLAB_PAYLOAD=str(empty))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.notifications()[-1][0], "critical")
        self.assertFalse((self.out / "claude_args.txt").exists())

    def test_missing_config_exits_nonzero(self):
        self.config.unlink()
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Missing config", r.stderr)

    def test_unauthenticated_glab_stops_before_running_claude(self):
        self._stub("glab", '#!/usr/bin/env bash\nexit 1\n')
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not authenticated", r.stdout + r.stderr)
        self.assertFalse((self.out / "claude_args.txt").exists())

    # --- notification degradation -------------------------------------------

    def test_notify_send_failure_does_not_fail_the_run(self):
        """The realistic cron breakage: notify-send exists but can't reach a
        session bus. Under `set -e` that would abort the job — a notification
        must never be able to fail the run."""
        r = self.run_script(STUB_NOTIFY_RC="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.out / f"failed_specs_unique_{PID}.xlsx").exists())

    def test_notify_can_be_turned_off(self):
        """Also covers a machine with no notify-send at all (macOS, headless):
        the script sets NOTIFY=no itself in that case, reaching this same path.
        Not tested by unlinking the stub, because the script's /usr/bin PATH
        fallback would still find a real notify-send on most Linux boxes."""
        self.write_config(NOTIFY="no")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.notifications(), [])


if __name__ == "__main__":
    unittest.main()
