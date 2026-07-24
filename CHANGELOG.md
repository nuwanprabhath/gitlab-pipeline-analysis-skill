# Changelog

All notable changes to this skill are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) and is tracked in the `version`
field of [`SKILL.md`](SKILL.md)'s frontmatter.

## [1.9.1] - 2026-07-24

### Added
- The `Passed on retry` cell is now a **clickable link to the job the spec
  passed on** (the `#<job_id>` in `yes (N) (#id)`). The link's project/host
  base is borrowed from the row's own failed-job URL, so it stays
  project-agnostic; the cell text is unchanged.

## [1.9.0] - 2026-07-24

### Added
- **Job links now show the job number** as clickable text (linking to the full
  GitLab job URL) instead of the raw URL.
- **`second_failed_job_url` / `third_failed_job_url`** columns — a spec's 2nd
  and 3rd failed retry attempts (populated only when they exist).
- The one of the three job-link cells that the `failure_cause` came from (the
  bug-signal attempt) now has a **red background**, so the cell you click pairs
  with the cause you're reading.
- **`cypress_url`** column — the failure-cause job's Cypress Cloud run link
  (extracted from the trace's `Run URL:` line), shown as its job number.
- **`Locally reproducible`** — an empty column (left of `failure_cause`) for
  the user to fill in.
- `extract_failures.py` records `cypress_run_url` per spec; `xlsx.py` gained a
  hyperlink `display` text option and a red-hyperlink cell style.

### Changed
- Unique-CSV/XLSX column order is now:
  `Failed spec, Passed on retry, New failure, bug_likelihood_(AI), Note,
  Locally reproducible, failure_cause, cypress_url, first_failed_job_url,
  second_failed_job_url, third_failed_job_url`.

## [1.8.0] - 2026-07-23

### Fixed — real bug masked by a flakier retry
- `extract_failures.py` only read each job's **latest** attempt. When a job was
  retried and the latest attempt died earlier than a previous one (e.g. a
  flaky `cy.click` glitch at test 2.1) it never reached — and thus masked — a
  real bug an earlier attempt had exposed (a deep-equal data mismatch at test
  8.2). The tool then reported only the glitch.
- Now, for each spec still failing in the latest attempt, it scans **all
  earlier failed attempts** of that job and upgrades to the strongest bug
  signal across attempts (`value-mismatch`/`app-error` > `element-timeout`).
  The record keeps `latest_attempt_error` / `latest_attempt_job_id` as a
  breadcrumb of what the latest attempt showed instead. Specs whose latest
  attempt passed are still excluded (flaky/passed-on-retry).

## [1.7.0] - 2026-07-23

### Fixed / anti-bias (deterministic enforcement)
- The 1.6.0 prose guardrails did not stop the LLM re-masking a data-mismatch
  bug as a Cypress glitch on a fresh run. So the rule is now **enforced in
  code**, not just documented. New `error_kind_enforce.py` runs automatically
  inside both `annotate_failure_cause.py` and `export_xlsx.py`
  (auto-discovering `failures_raw_<pid>.json` — no flag needed):
  - a `value-mismatch`/`app-error` spec left at LOW/blank is raised to at
    least **MEDIUM** (it can never be shipped at LOW);
  - a glitch-style `failure_cause` on such a spec is **replaced with the real
    captured assertion**, so a hallucinated "covered by popup / dropdown"
    label can't hide a data bug. The original label is noted for transparency.
  - Enforcement is idempotent and never touches genuine `element-timeout`
    glitches (no false positives), and prints an `⚠ Enforced ...` summary.
- `extract_failures.py` records `bug_signal_error` — the specific signature
  that made a spec a bug signal — so the enforced cause quotes the real
  assertion even when the mismatch was promoted from a later signature behind
  a masking interaction line.

## [1.6.0] - 2026-07-23

### Fixed / anti-bias
- **Stop masking real bugs as Cypress glitches.** A data-mismatch failure
  (`expected {…} to deeply equal {…}`) was being labelled as a UI-overlay
  glitch and rated LOW — with a fabricated cause that referenced a selector
  not present anywhere in that spec's trace. Root cause was classification
  bias + cross-spec/hallucinated causes, not extraction (the extractor had
  captured the correct error). Mitigations:
  - `extract_failures.py` now tags each spec with a deterministic **`error_kind`**
    (`value-mismatch` / `app-error` = real-bug signal; `element-timeout` =
    the only glitch-eligible kind; `other`). It prints a "BUG-SIGNAL specs"
    list. `error_kind` surfaces the strongest bug signal among a spec's
    signatures, so a value/data mismatch can't be buried behind an earlier
    recovered interaction warning.
  - SKILL.md classification step: **hard grounding rules** — classify each
    spec only from its own captured error; never import a cause/selector from
    another spec, the taxonomy examples, or memory; `error_kind` gates the
    glitch bucket (value-mismatch/app-error can never be LOW/glitch); plus a
    self-check before writing the mapping.
  - `failure_taxonomy.md`: new rule 0 (error TYPE decides glitch-eligibility)
    and rule 1 (ground labels in this spec's own error); glitch-family table
    now only applies to `element-timeout` specs; added the object deep-equal
    HIGH signature.

## [1.5.0] - 2026-07-23

### Changed
- **A run now leaves only the two `.xlsx` files** in the working directory.
  The CSVs and JSON files are intermediates: `export_xlsx.py` gained
  `--remove-source` (deletes the input CSV after a successful export), and the
  workflow removes `failures_raw_<pid>.json` / `mapping_<pid>.json` at the end.
- SKILL.md now explicitly requires writing outputs **flat into the current
  working directory** and forbids creating a per-pipeline subfolder — isolating
  runs in subfolders silently breaks the `New failure` comparison, which only
  scans the current folder for prior runs.

## [1.4.1] - 2026-07-23

### Changed
- Exported `.xlsx` sheets now hide Excel's default cell gridlines
  (`showGridLines="0"`), so the report reads cleanly and copy-pastes into
  other/online spreadsheets without stray borders. (No cell borders were ever
  applied by the styles; this removes the display gridlines.)

## [1.4.0] - 2026-07-23

### Added
- **Formatted Excel (.xlsx) deliverables.** New `export_xlsx.py` turns a
  failed-specs CSV into a styled workbook:
  - rows sorted alphabetically by spec (empty specs last);
  - the job-URL column rendered as a clickable hyperlink;
  - cell background **red** where `bug_likelihood_(AI)` is HIGH or
    `New failure` is `yes`;
  - whole row background **green** where `Passed on retry` starts with `yes`
    (red cells win over green);
  - bold, frozen header and auto-sized columns.
  `failed_specs_unique_<pid>.xlsx` is now the primary deliverable; the CSVs
  become intermediate working files.
- **`xlsx.py`** — a tiny, dependency-free OOXML reader/writer (no `openpyxl`
  or any pip install), so Excel output works on any OS with a stock Python 3,
  consistent with the rest of the skill.
- SKILL.md step 7 exports both workbooks; the Output recap now leads with the
  `.xlsx` files.

### Changed
- `compare_new_failures.py` now detects and reads a previous run from either
  `.csv` or `.xlsx` (auto-detect globs both). Because it reads the previous
  run's `.xlsx`, the intermediate CSVs are safe to delete between runs — the
  `New failure` comparison keeps working with only the Excel deliverables kept.

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
