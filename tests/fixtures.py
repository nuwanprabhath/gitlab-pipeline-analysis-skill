"""Shared helpers for building synthetic GitLab CI trace text in tests.

Real traces look like:
  2026-06-30T17:55:20.169452Z 01O   Running:  foo.cy.js   (1 of 17)

i.e. an RFC3339 timestamp, a short stream-id token, then the actual log
content. `clean_log()`/`clean()` in the scripts under test strip the
timestamp+token prefix and any ANSI color codes before parsing.
"""

TS = "2026-06-30T17:55:20.169452Z"


def gitlab_line(text, ts=TS, token="01O"):
    """Build one raw GitLab trace line with the timestamp+token prefix."""
    return f"{ts} {token} {text}"


def build_log(*lines):
    return "\n".join(lines) + "\n"
