#!/usr/bin/env python3
"""
Extract the ROOT failure (first failing test + its error) for each failed
Cypress spec in a GitLab pipeline. Feeds failure_cause classification.

For every cypress-run / cypress-priority job, the LATEST attempt is used (so
specs that passed on retry are excluded — those are flaky, not hard failures).
For each spec it captures the first `N) <test>` block's error line plus every
distinct error signature seen in that spec's section.

Outputs JSON (default: failures_raw_<pipeline>.json):
  { "pipeline_id", "project", "sha", "web_url",
    "specs": [ { "spec", "spec_path", "job_id", "job_name", "first_test",
                 "first_error", "first_error_spec_line", "signatures": [...] } ] }

`sha` is the commit the pipeline ran against — use it to read the spec code
(the repo is public on GitLab), and `first_error_spec_line` is the spec-file
line of the first failing assertion (parsed from the stack trace), so a
classifier can jump straight to what the test was asserting.

Usage:
  ./extract_failures.py <pipeline_id_or_url> [-o failures_raw.json] [-p PROJECT]

Requires `glab` installed and authenticated.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from collections import defaultdict, OrderedDict

DEFAULT_PROJECT = "ternandsparrow/paratoo-fdcp"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s+\S+\s?")
RUNNING_RE = re.compile(r"Running:\s+\S*?([A-Za-z0-9_+\-.]+\.cy\.(?:js|ts))")
SPEC_START_RE = re.compile(r"\[SPEC START\]\s+(\S+\.cy\.(?:js|ts))")
FAIL_NUM_RE = re.compile(r"^\s*(\d+)\)\s+(.*)")
# Lines that look like an actual error/assertion (not a test title or stack frame).
ERR_RE = re.compile(
    r"(Error:|AssertionError|CypressError|TypeError|DropdownError|Timed out retrying|"
    r"did not filter|requires a DOM|is not a function|Cannot read|scroll to \d|"
    r"never found|never appeared|not to exist|no longer attached|page updated)"
)
# Stack frame referencing repo test code (the spec itself or a custom command
# under test/cypress/support/) — NOT node_modules or the cypress runner, e.g.
#   at Context.eval (webpack://monitor-webapp/./test/cypress/integration/priority/plot-context.cy.js:383:9)
#   at Context.eval (webpack://monitor-webapp/./test/cypress/support/commands.js:315:11)
REPO_FRAME_RE = re.compile(r"webpack://[^)\s]*?/\.?/?(test/cypress/[^)\s]+?):(\d+):\d+")


def glab(path):
    r = subprocess.run(["glab", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"glab api {path} failed: {r.stderr}\n")
        raise SystemExit(1)
    return r.stdout


def parse_pipeline_id(arg):
    if arg.isdigit():
        return arg
    m = re.search(r"/pipelines/(\d+)", arg)
    if m:
        return m.group(1)
    raise SystemExit(f"Could not parse a pipeline id from: {arg!r}")


def clean(line):
    return PREFIX_RE.sub("", ANSI_RE.sub("", line))


def latest_failed_cypress_jobs(project, pipeline_id):
    enc = urllib.parse.quote(project, safe="")
    path = f"projects/{enc}/pipelines/{pipeline_id}/jobs?per_page=100&include_retried=true"
    jobs = json.loads(glab(path))
    by_name = defaultdict(list)
    for j in jobs:
        by_name[j["name"]].append(j)
    out = []
    for name, attempts in by_name.items():
        if "cypress-run" not in name and "cypress-priority" not in name:
            continue
        latest = max(attempts, key=lambda x: x["created_at"])
        if latest["status"] == "failed":
            out.append(latest)
    return out


def parse_spec_failures(trace, job_id, job_name):
    """Return OrderedDict spec -> {job_id, job_name, spec_path, first_test,
    first_error, first_error_spec_line, signatures}."""
    lines = [clean(line) for line in trace.splitlines()]
    cur = None
    spec_paths = {}
    data = OrderedDict()
    for i, line in enumerate(lines):
        sm = SPEC_START_RE.search(line)
        if sm:
            full = sm.group(1)
            spec_paths[full.rsplit("/", 1)[-1]] = full
            continue
        rm = RUNNING_RE.search(line)
        if rm:
            cur = rm.group(1)
            continue
        if cur is None:
            continue
        fm = FAIL_NUM_RE.match(line)
        if not fm:
            continue
        title = fm.group(2).strip()
        err = None
        spec_line = None
        frames = []
        for j in range(i + 1, min(i + 13, len(lines))):
            if err is None and ERR_RE.search(lines[j]):
                err = lines[j].strip()[:300]
            # Stack frames pointing into repo test code (the spec itself or a
            # custom command in test/cypress/support/) are the classifier's
            # entry points into what the failing assertion actually checks.
            frame = REPO_FRAME_RE.search(lines[j])
            if frame:
                path, lineno = frame.group(1), int(frame.group(2))
                ref = f"{path}:{lineno}"
                if ref not in frames and len(frames) < 3:
                    frames.append(ref)
                if spec_line is None and path.endswith(cur):
                    spec_line = lineno
        d = data.setdefault(
            cur,
            {"spec": cur, "spec_path": None, "job_id": job_id, "job_name": job_name,
             "first_test": None, "first_error": None, "first_error_spec_line": None,
             "first_error_frames": [], "signatures": []},
        )
        if d["first_test"] is None:
            d["first_test"] = title[:200]
        # The same failure appears twice in a trace: once mid-run (often
        # without error details nearby) and once in the final "N failing"
        # summary (with the error + stack frames). Take the error details
        # from whichever block first has them.
        if d["first_error"] is None and err:
            d["first_error"] = err
            d["first_error_spec_line"] = spec_line
            d["first_error_frames"] = frames
        if err and err not in d["signatures"]:
            d["signatures"].append(err)
    # The first `1)` block's error can sit beyond the scan window (deep suite
    # nesting). Fall back to the first real error seen anywhere in the spec so
    # first_error is never empty when signatures exist.
    for rec in data.values():
        if rec["first_error"] is None and rec["signatures"]:
            rec["first_error"] = rec["signatures"][0]
        rec["spec_path"] = spec_paths.get(rec["spec"])
    return data


def fetch_pipeline_info(project, pipeline_id):
    """Return {sha, web_url} for the pipeline (empty strings on failure)."""
    enc = urllib.parse.quote(project, safe="")
    try:
        info = json.loads(glab(f"projects/{enc}/pipelines/{pipeline_id}"))
        return {"sha": info.get("sha", ""), "web_url": info.get("web_url", "")}
    except SystemExit:
        return {"sha": "", "web_url": ""}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pipeline", help="pipeline id or GitLab pipeline URL")
    ap.add_argument(
        "-o", "--output", default=None,
        help="output JSON path (default: failures_raw_<pipeline_id>.json, so multiple "
             "pipelines can be analyzed in the same folder without overwriting)",
    )
    ap.add_argument("-p", "--project", default=DEFAULT_PROJECT)
    args = ap.parse_args()

    pid = parse_pipeline_id(args.pipeline)
    if args.output is None:
        args.output = f"failures_raw_{pid}.json"
    sys.stderr.write(f"Fetching latest failed cypress jobs for pipeline {pid}...\n")
    jobs = latest_failed_cypress_jobs(args.project, pid)
    sys.stderr.write(f"  {len(jobs)} failed cypress job(s) (latest attempt).\n")

    merged = OrderedDict()
    enc = urllib.parse.quote(args.project, safe="")
    for j in jobs:
        sys.stderr.write(f"  parsing job {j['id']} ({j['name']})...\n")
        trace = glab(f"projects/{enc}/jobs/{j['id']}/trace")
        for spec, rec in parse_spec_failures(trace, j["id"], j["name"]).items():
            merged.setdefault(spec, rec)  # first job that has the spec wins

    pipeline_info = fetch_pipeline_info(args.project, pid)
    out = {
        "pipeline_id": pid,
        "project": args.project,
        "sha": pipeline_info["sha"],
        "web_url": pipeline_info["web_url"],
        "specs": list(merged.values()),
    }
    with open(args.output, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    sys.stderr.write(
        f"\nWrote {len(out['specs'])} spec failure record(s) to {args.output} "
        f"(pipeline sha: {pipeline_info['sha'][:12] or 'unknown'})\n"
    )
    # Human-readable preview for quick classification.
    for rec in out["specs"]:
        detail = rec["first_error"] or rec["first_test"] or "(no error captured)"
        line = f":{rec['first_error_spec_line']}" if rec["first_error_spec_line"] else ""
        sys.stderr.write(f"  - {rec['spec']}{line}: {detail}\n")


if __name__ == "__main__":
    main()
