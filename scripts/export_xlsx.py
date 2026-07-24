#!/usr/bin/env python3
"""
Export a failed-specs CSV to a formatted .xlsx workbook (dependency-free).

Formatting applied automatically, driven by which columns are present:
  - Rows sorted alphabetically (case-insensitive) by "Failed spec"; rows with
    an empty spec (e.g. non-Cypress jobs) sort to the end.
  - Header row bold and frozen.
  - Any column whose header contains "url" is rendered as a clickable hyperlink.
  - Cell background RED when `bug_likelihood_(AI)` is HIGH, or `New failure`
    is yes (these are the specs worth re-running locally first).
  - Whole row background GREEN when `Passed on retry` starts with "yes"
    (flaky — passed on a later attempt). Red cells win over green.

Usage:
  ./export_xlsx.py --csv failed_specs_unique_<pid>.csv [-o OUT.xlsx] [--sheet NAME]

Default output path is the CSV path with a .xlsx extension. Only the standard
library is used, so this runs on any OS with a stock Python 3.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xlsx  # noqa: E402
import error_kind_enforce  # noqa: E402


def _find(header_lower, *names):
    """Return the index of the first matching column (by lowercased name), or None."""
    for name in names:
        if name in header_lower:
            return header_lower.index(name)
    return None


def _find_contains(header_lower, needle):
    for i, h in enumerate(header_lower):
        if needle in h:
            return i
    return None


def load_csv(path):
    with open(path, newline="") as fh:
        rows = [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]
    if not rows:
        sys.exit(f"{path} is empty")
    return rows[0], rows[1:]


_JOB_NUM_RE = re.compile(r"/jobs/(\d+)")
_RETRY_JOB_RE = re.compile(r"#(\d+)")


def _job_num(url):
    m = _JOB_NUM_RE.search(url or "")
    return m.group(1) if m else (url or "")


def _passed_on_retry_url(retry_value, sample_job_url):
    """Build the URL of the job the spec PASSED on, from a `Passed on retry`
    value like `yes (2) (#15505213166)` and any job URL in the same row (to
    borrow the project/host base). Returns '' when not applicable."""
    m = _RETRY_JOB_RE.search(retry_value or "")
    if not m or not sample_job_url or "/jobs/" not in sample_job_url:
        return ""
    return _JOB_NUM_RE.sub(f"/jobs/{m.group(1)}", sample_job_url, count=1)


def build_sheet(header, data, sheet_name, cause_jobs=None):
    """cause_jobs: optional {spec: job_id} — the failure-cause (bug-signal)
    job, so the matching one of the three job-URL cells is highlighted red."""
    cause_jobs = cause_jobs or {}
    header_lower = [h.strip().lower() for h in header]
    spec_idx = _find(header_lower, "failed spec")
    if spec_idx is None:
        spec_idx = 0
    likelihood_idx = _find(header_lower, "bug_likelihood_(ai)", "bug_likelihood")
    newfail_idx = _find(header_lower, "new failure")
    retry_idx = _find(header_lower, "passed on retry")
    cypress_idx = _find(header_lower, "cypress_url")
    job_url_idxs = {i for i, h in enumerate(header_lower) if h.endswith("_failed_job_url")}

    # Sort alphabetically by spec; empty specs last.
    def sort_key(row):
        spec = row[spec_idx].strip() if spec_idx < len(row) else ""
        return (spec == "", spec.lower())

    data = sorted(data, key=sort_key)

    out_rows = [[xlsx.Cell(h, xlsx.STYLE_HEADER) for h in header]]
    for row in data:
        row = row + [""] * (len(header) - len(row))
        spec = row[spec_idx].strip()
        cause_job = str(cause_jobs.get(spec) or "")
        row_green = (
            retry_idx is not None
            and row[retry_idx].strip().lower().startswith("yes")
        )
        # a job URL from this row, used to build the passed-on-retry job link
        sample_job_url = next(
            (row[j].strip() for j in sorted(job_url_idxs) if row[j].strip().startswith("http")),
            "",
        )
        cells = []
        for i, value in enumerate(row):
            value = value.strip()
            if i == retry_idx and value.lower().startswith("yes"):
                # Link the whole cell to the job the spec passed on (keep the
                # `yes (N) (#id)` text). Row is green (flaky) so use link-green.
                passed_url = _passed_on_retry_url(value, sample_job_url)
                if passed_url:
                    style = xlsx.STYLE_LINK_GREEN if row_green else xlsx.STYLE_LINK
                    cells.append(xlsx.Cell(passed_url, style, hyperlink=True, display=value))
                else:
                    cells.append(xlsx.Cell(value, xlsx.STYLE_GREEN if row_green else xlsx.STYLE_DEFAULT))
            elif i in job_url_idxs and value.startswith("http"):
                # Show the job number; link to the full URL. The cell for the
                # job the failure_cause is about gets a red background.
                num = _job_num(value)
                if num and num == cause_job:
                    style = xlsx.STYLE_LINK_RED
                elif row_green:
                    style = xlsx.STYLE_LINK_GREEN
                else:
                    style = xlsx.STYLE_LINK
                cells.append(xlsx.Cell(value, style, hyperlink=True, display=num))
            elif i == cypress_idx and value.startswith("http"):
                # Cypress Cloud link; show the failure-cause job number as text.
                style = xlsx.STYLE_LINK_GREEN if row_green else xlsx.STYLE_LINK
                cells.append(xlsx.Cell(value, style, hyperlink=True, display=cause_job or "cypress"))
            elif (
                (likelihood_idx is not None and i == likelihood_idx and value.upper() == "HIGH")
                or (newfail_idx is not None and i == newfail_idx and value.lower() == "yes")
            ):
                cells.append(xlsx.Cell(value, xlsx.STYLE_RED))
            elif row_green:
                cells.append(xlsx.Cell(value, xlsx.STYLE_GREEN))
            else:
                cells.append(xlsx.Cell(value))
        out_rows.append(cells)

    return xlsx.Sheet(sheet_name, out_rows)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, help="input CSV to export")
    ap.add_argument("-o", "--output", help="output .xlsx (default: CSV path with .xlsx)")
    ap.add_argument("--sheet", help="worksheet name (default: derived from filename)")
    ap.add_argument(
        "--remove-source",
        action="store_true",
        help="delete the input CSV after the .xlsx is written successfully "
        "(so a run leaves only the Excel deliverable)",
    )
    args = ap.parse_args()

    header, data = load_csv(args.csv)

    # Final deterministic backstop before the deliverable is written: a
    # value-mismatch/app-error spec can never be shipped as a glitch/LOW.
    # Auto-discovers failures_raw_<pid>.json next to the CSV; no-op if absent
    # or if this sheet has no bug_likelihood column (e.g. the per-job sheet).
    corrections = error_kind_enforce.apply_to_csv_rows(args.csv, header, data)
    if corrections:
        sys.stderr.write(
            f"⚠ Enforced {len(corrections)} bug-signal correction(s) before export "
            "(value-mismatch/app-error specs cannot be LOW/glitch):\n"
        )
        for c in corrections:
            sys.stderr.write(f"  {c}\n")

    # Which of the three job-URL cells the failure_cause is about (the
    # bug-signal job) — that cell is highlighted red. From failures_raw.
    cause_jobs = {}
    fr = error_kind_enforce.discover_failures_raw(args.csv)
    if fr:
        for spec, info in error_kind_enforce.load_error_kinds(fr).items():
            if info.get("job_id"):
                cause_jobs[spec] = info["job_id"]

    out_path = args.output or str(Path(args.csv).with_suffix(".xlsx"))
    sheet_name = args.sheet or Path(args.csv).stem
    sheet = build_sheet(header, data, sheet_name, cause_jobs=cause_jobs)
    xlsx.write_workbook(out_path, [sheet])
    sys.stderr.write(f"Wrote {len(data)} row(s) to {out_path}\n")

    if args.remove_source:
        src = Path(args.csv)
        if src.resolve() != Path(out_path).resolve():
            src.unlink(missing_ok=True)
            sys.stderr.write(f"Removed source {src}\n")


if __name__ == "__main__":
    main()
