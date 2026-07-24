#!/usr/bin/env python3
"""
Extract the ROOT failure (first failing test + its error) for each failed
Cypress spec in a GitLab pipeline. Feeds failure_cause classification.

For every cypress-run / cypress-priority job whose LATEST attempt failed (so
specs that passed on retry are excluded — those are flaky), it captures the
first `N) <test>` block's error line plus every distinct error signature. It
then scans EARLIER failed attempts of the same job and, for a spec still
failing in the latest attempt, upgrades to the strongest bug signal across
attempts — so a flaky early glitch in the latest attempt can't mask a real
bug an earlier attempt exposed (`latest_attempt_error` records what the latest
attempt showed instead).

Outputs JSON (default: failures_raw_<pipeline>.json):
  { "pipeline_id", "project", "sha", "web_url",
    "specs": [ { "spec", "spec_path", "job_id", "job_name", "first_test",
                 "first_error", "first_error_spec_line", "error_kind",
                 "signatures": [...] } ] }

`error_kind` is a deterministic anti-bias hint: `value-mismatch` / `app-error`
mean the app produced wrong output/state (a real-bug signal — never a Cypress
glitch), `element-timeout` is the only kind eligible for the glitch/LOW bucket.

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

# Deterministic error-kind classifier. This is a DELIBERATE anti-bias signal:
# a classifier reading only the error text tends to pattern-match UI-glitch
# phrases and wave failures away as LOW. But a value/data mismatch or an app
# error state is the app producing wrong output — a real-bug signal — no matter
# what the surrounding UI did. So we tag the kind deterministically and the
# taxonomy keys the glitch/LOW bucket ONLY off "element-timeout".
#
# Order matters: value-mismatch and app-error win over interaction phrases.
_VALUE_MISMATCH_RE = re.compile(
    r"to deeply equal|to equal|to eql|expected .+ to be\b|expected -?\d+ to |"
    r"Not enough elements found|Too many elements found|to have lengthOf|"
    r"to have length|expected '.*' to (?:equal|contain|include)|"
    r"expected .+ to (?:contain|include|match)\b",
    re.I,
)
_APP_ERROR_RE = re.compile(
    r"Failed to publish|Failed to load|Failed to upload|Failed while doing fetch|"
    r"Org is unavailable|No response from server|Bad request|Unregistered model case|"
    r"Cannot read propert|is not unique|Unable to start data collection|"
    r"but continuously found it",  # "expected not to find content: '<app error>' but continuously found it"
    re.I,
)
_INTERACTION_RE = re.compile(
    r"hidden from view|cy\.click\(\).*failed|cy\.filter\(\)|never found|never appeared|"
    r"not to exist.*continuously found|did not filter|found no matches|"
    r"no longer attached|page updated|requires a DOM|scroll to \d|"
    r"\.q-menu|chevron|clearStaleMenuPortals|multiselect__tags",
    re.I,
)


def classify_error_kind(text):
    """Return a coarse, deterministic error kind used to gate glitch vs bug:
      value-mismatch  - deterministic value/data/count assertion (STRONG bug signal)
      app-error       - app produced an error state / bad data shape (bug signal)
      element-timeout - element-find / interaction / dropdown timeout (glitch-ELIGIBLE)
      other           - anything else (treat as unknown, worth a look)
    Only 'element-timeout' failures may be classified as a Cypress glitch/LOW."""
    if not text:
        return "other"
    if _VALUE_MISMATCH_RE.search(text):
        return "value-mismatch"
    if _APP_ERROR_RE.search(text):
        return "app-error"
    if _INTERACTION_RE.search(text):
        return "element-timeout"
    return "other"
# Cypress Cloud run URL, printed once near the top of a recorded run's trace:
#   │ Run URL:        https://cloud.cypress.io/projects/6b9ofw/runs/12361 │
CYPRESS_RUN_URL_RE = re.compile(r"Run URL:\s*(https?://\S*?cypress\.io/\S+)")


def parse_cypress_run_url(trace):
    """Return the Cypress Cloud run URL for a job trace, or '' if not recorded."""
    m = CYPRESS_RUN_URL_RE.search(ANSI_RE.sub("", trace))
    return m.group(1).rstrip("│ ") if m else ""


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


# Strength ordering of error kinds — used to pick, across a job's retries, the
# attempt that exposes the real bug rather than whichever failed last.
KIND_RANK = {"value-mismatch": 3, "app-error": 3, "element-timeout": 1, "other": 0}


def cypress_job_attempts(project, pipeline_id):
    """Return {job_name: [attempts sorted oldest→newest]} for every
    cypress-run / cypress-priority job (all retries included)."""
    enc = urllib.parse.quote(project, safe="")
    path = f"projects/{enc}/pipelines/{pipeline_id}/jobs?per_page=100&include_retried=true"
    jobs = json.loads(glab(path))
    by_name = defaultdict(list)
    for j in jobs:
        if "cypress-run" in j["name"] or "cypress-priority" in j["name"]:
            by_name[j["name"]].append(j)
    for name in by_name:
        by_name[name].sort(key=lambda x: x["created_at"])
    return by_name


def parse_spec_failures(trace, job_id, job_name):
    """Return OrderedDict spec -> {job_id, job_name, spec_path, first_test,
    first_error, first_error_spec_line, cypress_run_url, signatures}."""
    cypress_run_url = parse_cypress_run_url(trace)
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
             "first_error_frames": [], "error_kind": "other", "bug_signal_error": "",
             "cypress_run_url": "", "signatures": []},
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
        rec["cypress_run_url"] = cypress_run_url
        # error_kind reflects the first_error; if that's an interaction timeout
        # but ANY signature is a value/data mismatch, prefer the mismatch (the
        # real failure often follows a recovered/ignored interaction warning).
        # `bug_signal_error` is the specific signature that makes it a bug
        # signal, so downstream can quote the real assertion (not a masking
        # interaction line).
        rec["error_kind"] = classify_error_kind(rec["first_error"])
        rec["bug_signal_error"] = (
            rec["first_error"] if rec["error_kind"] in ("value-mismatch", "app-error") else ""
        )
        if rec["error_kind"] in ("element-timeout", "other"):
            for sig in rec["signatures"]:
                kind = classify_error_kind(sig)
                if kind in ("value-mismatch", "app-error"):
                    rec["error_kind"] = kind
                    rec["bug_signal_error"] = sig
                    break
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
    sys.stderr.write(f"Fetching cypress job attempts for pipeline {pid}...\n")
    by_name = cypress_job_attempts(args.project, pid)
    enc = urllib.parse.quote(args.project, safe="")

    merged = OrderedDict()
    for name, attempts in by_name.items():
        latest = attempts[-1]
        if latest["status"] != "failed":
            continue  # spec(s) passed on the latest retry — flaky, not a hard failure

        sys.stderr.write(f"  parsing latest {latest['id']} ({name})...\n")
        latest_recs = parse_spec_failures(
            glab(f"projects/{enc}/jobs/{latest['id']}/trace"), latest["id"], name
        )
        if not latest_recs:
            continue
        for spec, rec in latest_recs.items():
            merged.setdefault(spec, rec)

        # A flaky early failure (e.g. a cy.click glitch) in the latest attempt
        # can die before reaching — and thus mask — a real bug an EARLIER
        # attempt exposed. So for each spec still failing in the latest attempt,
        # scan earlier failed attempts and upgrade to the strongest bug signal.
        for att in attempts[:-1]:
            if att["status"] != "failed":
                continue
            earlier = parse_spec_failures(
                glab(f"projects/{enc}/jobs/{att['id']}/trace"), att["id"], name
            )
            for spec in latest_recs:
                cand = earlier.get(spec)
                if not cand:
                    continue
                cur = merged[spec]
                if KIND_RANK[cand["error_kind"]] > KIND_RANK[cur["error_kind"]]:
                    # keep a breadcrumb of what the latest attempt showed instead
                    cand["latest_attempt_error"] = latest_recs[spec]["first_error"]
                    cand["latest_attempt_job_id"] = latest["id"]
                    merged[spec] = cand
                    sys.stderr.write(
                        f"    ↑ {spec}: stronger bug signal ({cand['error_kind']}) in "
                        f"earlier attempt {att['id']} — latest attempt only showed "
                        f"{cur['error_kind']}\n"
                    )

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
    # Human-readable preview for quick classification. The [kind] tag flags the
    # bug-signal category: value-mismatch / app-error are NOT glitches.
    for rec in out["specs"]:
        detail = rec["first_error"] or rec["first_test"] or "(no error captured)"
        line = f":{rec['first_error_spec_line']}" if rec["first_error_spec_line"] else ""
        sys.stderr.write(f"  - {rec['spec']}{line} [{rec['error_kind']}]: {detail}\n")
    bug_signals = [r["spec"] for r in out["specs"] if r["error_kind"] in ("value-mismatch", "app-error")]
    if bug_signals:
        sys.stderr.write(
            "\nBUG-SIGNAL specs (deterministic value/data/app-error — NOT Cypress "
            "glitches; classify MEDIUM/HIGH after reading the assertion):\n  "
            + "\n  ".join(bug_signals) + "\n"
        )


if __name__ == "__main__":
    main()
