# Changelog

All notable changes to this skill are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) and is tracked in the `version`
field of [`SKILL.md`](SKILL.md)'s frontmatter.

## [1.3.0] - 2026-07-22

### Added
- **`New failure` column** (3rd column) on `failed_specs_unique_<pipeline>.csv`,
  flagging specs that failed in this run but not the previous one:
  `yes` (newly introduced), `no` (pre-existing), `N/A` (no prior run / not
  compared). A new-failure that is also `bug_likelihood_(AI): HIGH` is the
  top-priority spec to re-run locally.
- **`compare_new_failures.py`** — deterministic comparison against the previous
  run's unique CSV. Auto-detects the most recently created
  `failed_specs_unique_*.csv` in the folder (`--detect-only` prints it so the
  skill can prompt "compare with X?"), or takes an explicit `--previous`. Only
  the `Failed spec` column is required in either CSV, so it survives column
  changes. First-time runs (no prior file) leave every row `N/A`.
- SKILL.md step 3: detect the prior unique CSV, ask the user via
  AskUserQuestion whether to compare, and populate `New failure` — asked only
  when a valid prior file exists.

### Changed
- Unique CSV column order is now (7 cols):
  `Failed spec, Passed on retry, New failure, bug_likelihood_(AI), Note,
  failure_cause, first_failed_job_url`. `pipeline_failed_specs.py` emits
  `New failure` defaulting to `N/A`.

## [1.2.0] - 2026-07-17

### Added
- **`bug_likelihood_(AI)` column** (LOW/MEDIUM/HIGH) on
  `failed_specs_unique_<pipeline>.csv`, separating likely real app bugs from
  Cypress-glitch false positives so users can re-run the HIGH ones locally
  first. `annotate_failure_cause.py` accepts mapping values as either a plain
  string (cause only, back-compatible) or
  `{"failure_cause": ..., "bug_likelihood": ...}`.
- The unique CSV now has a fixed six-column layout, in order:
  `Failed spec, Passed on retry, bug_likelihood_(AI), Note, failure_cause,
  first_failed_job_url`. `pipeline_failed_specs.py` emits all six (with empty
  placeholders for the two annotate-filled columns) so the column order is
  stable whether or not annotation has run.
- `extract_failures.py` output now carries the pipeline's commit **`sha`**
  (plus project/web_url) in a top-level header, and per spec: `spec_path`
  (repo path from `[SPEC START]` markers), `first_error_frames` (stack frames
  pointing into repo test code, e.g. `test/cypress/support/commands.js:1382`),
  and `first_error_spec_line`. Output shape changed from a bare list to
  `{pipeline_id, project, sha, web_url, specs: [...]}`.
- SKILL.md: new "Reading the code at the pipeline's commit" section — the
  project is open source on GitLab, so classification now reads the spec /
  custom-command code at the pipeline's SHA (local checkout via
  `git show <sha>:<path>`, or `glab api repository/files/<path>/raw?ref=<sha>`).

### Changed
- Classification workflow (SKILL.md step 4) now requires deriving the
  **test's intent** from code before labelling — a bare error like
  `expected false to be true` must be traced to what the assertion checks
  (e.g. a route assertion that the app "shouldn't have navigated").
  Deterministic behavior mismatches must be checked against app git history
  (`git log -S`) to distinguish real regressions from **stale tests** whose
  asserted behavior was intentionally changed.
- `reference/failure_taxonomy.md` reorganized around the glitch-vs-bug
  question: a "Known Cypress-glitch families" section (dropdown races,
  covered-by-popup clicks, listbox/options never rendering → LOW), an
  expanded app/data-signal table (exact-text label regressions, wrong list
  contents, data-shape changes, app error states → HIGH), a stale-test rule
  with a worked example (plot-context vs the quick-swap feature), and the
  full `bug_likelihood_(AI)` rubric.

## [1.1.1] - 2026-07-01

### Added
- `tests/` — unit tests for `pipeline_failed_specs.py`, `extract_failures.py`,
  and `annotate_failure_cause.py` (stdlib `unittest`, no dependencies). Covers
  `[SPEC START]`/`[SPEC END]` parsing (including the OOM-crash/missing-output
  case), retry detection, spec-path resolution, and CSV annotation. Run with
  `python3 -m unittest discover -s tests -v`.

### Fixed
- `.gitignore` now matches the pipeline-suffixed working-artifact filenames
  introduced in 1.1.0 (`failed_specs_*.csv`, `failures_raw_*.json`,
  `mapping_*.json`) instead of only the old fixed names.

## [1.1.0] - 2026-07-01

### Changed
- `pipeline_failed_specs.py` and `extract_failures.py` now default their
  output filenames to `failed_specs_<pipeline_id>.csv`,
  `failed_specs_unique_<pipeline_id>.csv`, and
  `failures_raw_<pipeline_id>.json` (previously fixed names with no pipeline
  id). This lets multiple pipelines be analyzed in the same folder without
  overwriting each other's output. Pass `-o`/`-u` explicitly to keep using a
  fixed name.
- `SKILL.md` workflow updated to use pipeline-suffixed filenames throughout,
  including `mapping_<pipeline_id>.json`.

## [1.0.0] - 2026-07-01

### Fixed
- `pipeline_failed_specs.py` no longer relies solely on Cypress's own
  `(Run Finished)` summary table to detect failed specs. That table is only
  printed if a job's Cypress process exits cleanly — jobs killed mid-batch
  (OOM, timeout) never print it, which silently dropped both the crashed
  spec *and* any real failures earlier in that same job. Detection is now
  based on the `[SPEC START]`/`[SPEC END]` markers CI wraps around every
  spec, which are always present.
- Spec path resolution (for the "re-run these specs" command) now uses the
  exact repo-relative path captured from those markers instead of guessing
  via a recursive filesystem glob.

### Added
- `Note` column on both `failed_specs.csv` and `failed_specs_unique.csv`:
  set to `Unable to find outputs` for any spec that started but never
  reached `[SPEC END]` (crash/timeout/OOM — pass/fail unknown).
