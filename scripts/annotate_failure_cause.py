#!/usr/bin/env python3
"""
Append (or refresh) `failure_cause` and `bug_likelihood_(AI)` columns on
failed_specs_unique CSV from a JSON mapping.

The mapping is produced by a human/agent reading failures_raw JSON against the
taxonomy in reference/failure_taxonomy.md — classification needs judgement, so it
is intentionally NOT automated here.

Usage:
  ./annotate_failure_cause.py --mapping mapping.json [--csv failed_specs_unique.csv] [-o OUT.csv]

  mapping.json values are either a plain string (failure_cause only):
    { "foo.cy.js": "publish/upload flake (pre-existing)" }
  or an object with a bug-likelihood verdict:
    { "foo.cy.js": {"failure_cause": "app label regression (missing Stratum suffix)",
                    "bug_likelihood": "HIGH"} }

  bug_likelihood must be LOW, MEDIUM, or HIGH (see the rubric in
  reference/failure_taxonomy.md). Specs not present in the mapping get the
  --default value (UNCLASSIFIED) so gaps are visible.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import error_kind_enforce  # noqa: E402

LIKELIHOOD_COLUMN = "bug_likelihood_(AI)"
VALID_LIKELIHOODS = {"LOW", "MEDIUM", "HIGH"}


def parse_mapping_entry(value, default):
    """Return (failure_cause, bug_likelihood) from a mapping value that is
    either a plain string or {"failure_cause": ..., "bug_likelihood": ...}."""
    if isinstance(value, dict):
        cause = value.get("failure_cause", default)
        likelihood = str(value.get("bug_likelihood", "")).upper().strip()
        if likelihood and likelihood not in VALID_LIKELIHOODS:
            sys.exit(
                f"Invalid bug_likelihood {likelihood!r} (must be one of "
                f"{sorted(VALID_LIKELIHOODS)})"
            )
        return cause, likelihood
    return value, ""


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--mapping", required=True, help="JSON file: {spec_filename: cause-or-object}")
    ap.add_argument("--csv", default="failed_specs_unique.csv")
    ap.add_argument("-o", "--output", help="output CSV (default: overwrite --csv in place)")
    ap.add_argument("--column", default="failure_cause")
    ap.add_argument("--default", default="UNCLASSIFIED")
    args = ap.parse_args()

    with open(args.mapping) as fh:
        mapping = json.load(fh)

    rows = [r for r in csv.reader(open(args.csv)) if r and any(c.strip() for c in r)]
    if not rows:
        sys.exit(f"{args.csv} is empty")

    header = rows[0]
    lower = [h.strip().lower() for h in header]
    spec_idx = lower.index("failed spec") if "failed spec" in lower else 0

    out_header = list(header)
    for column in (args.column, LIKELIHOOD_COLUMN):
        if column not in out_header:
            out_header.append(column)
    cause_idx = out_header.index(args.column)
    likelihood_idx = out_header.index(LIKELIHOOD_COLUMN)

    out = [out_header]
    for r in rows[1:]:
        r = r + [""] * (len(out_header) - len(r))
        cause, likelihood = parse_mapping_entry(
            mapping.get(r[spec_idx].strip(), args.default), args.default
        )
        r[cause_idx] = cause
        r[likelihood_idx] = likelihood
        out.append(r)

    # Deterministic guardrail: a value-mismatch/app-error spec can't be shipped
    # as a glitch/LOW. Auto-discovers failures_raw_<pid>.json next to the CSV.
    corrections = error_kind_enforce.apply_to_csv_rows(args.csv, out[0], out[1:])

    dest = args.output or args.csv
    with open(dest, "w", newline="") as fh:
        csv.writer(fh).writerows(out)

    if corrections:
        sys.stderr.write(
            f"\n⚠ Enforced {len(corrections)} bug-signal correction(s) "
            "(value-mismatch/app-error specs cannot be LOW/glitch):\n"
        )
        for c in corrections:
            sys.stderr.write(f"  {c}\n")
        sys.stderr.write("")

    unclassified = [r[spec_idx] for r in out[1:] if r[cause_idx] == args.default]
    no_likelihood = [
        r[spec_idx] for r in out[1:]
        if not r[likelihood_idx] and r[cause_idx] != args.default
    ]
    sys.stderr.write(f"Wrote {len(out) - 1} row(s) to {dest}\n")
    sys.stderr.write(
        "Unclassified: " + (", ".join(unclassified) if unclassified else "none") + "\n"
    )
    if no_likelihood:
        sys.stderr.write(
            "Missing bug_likelihood (string-form mapping entries): "
            + ", ".join(no_likelihood) + "\n"
        )


if __name__ == "__main__":
    main()
