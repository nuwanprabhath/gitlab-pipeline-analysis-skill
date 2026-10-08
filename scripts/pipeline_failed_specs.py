#!/usr/bin/env python3
"""
Map every Cypress spec in a GitLab CI pipeline to the job attempts it ran in.

Fetches all Cypress job attempts (jobs retry once, so max 2 attempts) —
including those that run in downstream child pipelines, such as the offline
suite (`cypress-offline-node-N` bridges → child pipelines) — parses each
trace's `[SPEC START]`/`[SPEC END]` markers for per-spec pass/fail, and writes:
  - all_specs_<pid>.csv        every spec + its 1st/2nd job attempt
  - failed_specs_unique_<pid>.csv  failed specs, classification columns blank
  - spec_runs_<pid>.json       sidecar with per-attempt status (for colouring)
A spec that started but never got an `[SPEC END]` (crash/timeout/OOM) is marked
Note = "JOB CRASHED". A failed Cypress job that never reached its first spec
(e.g. "Cypress verification timed out.") has no markers at all; its planned
specs are recovered from the offline shard table where possible and marked
Note = "NO SPECS RAN: <reason>", otherwise the job itself gets a row.

Usage:
  ./pipeline_failed_specs.py <pipeline_id_or_url> [-o ALL_SPECS.csv] [-u UNIQUE.csv] [-p PROJECT]

Requires `glab` to be installed and authenticated.
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
NO_SPECS_NOTE = "NO SPECS RAN"

# Why a Cypress job died before its first `[SPEC START]`, most specific first.
# The last pattern is GitLab's own footer, so any failed job has *some* reason.
_JOB_ABORT_RES = [
    re.compile(r"Cypress verification timed out\.?"),
    re.compile(r"Cypress failed to start[^\n]*"),
    re.compile(r"The cypress npm package is installed, but the Cypress binary is missing[^\n]*"),
    re.compile(r"Your system is missing the dependency[^\n]*"),
    re.compile(r"ERROR: Unknown CYPRESS_JOB_TYPE[^\n]*"),
    re.compile(r"ERROR: Job failed: [^\n]*"),
]
NO_MARKERS_REASON = "job failed before any spec started (no [SPEC START] markers)"


def detect_job_abort(log):
    """Return a one-line reason a Cypress job failed without running a spec."""
    log = clean_log(log)
    for rx in _JOB_ABORT_RES:
        m = rx.search(log)
        if m:
            return m.group(0).strip()
    return NO_MARKERS_REASON


_SPEC_GROUP_RE = re.compile(r":\s*\[([^\]]+)\]\s*$")


def spec_group_from_job_name(name):
    """`cypress-offline-child-3: [drones]` -> 'drones' (GitLab parallel:matrix
    suffix, which the CI passes to the shard script as SPEC_GROUP)."""
    m = _SPEC_GROUP_RE.search(name or "")
    return m.group(1) if m else ""


# The offline suite's shard table is code, not something the trace prints: the
# job runs `node scripts/cypress-parallel-offline.js --stdout` and only logs
# "Running N offline specs". Evaluating the same function at the pipeline's
# commit recovers which specs a job that never started was meant to run.
OFFLINE_SHARD_SCRIPT = "paratoo-webapp/scripts/cypress-parallel-offline.js"
_PLANNED_CACHE = {}
_NODE_EVAL = (
    "const m = require(process.argv[1]);"
    "const out = m.sortOfflineSpecs(Number(process.argv[2]), process.argv[3] || '');"
    "process.stdout.write(JSON.stringify(out));"
)


def fetch_repo_file(project, path, ref):
    project_enc = urllib.parse.quote(project, safe="")
    path_enc = urllib.parse.quote(path, safe="")
    return glab(f"projects/{project_enc}/repository/files/{path_enc}/raw?ref={ref}")


def fetch_pipeline_sha(project, pipeline_id):
    project_enc = urllib.parse.quote(project, safe="")
    try:
        return json.loads(glab(f"projects/{project_enc}/pipelines/{pipeline_id}")).get("sha", "")
    except (subprocess.CalledProcessError, ValueError):
        return ""


def planned_offline_specs(project, sha, node, spec_group):
    """Repo-relative spec paths an offline node/group was meant to run, or []
    when that can't be determined (no node, no `node` binary, file missing,
    script throws). Never raises: this only enriches the report."""
    key = (project, sha, str(node), spec_group)
    if key in _PLANNED_CACHE:
        return _PLANNED_CACHE[key]
    result = []
    try:
        if node and sha and shutil.which("node"):
            src = fetch_repo_file(project, OFFLINE_SHARD_SCRIPT, sha)
            with tempfile.TemporaryDirectory() as tmp:
                script = Path(tmp) / "cypress-parallel-offline.js"
                script.write_text(src)
                out = subprocess.run(
                    ["node", "-e", _NODE_EVAL, str(script), str(node), spec_group],
                    capture_output=True, text=True, timeout=30,
                )
            if out.returncode == 0:
                result = [
                    re.sub(r"^\./", "", p) for p in json.loads(out.stdout)
                    if isinstance(p, str) and p.endswith((".cy.js", ".cy.ts"))
                ]
            else:
                lines = out.stderr.strip().splitlines()
                err = next((ln for ln in lines if re.match(r"\s*\w*Error: ", ln)), lines[-1] if lines else "")
                sys.stderr.write(
                    f"  could not evaluate {OFFLINE_SHARD_SCRIPT} for node {node}"
                    f"{' [' + spec_group + ']' if spec_group else ''}: {err.strip()}\n"
                )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        sys.stderr.write(f"  could not resolve planned offline specs: {exc}\n")
    _PLANNED_CACHE[key] = result
    return result


def no_specs_key(job):
    """Row key for a job that ran no spec and whose planned specs are unknown.
    Includes the offline node: nodes 1/2/4 share the job name
    `cypress-offline-child`, and must not collapse into one row."""
    node = job.get("_offline_node", "")
    return f"{job['name']}{' node-' + node if node else ''} (no specs ran)"


def parse_cypress_run_url(log):
    """Return the Cypress Cloud run URL for a job trace, or '' if not recorded."""
    m = CYPRESS_RUN_URL_RE.search(ANSI_RE.sub("", log))
    return m.group(1).rstrip("│ ") if m else ""


def is_cypress_job(name):
    """True for any job that may run Cypress specs.

    Deliberately structural rather than an allowlist of known job names
    (`cypress-run`, `cypress-priority`, `cypress-smoke-test`, `cypress-setup`,
    …). An allowlist has to be extended every time CI grows a job, and until
    someone notices, that job's specs are missing from the report with no
    warning — `cypress-setup` was excluded on the incorrect assumption that it
    only builds the environment, silently dropping the specs it does run.

    Over-matching is cheap and safe: a job whose trace has no
    `[SPEC START]` markers contributes nothing. Under-matching loses failures.
    """
    return "cypress" in name.lower()


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


def fetch_bridges(project, pipeline_id):
    """Trigger (bridge) jobs of a pipeline, each pointing at a downstream child
    pipeline. Some Cypress suites run in child pipelines (e.g. the offline suite
    runs as `cypress-offline-node-N` bridges → child pipelines whose
    `cypress-offline-child` job holds the specs), so those jobs are NOT in the
    parent pipeline's own job list. Includes retried bridges: re-running a
    bridge triggers a NEW child pipeline and hides the old bridge (and its
    failed child) from the default listing, so without include_retried a
    failure that passed on a manual re-run vanishes from the report.
    Degrades to [] on older GitLab / no access."""
    project_enc = urllib.parse.quote(project, safe="")
    try:
        return glab_paginated(
            f"projects/{project_enc}/pipelines/{pipeline_id}/bridges"
            f"?per_page=100&include_retried=true"
        )
    except subprocess.CalledProcessError:
        return []


_OFFLINE_NODE_RE = re.compile(r"node-?(\d+)", re.I)


def gather_cypress_jobs(project, pipeline_id):
    """All Cypress job attempts for a pipeline, INCLUDING those that run in
    downstream child pipelines. Child-pipeline jobs are tagged with the parallel
    node index parsed from their triggering bridge name (`_offline_node`), so
    e.g. the offline suite's specs can be labelled `(offline)[node-N]`."""
    jobs = [dict(j) for j in fetch_all_jobs(project, pipeline_id)
            if is_cypress_job(j["name"])]
    for bridge in fetch_bridges(project, pipeline_id):
        child = bridge.get("downstream_pipeline") or {}
        child_id = child.get("id")
        if not child_id:
            continue  # bridge that didn't trigger a child (skipped/manual)
        m = _OFFLINE_NODE_RE.search(bridge.get("name", ""))
        node = m.group(1) if m else ""
        for j in fetch_all_jobs(project, child_id):
            if is_cypress_job(j["name"]):
                j = dict(j)
                j["_offline_node"] = node
                jobs.append(j)
    return sorted(jobs, key=lambda j: j["created_at"])


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

    sys.stderr.write(f"Fetching all jobs for pipeline {pipeline_id} (incl. child pipelines)...\n")
    cypress_jobs = gather_cypress_jobs(args.project, pipeline_id)
    sys.stderr.write(f"Found {len(cypress_jobs)} cypress job attempt(s).\n")

    # spec (basename) -> chronological list of runs. Each run is one job attempt
    # the spec appeared in (via [SPEC START]/[SPEC END]) with its outcome.
    spec_runs = defaultdict(list)
    known_spec_paths = {}
    no_spec_jobs = []
    sha = None
    for job in cypress_jobs:
        sys.stderr.write(f"  job {job['id']} ({job['name']})...\n")
        trace = fetch_job_trace(args.project, job["id"])
        order, status = parse_spec_events(trace)
        cyurl = parse_cypress_run_url(trace)

        def add_run(base, run_status, **extra):
            spec_runs[base].append({
                "job_id": job["id"],
                "job_name": job["name"],
                "stage": job.get("stage", ""),
                # parallel node index for child-pipeline (offline) jobs; "" otherwise
                "node": job.get("_offline_node", ""),
                "status": run_status,
                "created_at": job["created_at"],
                "job_url": job_url(job["id"]),
                "cypress_url": cyurl,
                **extra,
            })

        for full in order:
            base = os.path.basename(full)
            known_spec_paths.setdefault(base, full)
            add_run(base, status.get(full) or "MISSING")

        # A failed job with no spec markers never started a spec (Cypress
        # binary verification timeout, install failure, ...). Without this it
        # contributes nothing and its failure vanishes from the report.
        if not order and job.get("status") == "failed":
            reason = detect_job_abort(trace)
            planned = []
            if job.get("_offline_node"):
                if sha is None:
                    sha = fetch_pipeline_sha(args.project, pipeline_id)
                planned = planned_offline_specs(
                    args.project, sha, job["_offline_node"], spec_group_from_job_name(job["name"])
                )
            for full in planned:
                base = os.path.basename(full)
                known_spec_paths.setdefault(base, full)
                add_run(base, "NOT_RUN", abort_reason=reason)
            if not planned:
                add_run(no_specs_key(job), "NOT_RUN", abort_reason=reason)
            no_spec_jobs.append((job, reason, len(planned)))
    for base in spec_runs:
        spec_runs[base].sort(key=lambda r: r["created_at"])

    all_specs = sorted(spec_runs)

    def is_fail(status):
        return status in ("FAILED", "MISSING", "NOT_RUN")

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
        not_run = next((r for r in runs if r["status"] == "NOT_RUN"), None)
        if any(r["status"] == "MISSING" for r in runs):
            note = MISSING_OUTPUT_NOTE
        elif not_run:
            note = f"{NO_SPECS_NOTE}: {not_run.get('abort_reason') or NO_MARKERS_REASON}"
        else:
            note = ""
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
    if no_spec_jobs:
        sys.stderr.write(
            f"\n{len(no_spec_jobs)} failed cypress job(s) ran NO specs ({NO_SPECS_NOTE}):\n"
        )
        for job, reason, n in no_spec_jobs:
            what = f"{n} planned spec(s) marked NOT_RUN" if n else "planned specs unknown, job row added"
            sys.stderr.write(f"  {job['id']} {job['name']}: {reason} -- {what}\n")

    if not failed_specs:
        sys.stderr.write("\nNo failed Cypress specs to re-run.\n")
        return

    # `... (no specs ran)` job rows aren't spec files, so they can't be re-run.
    rerunnable = [s for s in failed_specs if s.endswith((".cy.js", ".cy.ts"))]
    if not rerunnable:
        return
    resolved, unresolved = resolve_spec_paths(rerunnable, known_paths=known_spec_paths)
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
