#!/usr/bin/env python3
"""
Extract the ROOT failure (first failing test + its error) for each failed
Cypress spec in a GitLab pipeline. Feeds failure_cause classification.

For every cypress-run / cypress-priority job, the LATEST attempt is used (so
specs that passed on retry are excluded — those are flaky, not hard failures).
For each spec it captures the first `N) <test>` block's error line plus every
distinct error signature seen in that spec's section.

Outputs JSON (default: failures_raw.json), a list of:
  { "spec", "job_id", "job_name", "first_test", "first_error", "signatures": [...] }

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
FAIL_NUM_RE = re.compile(r"^\s*(\d+)\)\s+(.*)")
# Lines that look like an actual error/assertion (not a test title or stack frame).
ERR_RE = re.compile(
    r"(Error:|AssertionError|CypressError|TypeError|DropdownError|Timed out retrying|"
    r"did not filter|requires a DOM|is not a function|Cannot read|scroll to \d|"
    r"never found|never appeared|not to exist|no longer attached|page updated)"
)


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
    """Return OrderedDict spec -> {job_id, job_name, first_test, first_error, signatures}."""
    lines = [clean(line) for line in trace.splitlines()]
    cur = None
    data = OrderedDict()
    for i, line in enumerate(lines):
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
        for j in range(i + 1, min(i + 9, len(lines))):
            if ERR_RE.search(lines[j]):
                err = lines[j].strip()[:300]
                break
        d = data.setdefault(
            cur,
            {"spec": cur, "job_id": job_id, "job_name": job_name,
             "first_test": None, "first_error": None, "signatures": []},
        )
        if d["first_test"] is None:
            d["first_test"] = title[:200]
            d["first_error"] = err
        if err and err not in d["signatures"]:
            d["signatures"].append(err)
    # The first `1)` block's error can sit beyond the scan window (deep suite
    # nesting). Fall back to the first real error seen anywhere in the spec so
    # first_error is never empty when signatures exist.
    for rec in data.values():
        if rec["first_error"] is None and rec["signatures"]:
            rec["first_error"] = rec["signatures"][0]
    return data


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pipeline", help="pipeline id or GitLab pipeline URL")
    ap.add_argument("-o", "--output", default="failures_raw.json")
    ap.add_argument("-p", "--project", default=DEFAULT_PROJECT)
    args = ap.parse_args()

    pid = parse_pipeline_id(args.pipeline)
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

    out = list(merged.values())
    with open(args.output, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    sys.stderr.write(f"\nWrote {len(out)} spec failure record(s) to {args.output}\n")
    # Human-readable preview for quick classification.
    for rec in out:
        detail = rec["first_error"] or rec["first_test"] or "(no error captured)"
        sys.stderr.write(f"  - {rec['spec']}: {detail}\n")


if __name__ == "__main__":
    main()
