#!/usr/bin/env python3
"""
Populate the "New failure" column on a failed_specs_unique CSV by comparing
its failed specs against the PREVIOUS run's unique CSV.

"New failure" values (per spec in the current CSV):
  yes  - failed in this run but NOT in the previous run (newly introduced)
  no   - failed in both this run and the previous run (pre-existing)
  N/A  - no previous run to compare against (first-time run / comparison skipped)

Only the "Failed spec" column is required in either CSV, so this keeps working
even if other columns are added, removed, or reordered. The column is written
as the 3rd column if not already present.

Usage:
  # Compare against an explicit previous CSV:
  ./compare_new_failures.py --current failed_specs_unique_<pid>.csv \
      --previous failed_specs_unique_<prev>.csv

  # Auto-detect the previous CSV (most recently created failed_specs_unique_*.csv
  # in the same folder, other than --current) and compare:
  ./compare_new_failures.py --current failed_specs_unique_<pid>.csv

  # Just print the auto-detected previous file (empty line if none) and exit,
  # without modifying anything — used to drive the "compare with X?" prompt:
  ./compare_new_failures.py --current failed_specs_unique_<pid>.csv --detect-only

If no previous CSV is found/given, every row's "New failure" is set to N/A.
"""
import argparse
import csv
import sys
from pathlib import Path

NEW_FAILURE_COLUMN = "New failure"
NEW_FAILURE_POSITION = 2  # third column (0-indexed)
UNIQUE_CSV_GLOB = "failed_specs_unique_*.csv"


def _creation_time(path):
    """Best-effort creation time: st_birthtime where the platform provides it
    (macOS/BSD), else fall back to st_mtime."""
    st = path.stat()
    return getattr(st, "st_birthtime", st.st_mtime)


def find_previous_unique_csv(current_path):
    """Most-recently-created failed_specs_unique_*.csv in current's folder,
    excluding current itself. Returns a Path (in the same path style as
    `current_path`) or None."""
    current = Path(current_path)
    candidates = []
    for p in current.parent.glob(UNIQUE_CSV_GLOB):
        try:
            if p.samefile(current):
                continue
        except (FileNotFoundError, OSError):
            pass
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=_creation_time)


def _spec_index(header):
    lower = [h.strip().lower() for h in header]
    return lower.index("failed spec") if "failed spec" in lower else 0


def load_rows(path):
    with open(path, newline="") as fh:
        return [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]


def read_failed_specs(path):
    """Return the set of non-empty 'Failed spec' values in a unique CSV."""
    rows = load_rows(path)
    if not rows:
        return set()
    idx = _spec_index(rows[0])
    return {
        r[idx].strip()
        for r in rows[1:]
        if idx < len(r) and r[idx].strip()
    }


def mark_new_failures(current_path, previous_specs, output_path=None):
    """Add/refresh the 'New failure' column on current_path.

    previous_specs is a set of spec names, or None to mark everything N/A.
    """
    rows = load_rows(current_path)
    if not rows:
        sys.exit(f"{current_path} is empty")
    header = list(rows[0])
    sidx = _spec_index(header)
    # Pad short data rows to the header width.
    data = [r + [""] * (len(header) - len(r)) for r in rows[1:]]

    def verdict(spec):
        if previous_specs is None:
            return "N/A"
        return "yes" if spec not in previous_specs else "no"

    if NEW_FAILURE_COLUMN in header:
        cidx = header.index(NEW_FAILURE_COLUMN)
        out_header = header
        out_rows = data
        for r in out_rows:
            r[cidx] = verdict(r[sidx].strip())
    else:
        cidx = min(NEW_FAILURE_POSITION, len(header))
        out_header = header[:cidx] + [NEW_FAILURE_COLUMN] + header[cidx:]
        out_rows = [
            r[:cidx] + [verdict(r[sidx].strip())] + r[cidx:] for r in data
        ]

    dest = output_path or current_path
    with open(dest, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(out_header)
        writer.writerows(out_rows)
    return out_rows, cidx


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--current", required=True, help="current run's unique CSV")
    ap.add_argument(
        "--previous",
        help="previous run's unique CSV (default: auto-detect newest in folder)",
    )
    ap.add_argument("-o", "--output", help="output CSV (default: overwrite --current)")
    ap.add_argument(
        "--detect-only",
        action="store_true",
        help="print the auto-detected previous CSV path (blank if none) and exit",
    )
    ap.add_argument(
        "--no-previous",
        action="store_true",
        help="force N/A for every row (do not compare, even if a previous exists)",
    )
    args = ap.parse_args()

    if args.detect_only:
        prev = find_previous_unique_csv(args.current)
        print(str(prev) if prev else "")
        return

    if args.no_previous:
        previous_path = None
    elif args.previous:
        previous_path = Path(args.previous)
        if not previous_path.exists():
            sys.exit(f"previous CSV not found: {previous_path}")
    else:
        previous_path = find_previous_unique_csv(args.current)

    previous_specs = read_failed_specs(previous_path) if previous_path else None

    out_rows, cidx = mark_new_failures(args.current, previous_specs, args.output)

    dest = args.output or args.current
    if previous_path is None:
        sys.stderr.write(
            f"No previous run found — marked {len(out_rows)} row(s) 'N/A' in {dest}\n"
        )
    else:
        yes = sum(1 for r in out_rows if r[cidx] == "yes")
        sys.stderr.write(
            f"Compared against {previous_path}: {yes} new failure(s) of "
            f"{len(out_rows)} in {dest}\n"
        )
        if yes:
            sys.stderr.write("New failures:\n")
            sidx = _spec_index(load_rows(dest)[0])
            for r in out_rows:
                if r[cidx] == "yes":
                    sys.stderr.write(f"  {r[sidx]}\n")


if __name__ == "__main__":
    main()
