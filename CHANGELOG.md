# Changelog

All notable changes to this skill are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) and is tracked in the `version`
field of [`SKILL.md`](SKILL.md)'s frontmatter.

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
