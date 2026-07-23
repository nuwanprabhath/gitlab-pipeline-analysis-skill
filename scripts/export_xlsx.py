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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xlsx  # noqa: E402


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


def build_sheet(header, data, sheet_name):
    header_lower = [h.strip().lower() for h in header]
    spec_idx = _find(header_lower, "failed spec")
    if spec_idx is None:
        spec_idx = 0
    likelihood_idx = _find(header_lower, "bug_likelihood_(ai)", "bug_likelihood")
    newfail_idx = _find(header_lower, "new failure")
    retry_idx = _find(header_lower, "passed on retry")
    url_idx = _find_contains(header_lower, "url")

    # Sort alphabetically by spec; empty specs last.
    def sort_key(row):
        spec = row[spec_idx].strip() if spec_idx < len(row) else ""
        return (spec == "", spec.lower())

    data = sorted(data, key=sort_key)

    out_rows = [[xlsx.Cell(h, xlsx.STYLE_HEADER) for h in header]]
    for row in data:
        row = row + [""] * (len(header) - len(row))
        row_green = (
            retry_idx is not None
            and row[retry_idx].strip().lower().startswith("yes")
        )
        cells = []
        for i, value in enumerate(row):
            value = value.strip()
            is_url = url_idx is not None and i == url_idx and value.startswith("http")
            red = (
                (likelihood_idx is not None and i == likelihood_idx and value.upper() == "HIGH")
                or (newfail_idx is not None and i == newfail_idx and value.lower() == "yes")
            )
            if is_url:
                style = xlsx.STYLE_LINK_GREEN if row_green else xlsx.STYLE_LINK
                cells.append(xlsx.Cell(value, style, hyperlink=True))
            elif red:
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
    out_path = args.output or str(Path(args.csv).with_suffix(".xlsx"))
    sheet_name = args.sheet or Path(args.csv).stem
    sheet = build_sheet(header, data, sheet_name)
    xlsx.write_workbook(out_path, [sheet])
    sys.stderr.write(f"Wrote {len(data)} row(s) to {out_path}\n")

    if args.remove_source:
        src = Path(args.csv)
        if src.resolve() != Path(out_path).resolve():
            src.unlink(missing_ok=True)
            sys.stderr.write(f"Removed source {src}\n")


if __name__ == "__main__":
    main()
