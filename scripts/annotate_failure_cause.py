#!/usr/bin/env python3
"""
Append (or refresh) a `failure_cause` column on failed_specs_unique.csv from a
JSON mapping of {spec_filename: cause}.

The mapping is produced by a human/agent reading failures_raw.json against the
taxonomy in reference/failure_taxonomy.md — classification needs judgement, so it
is intentionally NOT automated here.

Usage:
  ./annotate_failure_cause.py --mapping mapping.json [--csv failed_specs_unique.csv] [-o OUT.csv]

  mapping.json: { "foo.cy.js": "publish/upload flake (pre-existing)", ... }

Specs not present in the mapping get the --default value (UNCLASSIFIED) so gaps
are visible.
"""
import argparse
import csv
import json
import sys


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--mapping", required=True, help="JSON file: {spec_filename: cause}")
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

    out = []
    if args.column in header:
        col = header.index(args.column)
        out.append(header)
        for r in rows[1:]:
            r = r + [""] * (len(header) - len(r))
            r[col] = mapping.get(r[spec_idx].strip(), args.default)
            out.append(r)
    else:
        out.append(header + [args.column])
        for r in rows[1:]:
            out.append(r + [mapping.get(r[spec_idx].strip(), args.default)])

    dest = args.output or args.csv
    with open(dest, "w", newline="") as fh:
        csv.writer(fh).writerows(out)

    unclassified = [r[spec_idx] for r in out[1:] if r[-1] == args.default]
    sys.stderr.write(f"Wrote {len(out) - 1} row(s) to {dest}\n")
    sys.stderr.write(
        "Unclassified: " + (", ".join(unclassified) if unclassified else "none") + "\n"
    )


if __name__ == "__main__":
    main()
