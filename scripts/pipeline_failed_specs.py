#!/usr/bin/env python3
"""
Map every Cypress spec in a GitLab CI pipeline to the job attempts it ran in.

Fetches all cypress-run / cypress-priority job attempts (jobs retry once, so
max 2 attempts), parses each trace's `[SPEC START]`/`[SPEC END]` markers for
per-spec pass/fail, and writes:
  - all_specs_<pid>.csv        every spec + its 1st/2nd job attempt
  - failed_specs_unique_<pid>.csv  failed specs, classification columns blank
  - spec_runs_<pid>.json       sidecar with per-attempt status (for colouring)
A spec that started but never got an `[SPEC END]` (crash/timeout/OOM) is marked
Note = "JOB CRASHED".

Usage:
  ./pipeline_failed_specs.py <pipeline_id_or_url> [-o ALL_SPECS.csv] [-u UNIQUE.csv] [-p PROJECT]

Requires `glab` to be installed and authenticated.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

DEFAULT_PROJECT = "ternandsparrow/paratoo-fdcp"
GITLAB_BASE_URL = "https://gitlab.com"
# Cypress is run from paratoo-webapp/ with specPattern test/cypress/integration/**/*.cy.{js,ts}.
# The --spec paths passed to `yarn cypress run` are relative to paratoo-webapp/.
SPEC_PATH_PREFIX = "test/cypress/integration"


def _detect_integration_dir():
    """Locate the Cypress integration dir so spec filenames can resolve to real
    sub-paths. Falls back to None (glob-based re-run paths) when no checkout is
    nearby — so the tool still works without a local clone of the app repo."""
    candidates = []
    env = os.environ.get("PARATOO_WEBAPP_INTEGRATION_DIR")
    if env:
        candidates.append(Path(env))
    cwd = Path.cwd()
    candidates += [
        cwd / "paratoo-webapp" / SPEC_PATH_PREFIX,
        cwd / SPEC_PATH_PREFIX,
        Path(__file__).resolve().parent.parent.parent / "paratoo-webapp" / SPEC_PATH_PREFIX,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


CYPRESS_INTEGRATION_DIR = _detect_integration_dir()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
GITLAB_LINE_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z \S+ ?", re.MULTILINE
)
# CI wraps each spec with `[SPEC START] <path> | ...` / `[SPEC END]   <path> | ... | <symbol> <PASSED|FAILED>`.
# These markers are per-spec and always present, unlike Cypress's own final
# "(Run Finished)" summary table, which a job never prints if it dies mid-batch
# (crash/timeout/OOM) — relying on that table alone silently drops real
# failures and passes from any job that doesn't finish cleanly.
SPEC_EVENT_RE = re.compile(
    r"\[SPEC (START|END)\]\s+(\S+\.cy\.(?:js|ts))(?:[^\n]*?(✔ PASSED|✖ FAILED))?"
)
# Cypress Cloud run URL, printed once near the top of a recorded run's trace.
CYPRESS_RUN_URL_RE = re.compile(r"Run URL:\s*(https?://\S*?cypress\.io/\S+)")
MISSING_OUTPUT_NOTE = "JOB CRASHED"


def parse_cypress_run_url(log):
    """Return the Cypress Cloud run URL for a job trace, or '' if not recorded."""
    m = CYPRESS_RUN_URL_RE.search(ANSI_RE.sub("", log))
    return m.group(1).rstrip("│ ") if m else ""


def is_cypress_job(name):
    return "cypress-run" in name or "cypress-priority" in name


def glab(path):
    """Call `glab api <path>` and return stdout as text."""
    try:
        result = subprocess.run(
            ["glab", "api", path],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"glab api {path} failed: {exc.stderr}\n")
        raise
    return result.stdout


def glab_paginated(path):
    """Call `glab api --paginate <path>` and return parsed JSON list."""
    try:
        result = subprocess.run(
            ["glab", "api", "--paginate", path],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"glab api --paginate {path} failed: {exc.stderr}\n")
        raise
    return json.loads(result.stdout)


def fetch_failed_jobs(project, pipeline_id):
    project_enc = urllib.parse.quote(project, safe="")
    path = (
        f"projects/{project_enc}/pipelines/{pipeline_id}/jobs"
        f"?scope%5B%5D=failed&per_page=100&include_retried=true"
    )
    return glab_paginated(path)


def fetch_all_jobs(project, pipeline_id):
    """Fetch ALL jobs (any status) including retries for the pipeline."""
    project_enc = urllib.parse.quote(project, safe="")
    path = (
        f"projects/{project_enc}/pipelines/{pipeline_id}/jobs"
        f"?per_page=100&include_retried=true"
    )
    return glab_paginated(path)


def fetch_job_trace(project, job_id):
    project_enc = urllib.parse.quote(project, safe="")
    return glab(f"projects/{project_enc}/jobs/{job_id}/trace")


def clean_log(log):
    log = ANSI_RE.sub("", log)
    log = GITLAB_LINE_PREFIX_RE.sub("", log)
    return log


def parse_spec_events(log):
    """Walk a job's `[SPEC START]`/`[SPEC END]` markers in order.

    Returns (order, status): `order` is the list of spec paths (full
    repo-relative path, e.g. 'test/cypress/integration/run/foo.cy.js') in
    first-seen order; `status` maps each path to 'PASSED', 'FAILED', or
    'MISSING' (started but the job died — crash/timeout/OOM — before an END
    was logged for it, so the outcome is unknown).
    """
    log = clean_log(log)
    order = []
    status = {}
    pending = None
    for kind, spec, outcome in SPEC_EVENT_RE.findall(log):
        if spec not in order:
            order.append(spec)
        if kind == "START":
            if pending is not None and pending not in status:
                status[pending] = "MISSING"
            pending = spec
        else:  # END
            status[spec] = "FAILED" if outcome.startswith("✖") else "PASSED"
            if pending == spec:
                pending = None
    if pending is not None and pending not in status:
        status[pending] = "MISSING"
    return order, status


def parse_failed_specs(log):
    """Return failed spec basenames (e.g. 'foo.cy.js'), first-seen order,
    based on `[SPEC END] ... FAILED` markers."""
    order, status = parse_spec_events(log)
    return [os.path.basename(s) for s in order if status.get(s) == "FAILED"]


def find_missing_output_specs(log):
    """Return spec basenames that started (`[SPEC START]`) but the job died
    before logging their `[SPEC END]` — outcome unknown (crash/timeout/OOM)."""
    order, status = parse_spec_events(log)
    return [os.path.basename(s) for s in order if status.get(s) == "MISSING"]


def parse_spec_full_paths(log):
    """Return {basename: full_repo_relative_path} for every spec seen via
    `[SPEC START]`/`[SPEC END]` markers in this job's trace."""
    order, _status = parse_spec_events(log)
    return {os.path.basename(s): s for s in order}


def resolve_spec_paths(spec_names, integration_dir=CYPRESS_INTEGRATION_DIR, known_paths=None):
    """Map each bare spec filename (e.g. 'foo.cy.js') to its repo-relative path.

    Prefers `known_paths` (collected from `[SPEC START]`/`[SPEC END]` markers
    across the pipeline's own job traces — the exact path Cypress actually
    used) and falls back to a local checkout lookup, then to a recursive glob.

    Returns (resolved, unresolved) where resolved is a list of
    'test/cypress/integration/<subdir>/<file>' strings in input order and
    unresolved is a list of filenames we couldn't locate any other way.
    """
    known_paths = known_paths or {}
    resolved, unresolved = [], []
    have_checkout = integration_dir is not None and Path(integration_dir).is_dir()
    for name in spec_names:
        if name in known_paths:
            resolved.append(known_paths[name])
            continue
        if have_checkout:
            matches = list(integration_dir.rglob(name))
            if matches:
                # Pick the shortest path in case the same basename appears more than once
                match = min(matches, key=lambda p: len(p.parts))
                rel = match.relative_to(integration_dir.parent.parent.parent)
                resolved.append(str(rel).replace("\\", "/"))
                continue
        if not have_checkout:
            # No local checkout and no known path — emit a recursive glob that
            # Cypress can match without one.
            resolved.append(f"{SPEC_PATH_PREFIX}/**/{name}")
            continue
        unresolved.append(name)
    return resolved, unresolved


def build_cypress_command(spec_paths):
    if not spec_paths:
        return None
    joined = ",".join(spec_paths)
    return f'yarn cypress run --browser chrome --spec "{joined}"'


def parse_pipeline_id(arg):
    """Accept a numeric pipeline id or a full GitLab pipeline URL."""
    if arg.isdigit():
        return arg
    m = re.search(r"/pipelines/(\d+)", arg)
    if m:
        return m.group(1)
    raise ValueError(f"Could not parse pipeline id from: {arg}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pipeline", help="pipeline id or GitLab pipeline URL")
    parser.add_argument(
        "-o", "--output", default=None,
        help="all-specs CSV path (default: all_specs_<pipeline_id>.csv)",
    )
    parser.add_argument(
        "-u", "--unique-output", default=None,
        help="failed-specs CSV path (default: failed_specs_unique_<pipeline_id>.csv)",
    )
    parser.add_argument("-p", "--project", default=DEFAULT_PROJECT, help=f"GitLab project path (default: {DEFAULT_PROJECT})")
    args = parser.parse_args()

    pipeline_id = parse_pipeline_id(args.pipeline)
    if args.output is None:
        args.output = f"all_specs_{pipeline_id}.csv"
    if args.unique_output is None:
        args.unique_output = f"failed_specs_unique_{pipeline_id}.csv"
    # sidecar: per-spec run status/name/cypress, so export can colour cells and
    # bold the failure-cause job. Written beside the unique CSV so export's
    # auto-discovery finds it. Cleaned up with the other intermediates.
    sidecar_path = str(Path(args.unique_output).with_name(f"spec_runs_{pipeline_id}.json"))

    def job_url(job_id):
        return f"{GITLAB_BASE_URL}/{args.project}/-/jobs/{job_id}"

    sys.stderr.write(f"Fetching all jobs for pipeline {pipeline_id}...\n")
    all_jobs = fetch_all_jobs(args.project, pipeline_id)
    cypress_jobs = sorted(
        (j for j in all_jobs if is_cypress_job(j["name"])),
        key=lambda j: j["created_at"],
    )
    sys.stderr.write(f"Found {len(cypress_jobs)} cypress job attempt(s).\n")

    # spec (basename) -> chronological list of runs. Each run is one job attempt
    # the spec appeared in (via [SPEC START]/[SPEC END]) with its outcome.
    spec_runs = defaultdict(list)
    known_spec_paths = {}
    for job in cypress_jobs:
        sys.stderr.write(f"  job {job['id']} ({job['name']})...\n")
        trace = fetch_job_trace(args.project, job["id"])
        order, status = parse_spec_events(trace)
        cyurl = parse_cypress_run_url(trace)
        for full in order:
            base = os.path.basename(full)
            known_spec_paths.setdefault(base, full)
            spec_runs[base].append({
                "job_id": job["id"],
                "job_name": job["name"],
                "status": status.get(full) or "MISSING",
                "created_at": job["created_at"],
                "job_url": job_url(job["id"]),
                "cypress_url": cyurl,
            })
    for base in spec_runs:
        spec_runs[base].sort(key=lambda r: r["created_at"])

    all_specs = sorted(spec_runs)

    def is_fail(status):
        return status in ("FAILED", "MISSING")

    def passed_on_retry(runs):
        """Return (attempt_number, passed_run) if the spec failed then later
        passed, else None."""
        for i, r in enumerate(runs):
            if is_fail(r["status"]):
                for j in range(i + 1, len(runs)):
                    if runs[j]["status"] == "PASSED":
                        return j + 1, runs[j]
                break
        return None

    failed_specs = [s for s in all_specs if any(is_fail(r["status"]) for r in spec_runs[s])]

    # --- sidecar (for export colouring) ---
    with open(sidecar_path, "w") as fh:
        json.dump({s: {"runs": spec_runs[s]} for s in all_specs}, fh, indent=1)

    # --- all_specs CSV: every spec + its (up to 2) job links ---
    all_rows = []
    for spec in all_specs:
        runs = spec_runs[spec]
        all_rows.append({
            "Spec": spec,
            "first_job_url": runs[0]["job_url"] if runs else "",
            "second_job_url": runs[1]["job_url"] if len(runs) > 1 else "",
        })
    with open(args.output, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Spec", "first_job_url", "second_job_url"])
        writer.writeheader()
        writer.writerows(all_rows)
    sys.stderr.write(f"Wrote {len(all_rows)} spec(s) to {args.output}\n")

    # --- failed-specs unique CSV ---
    # Later steps fill columns in place: compare_new_failures.py -> New failure;
    # annotate_failure_cause.py -> failure_cause, bug_likelihood_(AI). Job/cypress
    # URLs are chronological attempts (all runs, not only failures); export
    # colours the failed ones red and bolds the failure-cause job.
    unique_rows = []
    for spec in failed_specs:
        runs = spec_runs[spec]
        por = passed_on_retry(runs)
        note = MISSING_OUTPUT_NOTE if any(r["status"] == "MISSING" for r in runs) else ""
        unique_rows.append({
            "Failed spec": spec,
            "Passed on retry": f"yes ({por[0]}) (#{por[1]['job_id']})" if por else "no",
            "New failure": "N/A",
            "bug_likelihood_(AI)": "",
            "Note": note,
            "Locally reproducible": "",
            "failure_cause": "",
            "first_cypress_url": runs[0]["cypress_url"] if runs else "",
            "second_cypress_url": runs[1]["cypress_url"] if len(runs) > 1 else "",
            "first_job_url": runs[0]["job_url"] if runs else "",
            "second_job_url": runs[1]["job_url"] if len(runs) > 1 else "",
        })
    with open(args.unique_output, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "Failed spec", "Passed on retry", "New failure", "bug_likelihood_(AI)",
                "Note", "Locally reproducible", "failure_cause",
                "first_cypress_url", "second_cypress_url", "first_job_url", "second_job_url",
            ],
        )
        writer.writeheader()
        writer.writerows(unique_rows)
    sys.stderr.write(f"Wrote {len(unique_rows)} failed spec(s) to {args.unique_output}\n")

    crashed = [s for s in failed_specs if any(r["status"] == "MISSING" for r in spec_runs[s])]
    if crashed:
        sys.stderr.write(f"{len(crashed)} spec(s) with a crashed job (JOB CRASHED): {', '.join(crashed)}\n")

    if not failed_specs:
        sys.stderr.write("\nNo failed Cypress specs to re-run.\n")
        return

    resolved, unresolved = resolve_spec_paths(failed_specs, known_paths=known_spec_paths)
    cmd = build_cypress_command(resolved)
    sys.stderr.write(
        f"\nTo re-run the {len(resolved)} failed spec(s), from paratoo-webapp/:\n\n"
    )
    print(cmd)
    if unresolved:
        sys.stderr.write(
            f"\nCould not locate {len(unresolved)} spec file(s) under "
            f"{CYPRESS_INTEGRATION_DIR} — add them manually if needed:\n"
        )
        for name in unresolved:
            sys.stderr.write(f"  {name}\n")


if __name__ == "__main__":
    main()
