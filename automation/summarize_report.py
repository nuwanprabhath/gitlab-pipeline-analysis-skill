#!/usr/bin/env python3
"""One-line summary of a failed_specs_unique_<pid>.xlsx, for notifications.

Prints e.g. "5 unique failures — 3 HIGH, 1 new" (or "no failures").
Exits non-zero with a message on stderr if the workbook can't be read.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import xlsx  # noqa: E402  (stdlib-only reader shipped with the skill)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: summarize_report.py <failed_specs_unique_*.xlsx>")

    rows = xlsx.read_sheet(sys.argv[1])
    if not rows:
        sys.exit("empty workbook")

    header = [h.strip().lower() for h in rows[0]]
    data = [r for r in rows[1:] if any(c.strip() for c in r)]

    def col(name):
        return header.index(name) if name in header else None

    def count(idx, *wanted):
        if idx is None:
            return 0
        return sum(
            1 for r in data if idx < len(r) and r[idx].strip().lower() in wanted
        )

    total = len(data)
    if total == 0:
        print("no failures")
        return

    high = count(col("bug_likelihood_(ai)"), "high")
    new = count(col("new failure"), "yes")

    parts = []
    if high:
        parts.append("{} HIGH".format(high))
    if new:
        parts.append("{} new".format(new))

    summary = "{} unique failure{}".format(total, "" if total == 1 else "s")
    if parts:
        summary += " — " + ", ".join(parts)
    print(summary)


if __name__ == "__main__":
    main()
