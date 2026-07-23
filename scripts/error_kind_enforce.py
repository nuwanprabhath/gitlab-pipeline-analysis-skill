#!/usr/bin/env python3
"""
Deterministic guardrail that stops real bugs being shipped as Cypress glitches.

The classification of `failure_cause` / `bug_likelihood_(AI)` is done by an LLM
and has proven unreliable at the one thing that matters most: it will pattern-
match a data/value mismatch to a UI-glitch family and bury it at LOW. Prose
instructions did not fix this, so this module enforces the rule in code, using
the deterministic `error_kind` that `extract_failures.py` already computes.

Invariant enforced on the final rows (per spec present in failures_raw):
  - `error_kind` is `value-mismatch` or `app-error`  → the app produced wrong
    output; this is a bug signal, never a glitch. Then:
      * bug_likelihood_(AI) must be at least MEDIUM (LOW/blank → MEDIUM);
      * a failure_cause that reads like a Cypress-glitch label is a mislabel —
        it's replaced with the real captured error (the original is preserved
        inline for transparency).

Both `annotate_failure_cause.py` and `export_xlsx.py` call this automatically
(auto-discovering `failures_raw_<pid>.json` next to the CSV), so it applies with
no extra flags and can't be silently skipped in the normal flow.
"""
import json
import re
from pathlib import Path

BUG_SIGNAL_KINDS = ("value-mismatch", "app-error")
_LEVEL_RANK = {"": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

# Phrases that indicate a Cypress-glitch / UI-interaction label. If one of these
# is used to describe a value-mismatch/app-error spec, it's a mislabel. Includes
# the literal word "glitch" because that's how the mislabels are written
# ("Cypress glitch: ..."), plus common interaction/timeout wording.
_GLITCH_PHRASES = (
    "glitch", "covered by popup", "overlay on click", "hidden from view",
    "dropdown", "listbox", "q-item", "q-menu", "q-card", "chevron",
    "multiselect", "clearstalemenu", "portal", "filter race",
    "found no matches", "did not filter", "never opened", "never rendered",
    "never populated", "never found", "never appeared", "field never",
    "timed out on", "stepper", "detached mid-click", "scrollintoview",
    "cy.click", "cy.filter", "interaction-timing", "interaction timeout",
)


def _pid_from_name(name):
    m = re.search(r"(\d+)(?=\.\w+$)", name) or re.search(r"(\d+)$", Path(name).stem)
    return m.group(1) if m else None


def discover_failures_raw(csv_path):
    """Find the failures_raw JSON that pairs with a unique CSV/XLSX, or None."""
    p = Path(csv_path)
    folder = p.parent
    pid = _pid_from_name(p.name)
    if pid:
        cand = folder / f"failures_raw_{pid}.json"
        if cand.exists():
            return cand
    globbed = sorted(folder.glob("failures_raw_*.json"))
    if len(globbed) == 1:
        return globbed[0]
    return None


def load_error_kinds(failures_raw_path):
    """Return {spec_filename: {"error_kind":..., "first_error":...}}."""
    with open(failures_raw_path) as fh:
        data = json.load(fh)
    specs = data.get("specs", data) if isinstance(data, dict) else data
    out = {}
    for rec in specs:
        # Prefer the specific bug-signal signature (the real assertion) over the
        # first_error, which may be a masking interaction line for promoted specs.
        signal = rec.get("bug_signal_error") or rec.get("first_error") or ""
        out[rec.get("spec", "")] = {
            "error_kind": rec.get("error_kind", "other"),
            "first_error": signal,
        }
    return out


def _looks_like_glitch_label(cause):
    low = cause.lower()
    return any(phrase in low for phrase in _GLITCH_PHRASES)


def enforce(header, data_rows, error_kinds):
    """Mutate data_rows in place to enforce the bug-signal invariant.

    header: list of column names. data_rows: list of list[str].
    error_kinds: {spec: {error_kind, first_error}}.
    Returns a list of human-readable correction messages (empty if none).
    """
    lower = [h.strip().lower() for h in header]

    def idx(*names):
        for n in names:
            if n in lower:
                return lower.index(n)
        return None

    spec_i = idx("failed spec")
    bug_i = idx("bug_likelihood_(ai)", "bug_likelihood")
    cause_i = idx("failure_cause")
    if spec_i is None or bug_i is None or cause_i is None:
        return []  # not an annotated unique sheet; nothing to enforce

    corrections = []
    for row in data_rows:
        if len(row) <= max(spec_i, bug_i, cause_i):
            row += [""] * (max(spec_i, bug_i, cause_i) + 1 - len(row))
        spec = row[spec_i].strip()
        info = error_kinds.get(spec)
        if not info or info["error_kind"] not in BUG_SIGNAL_KINDS:
            continue
        kind = info["error_kind"]
        first_error = info["first_error"]

        # 1) Floor the bug likelihood at MEDIUM.
        cur = row[bug_i].strip().upper()
        if _LEVEL_RANK.get(cur, 0) < _LEVEL_RANK["MEDIUM"]:
            row[bug_i] = "MEDIUM"
            corrections.append(
                f"{spec}: bug_likelihood {cur or 'blank'} -> MEDIUM (error_kind={kind})"
            )

        # 2) Replace a glitch-style cause with the real captured error.
        cause = row[cause_i].strip()
        # Idempotent: if we already rewrote this cause, don't wrap it again.
        already = cause.startswith(f"{kind}:") or "[auto-corrected" in cause
        if not already and _looks_like_glitch_label(cause) and first_error:
            row[cause_i] = (
                f"{kind}: {first_error[:220]} "
                f"[auto-corrected: original mislabel was \"{cause[:100]}\"]"
            )
            corrections.append(
                f"{spec}: cause looked like a Cypress-glitch label but error_kind="
                f"{kind}; replaced with captured error"
            )
    return corrections


def apply_to_csv_rows(csv_path, header, data_rows):
    """Convenience: auto-discover failures_raw for csv_path and enforce.
    Returns correction messages (empty list if no failures_raw found)."""
    fr = discover_failures_raw(csv_path)
    if not fr:
        return []
    return enforce(header, data_rows, load_error_kinds(fr))
