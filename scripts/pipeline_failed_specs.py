#!/usr/bin/env python3
"""
Extract all failed Cypress specs from a GitLab CI pipeline.

For each failed job in the pipeline (including retries), this script
downloads the job trace, parses the `[SPEC START]`/`[SPEC END]` markers CI
wraps around each spec, and emits one CSV row per failed spec. Specs that
started but never got an `[SPEC END]` (job crashed/timed out/OOM-killed
mid-spec) are emitted too, with Note = "Unable to find outputs".

Usage:
  ./pipeline_failed_specs.py <pipeline_id_or_url> [-o OUTPUT.csv] [-p PROJECT]

Examples:
  ./pipeline_failed_specs.py 2586657275
  ./pipeline_failed_specs.py https://gitlab.com/ternandsparrow/paratoo-fdcp/-/pipelines/2466892610
  ./pipeline_failed_specs.py 2466892610 -o failures.csv

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
MISSING_OUTPUT_NOTE = "Unable to find outputs"


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
        help="output CSV path (default: failed_specs_<pipeline_id>.csv, so multiple "
             "pipelines can be analyzed in the same folder without overwriting)",
    )
    parser.add_argument(
        "-u", "--unique-output", default=None,
        help="unique-specs CSV path (default: failed_specs_unique_<pipeline_id>.csv)",
    )
    parser.add_argument("-p", "--project", default=DEFAULT_PROJECT, help=f"GitLab project path (default: {DEFAULT_PROJECT})")
    args = parser.parse_args()

    pipeline_id = parse_pipeline_id(args.pipeline)
    if args.output is None:
        args.output = f"failed_specs_{pipeline_id}.csv"
    if args.unique_output is None:
        args.unique_output = f"failed_specs_unique_{pipeline_id}.csv"

    sys.stderr.write(f"Fetching all jobs for pipeline {pipeline_id}...\n")
    all_jobs = fetch_all_jobs(args.project, pipeline_id)
    failed_jobs = [j for j in all_jobs if j["status"] == "failed"]
    sys.stderr.write(f"Found {len(failed_jobs)} failed job(s) out of {len(all_jobs)} total.\n")

    # Group ALL jobs by name (sorted chronologically) to track retry attempts
    jobs_by_name = defaultdict(list)
    for job in sorted(all_jobs, key=lambda j: j["created_at"]):
        jobs_by_name[job["name"]].append(job)

    # Group job instances by name so we can emit a stable "Job" column per group
    group_first_id = {}
    for job in sorted(failed_jobs, key=lambda j: j["created_at"]):
        group_first_id.setdefault(job["name"], job["id"])

    rows = []
    no_spec_jobs = []
    missing_output_jobs = []
    # Track which specs failed in which job instances: {job_id: [spec, ...]}.
    # Specs whose output went missing are folded in here too, so the existing
    # retry-detection / first-failed-job logic below picks them up for free.
    failed_specs_by_job_id = {}
    # Specs that had at least one job attempt with no summary-table entry
    missing_output_specs = set()
    # Exact repo-relative spec paths seen via [SPEC START]/[SPEC END] markers,
    # keyed by basename — the ground truth for re-run path resolution.
    known_spec_paths = {}

    # Process newest-first so the CSV lists the latest retries at the top
    for job in sorted(failed_jobs, key=lambda j: j["created_at"], reverse=True):
        job_id = job["id"]
        name = job["name"]
        sys.stderr.write(f"  job {job_id} ({name})...\n")
        trace = fetch_job_trace(args.project, job_id)
        specs = parse_failed_specs(trace)
        missing = [s for s in find_missing_output_specs(trace) if s not in specs]
        failed_specs_by_job_id[job_id] = specs + missing
        for basename, full_path in parse_spec_full_paths(trace).items():
            known_spec_paths.setdefault(basename, full_path)
        group_label = f"#{group_first_id[name]}: {name}"
        retry_label = f"#{job_id}: {name}"
        if not specs and not missing:
            no_spec_jobs.append((job_id, name))
            rows.append({
                "Job": group_label,
                "Related jobs": retry_label,
                "Failed spec": "",
                "Note": "",
            })
            continue
        for spec in specs:
            rows.append({
                "Job": group_label,
                "Related jobs": retry_label,
                "Failed spec": spec,
                "Note": "",
            })
        if missing:
            missing_output_jobs.append((job_id, name, missing))
            missing_output_specs.update(missing)
        for spec in missing:
            rows.append({
                "Job": group_label,
                "Related jobs": retry_label,
                "Failed spec": spec,
                "Note": MISSING_OUTPUT_NOTE,
            })

    with open(args.output, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Job", "Related jobs", "Failed spec", "Note"])
        writer.writeheader()
        writer.writerows(rows)

    sys.stderr.write(f"\nWrote {len(rows)} row(s) to {args.output}\n")
    if no_spec_jobs:
        sys.stderr.write(
            f"{len(no_spec_jobs)} job(s) had no Cypress '(Run Finished)' summary "
            "(likely non-Cypress failures, e.g. commitlint/setup/sonarcloud):\n"
        )
        for jid, name in no_spec_jobs:
            sys.stderr.write(f"  #{jid} {name}\n")
    if missing_output_jobs:
        sys.stderr.write(
            f"{len(missing_output_jobs)} job(s) started a spec that never reached "
            "the Cypress summary table (crash/timeout/OOM) — outcome unknown:\n"
        )
        for jid, name, missing in missing_output_jobs:
            sys.stderr.write(f"  #{jid} {name}: {', '.join(missing)}\n")

    # Build a de-duplicated list of failed spec filenames, preserving discovery order
    unique_specs = []
    seen = set()
    for row in rows:
        name = row["Failed spec"]
        if name and name not in seen:
            seen.add(name)
            unique_specs.append(name)

    # Determine "Passed on retry" for each unique spec.
    # For each spec, find which job name ran it, then check if a later attempt
    # of that same job name either succeeded or no longer lists the spec as failed.
    # Map each spec to the job name(s) that ran it
    spec_to_job_names = defaultdict(set)
    for job in failed_jobs:
        for spec in failed_specs_by_job_id.get(job["id"], []):
            spec_to_job_names[spec].add(job["name"])

    def find_passed_on_retry(spec):
        """Return (attempt_number, job_id) where the spec passed, or None."""
        for job_name in spec_to_job_names.get(spec, []):
            attempts = jobs_by_name[job_name]
            # Find the first attempt where this spec failed
            first_fail_idx = None
            for i, attempt in enumerate(attempts):
                if attempt["id"] in failed_specs_by_job_id and spec in failed_specs_by_job_id[attempt["id"]]:
                    first_fail_idx = i
                    break
            if first_fail_idx is None:
                continue
            # Look at subsequent attempts
            for i in range(first_fail_idx + 1, len(attempts)):
                attempt = attempts[i]
                if attempt["status"] == "success":
                    return i + 1, attempt["id"]
                if attempt["id"] in failed_specs_by_job_id:
                    if spec not in failed_specs_by_job_id[attempt["id"]]:
                        return i + 1, attempt["id"]
        return None

    passed_on_retry = {}
    for spec in unique_specs:
        result = find_passed_on_retry(spec)
        if result is not None:
            retry_num, job_id = result
            passed_on_retry[spec] = f"yes ({retry_num}) (#{job_id})"
        else:
            passed_on_retry[spec] = "no"

    def find_first_failed_job_id(spec):
        """Return the job_id of the chronologically earliest attempt where this spec failed."""
        first_job_id = None
        first_created_at = None
        for job_name in spec_to_job_names.get(spec, []):
            for attempt in jobs_by_name[job_name]:  # already sorted by created_at
                if attempt["id"] in failed_specs_by_job_id and spec in failed_specs_by_job_id[attempt["id"]]:
                    if first_created_at is None or attempt["created_at"] < first_created_at:
                        first_created_at = attempt["created_at"]
                        first_job_id = attempt["id"]
                    break  # earliest for this job_name found; move to next job_name
        return first_job_id

    first_failed_job_url = {}
    for spec in unique_specs:
        job_id = find_first_failed_job_id(spec)
        if job_id is not None:
            first_failed_job_url[spec] = f"{GITLAB_BASE_URL}/{args.project}/-/jobs/{job_id}"
        else:
            first_failed_job_url[spec] = ""

    resolved, unresolved = resolve_spec_paths(unique_specs, known_paths=known_spec_paths)

    unique_rows = [
        {
            "Failed spec": spec,
            "Passed on retry": passed_on_retry[spec],
            "first_failed_job_url": first_failed_job_url[spec],
            "Note": MISSING_OUTPUT_NOTE if spec in missing_output_specs else "",
        }
        for spec in unique_specs
    ]
    with open(args.unique_output, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["Failed spec", "Passed on retry", "first_failed_job_url", "Note"]
        )
        writer.writeheader()
        writer.writerows(unique_rows)
    sys.stderr.write(f"Wrote {len(unique_rows)} unique spec(s) to {args.unique_output}\n")

    if not unique_specs:
        sys.stderr.write("\nNo failed Cypress specs to re-run.\n")
        return

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
