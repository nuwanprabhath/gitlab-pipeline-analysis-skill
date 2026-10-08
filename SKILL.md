---
name: gitlab-pipeline-analysis
description: >-
  Triage failed Cypress specs in a GitLab CI pipeline. Given a pipeline number or
  URL, produce formatted Excel (.xlsx) reports of failed specs with a New failure
  column (regressions vs the previous run), bug_likelihood_(AI) and failure_cause
  (separating real app bugs from Cypress glitches), colour-coded cells and
  clickable job links, then group failures by root cause and offer to open a
  GitLab issue for the dominant cluster. Use when asked to investigate, analyze,
  triage, or summarize CI / pipeline test failures, or to classify why specs failed.
version: 2.2.1
---

# GitLab Pipeline Failure Analysis

Turn a GitLab pipeline number into: (1) two CSVs of failed specs, (2) a
`failure_cause` for each unique spec, (3) a grouped breakdown, and (4) an
optional GitLab ticket for the most actionable cluster.

## Prerequisites (check first)

- `glab` installed and authenticated — run `glab auth status`. If not, stop and
  tell the user to run `glab auth login`.
- `python3` available.
- Default project is `ternandsparrow/paratoo-fdcp`; pass `-p <group/project>` to
  every script for a different repo.

Let `SKILL_DIR` be the directory containing this file. Run scripts as
`python3 "$SKILL_DIR/scripts/<name>.py" ...`.

**Write all outputs FLAT into the user's current working directory** (or a
single path they specify) — never into the skill repo, and **do NOT create a
per-pipeline subfolder**. Every filename is already suffixed with the pipeline
id (`_<pipeline>`), so multiple pipelines coexist safely in one folder. This
matters: the "New failure" comparison (step 3) only scans the *current* folder
for prior runs, so isolating each run in its own subfolder silently breaks
regression detection.

A run leaves exactly **two Excel files** in the working directory —
`failed_specs_unique_<PID>.xlsx` (the failed specs, classified) and
`all_specs_<PID>.xlsx` (every spec that ran, with its job links). The CSV and
JSON files the scripts produce along the way are intermediates and are removed
in step 7. Let `PID` be the pipeline id.

## Steps

1. **Get the pipeline.** Accept a number or a full pipeline URL from the user.
   Confirm it resolves: `glab api projects/<enc>/pipelines/<id> | ...` (optional
   sanity check of status/ref/sha).

2. **Generate the CSVs.**
   ```bash
   python3 "$SKILL_DIR/scripts/pipeline_failed_specs.py" <pipeline> \
     [-p <group/project>]
   ```
   Fetches every Cypress job attempt (jobs retry once, so max 2 attempts) —
   including jobs that run in downstream child pipelines, such as the offline
   suite (`cypress-offline-node-N` bridges → child pipelines whose
   `cypress-offline-child` job holds the specs, including the child pipelines
   of bridges that were re-run, so a failure fixed by a manual re-run still
   shows as passed-on-retry) — parses each trace's
   `[SPEC START]`/`[SPEC END]` markers for per-spec pass/fail, and writes three
   files (override with `-o`/`-u`):
   - `all_specs_$PID.csv` — every spec that ran (passed or failed), with
     `first_job_url, second_job_url` (its 1st/2nd attempt, chronological).
   - `failed_specs_unique_$PID.csv` — the failed specs, fixed column order:
     `Failed spec, Passed on retry, New failure, bug_likelihood_(AI), Note,
     Locally reproducible, failure_cause, first_cypress_url, second_cypress_url,
     first_job_url, second_job_url`. Job/cypress columns are the spec's 1st/2nd
     **attempt** (all runs, not only failures — a spec can pass attempt 1 and
     fail attempt 2). The classification columns start blank/`N/A`.
   - `spec_runs_$PID.json` — sidecar with per-attempt status; export uses it to
     colour cells (red = the spec failed in that attempt).

   Specs marked `Passed on retry: yes (...)` are FLAKY. Specs marked
   `Note: JOB CRASHED` started (per a `[SPEC START]` marker) but the job never
   logged a matching `[SPEC END]` — it likely crashed/timed out/OOM'd mid-spec,
   so pass/fail is unknown; call these out separately.

   Specs marked `Note: NO SPECS RAN: <reason>` belong to a **failed Cypress job
   that never started a single spec** (e.g. `Cypress verification timed out.`,
   binary/install failure), so the trace has no `[SPEC START]` markers. For
   offline child jobs the planned specs are recovered by evaluating
   `sortOfflineSpecs(node, SPEC_GROUP)` from
   `paratoo-webapp/scripts/cypress-parallel-offline.js` at the pipeline's
   commit (needs `node` on PATH), and each gets a row. If that can't be
   resolved, the job itself gets a row keyed `<job name> node-N (no specs
   ran)`. The script prints a `ran NO specs` summary — always report these:
   the spec didn't fail, it never got to run, so its coverage is missing.

3. **Flag newly-introduced failures (optional, only if a previous run exists).**
   Ask the script for the most recent prior unique CSV in the working folder:
   ```bash
   python3 "$SKILL_DIR/scripts/compare_new_failures.py" \
     --current "failed_specs_unique_$PID.csv" --detect-only
   ```
   - If it prints a path, that's the last run's unique CSV (by creation time).
     Use **AskUserQuestion** to ask the user whether to compare this pipeline
     against that file. Only ask when a path is printed — first-time runners
     (no prior file) skip this entirely and every `New failure` stays `N/A`.
   - If the user says yes, populate the `New failure` column deterministically:
     ```bash
     python3 "$SKILL_DIR/scripts/compare_new_failures.py" \
       --current "failed_specs_unique_$PID.csv" --previous "<detected-file>"
     ```
     `New failure` becomes `yes` for specs that failed this run but not in the
     previous one, `no` for specs failing in both. The comparison needs only
     the `Failed spec` column in each CSV, so it survives column changes.
   - If the user declines (or no prior file), leave the column as `N/A`.

   Newly-introduced failures (`yes`) are the highest-signal specs — surface
   them prominently in the final summary.

4. **Extract root failures** (for classification):
   ```bash
   python3 "$SKILL_DIR/scripts/extract_failures.py" <pipeline> \
     [-p <group/project>]
   ```
   Defaults to writing `failures_raw_$PID.json` (override with `-o`). For each
   spec still failing in the latest attempt it records the strongest bug
   signal across all failed attempts (so a flaky early glitch can't mask a real
   bug an earlier attempt exposed); flaky (passed-on-retry) specs are also
   captured, from their first failed attempt. Per spec it records the
   first failing test, its error line, the spec's repo path (`spec_path`),
   the stack frames pointing into repo test code (`first_error_frames`, e.g.
   `test/cypress/support/commands.js:1382` — custom commands hold most
   asserted behavior; `first_error_spec_line` is set when a frame hits the
   spec itself), an `error_kind` tag (`value-mismatch` / `app-error` =
   real-bug signal; `element-timeout` = glitch-eligible; `job-aborted` = the
   job died before the spec started, see step 5), and all distinct
   error signatures. The JSON header also carries the pipeline's commit `sha`
   — the exact code the pipeline ran. It prints a "BUG-SIGNAL specs" list;
   none of those may end up labelled a Cypress glitch.

5. **Classify each unique spec — grounded in THAT spec's own captured error.**
   Read `failures_raw_$PID.json` and apply `reference/failure_taxonomy.md`.

   **Hard grounding rules (these prevent the failure mode of masking real bugs):**
   - Classify each spec **only** from its own record (`first_error`,
     `signatures`, `error_kind`, `first_error_frames`). **Never** import an
     error string, symptom, or cause from another spec, from the taxonomy's
     example phrases, or from memory. If your `failure_cause` names something
     (a selector, a button label, an overlay) that does not appear in this
     spec's `first_error`/`signatures`, it is wrong — delete it.
   - **`error_kind` gates the glitch bucket.** Only `error_kind: element-timeout`
     is eligible to be called a Cypress glitch / LOW. If `error_kind` is
     **`value-mismatch` or `app-error`, it is a real-bug signal, NOT a glitch**
     — the app produced wrong data/output. Classify it MEDIUM or HIGH and read
     the assertion; do not reach for a UI-glitch label no matter what the
     surrounding UI did. (`extract_failures.py` prints these as "BUG-SIGNAL
     specs" — none of them may be labelled a glitch.)

   Then, per spec:

   a. Read the actual error type. A deterministic assertion — `to deeply
      equal`, `to equal`, count mismatch, exact text, `expected X to be Y` —
      means the app produced the wrong value/shape: a bug (MEDIUM/HIGH), never
      a glitch. Only an *element-find / interaction timeout* (`never found`,
      `hidden from view`, dropdown races) may be a glitch — and only if the
      captured error genuinely is that.
   b. Understand what the test asserts before labelling — **read the test code**
      (see "Reading the code at the pipeline's commit"): open the files in
      `first_error_frames` (and `spec_path` at `first_error_spec_line`), and
      follow any custom command into `test/cypress/support/`. State the test's
      *intent* and the concrete discrepancy in the `failure_cause`, e.g.
      `app data mismatch: fauna_plot_not_walked present in actual but absent
      in expected (plot layout edit not persisting field)` — quote the real
      diff, never a generic phrase.
   c. For deterministic behavior mismatches, check whether the app changed
      intentionally (`git log -S "<asserted text>"` on app src at the
      checkout): a deliberately removed/replaced behavior is
      `stale test (app behavior changed: <feature>)`.
   d. Distinguish a **root cause** from a **cascade**, mark
      `Passed on retry: yes` specs as `flaky (passed on retry)`, keep
      pre-existing/unrelated failures out of the headline cluster, and hedge
      honestly (`(likely ...)`).
   e. Assign `bug_likelihood` per the rubric in the taxonomy: HIGH = real app
      bug (deterministic value/text/data mismatches, app error states,
      corroborated across specs); MEDIUM = deterministic but ambiguous (count
      mismatches, stale tests, missing UI states, anything you couldn't fully
      pin); LOW = **only** genuine `element-timeout` Cypress-glitch families,
      cascades of one, ordering deps, test bugs, or flaky-passed-on-retry.

   **`job-aborted` specs** (`Note: NO SPECS RAN`) have no test result at all.
   Classify them as CI/infra, e.g. `not run: job aborted before first spec
   (Cypress verification timed out) — CI infra, no test result`, with
   `bug_likelihood` LOW. Group every spec sharing one abort reason into one
   cluster (it's one infra incident, not N failures), and in the summary say
   how many specs lost coverage.

   **Self-check before writing the mapping:** for every spec, confirm the
   `failure_cause` is a paraphrase of that spec's own `first_error`/signatures,
   and that no `value-mismatch`/`app-error` spec was labelled LOW/glitch. If a
   cause mentions a UI glitch but `error_kind` is `value-mismatch`, that is the
   exact bug-masking mistake this step exists to catch — reclassify it.

   Write decisions to `mapping_$PID.json` as
   `{ "<spec>.cy.js": {"failure_cause": "<cause>", "bug_likelihood": "LOW|MEDIUM|HIGH"} }`
   (a plain string value is still accepted and leaves the likelihood blank).

   > Note: steps 6 and 7 **deterministically enforce** the bug-signal rule as a
   > backstop (auto-discovering `failures_raw_$PID.json`): any `value-mismatch`
   > /`app-error` spec you leave at LOW is raised to MEDIUM, and a glitch-style
   > cause on such a spec is replaced with its real captured error. Aim to get
   > it right here anyway — but the deliverable can't ship a masked bug even if
   > you miss one.

6. **Annotate the CSV.**
   ```bash
   python3 "$SKILL_DIR/scripts/annotate_failure_cause.py" \
     --mapping "mapping_$PID.json" --csv "failed_specs_unique_$PID.csv"
   ```
   Adds/refreshes both `failure_cause` and `bug_likelihood_(AI)` columns, then
   applies the bug-signal enforcement (prints any `⚠ Enforced ...` corrections).
   Re-run after edits; any spec missing from the mapping shows as
   `UNCLASSIFIED`, so resolve those before finishing.

7. **Export the Excel deliverables and clean up intermediates.** Convert both
   CSVs to formatted workbooks (deleting the source CSVs as they go), then
   remove the JSON working files (including the sidecar) — so the run leaves
   only the two `.xlsx`.
   ```bash
   python3 "$SKILL_DIR/scripts/export_xlsx.py" --csv "failed_specs_unique_$PID.csv" --remove-source
   python3 "$SKILL_DIR/scripts/export_xlsx.py" --csv "all_specs_$PID.csv" --remove-source
   rm -f "failures_raw_$PID.json" "mapping_$PID.json" "spec_runs_$PID.json"
   ```
   The exporter (dependency-free — pure stdlib, runs on any OS) sorts rows
   alphabetically by spec and renders each job/cypress link as its **job
   number** (clickable). Cell colours:
   - a job or cypress cell is **red** if the spec FAILED in that attempt;
     the failure-cause (bug-signal) job cell is **orange + bold** instead;
   - `Note: JOB CRASHED` / `Note: NO SPECS RAN: ...` cells are red (and so is
     a `NOT_RUN` attempt's job cell); `bug_likelihood_(AI): HIGH` and
     `New failure: yes` cells are red; a `Passed on retry: yes` row is green;
   - in `all_specs`, each job cell shows `job# (job name)` and is **green if
     the spec passed** / red if it failed — so you can find which job a passed
     spec ran in.

   After this step the working directory holds exactly
   `failed_specs_unique_$PID.xlsx` (the primary deliverable) and
   `all_specs_$PID.xlsx`. The next run's step-3 comparison reads the unique
   `.xlsx` as the previous run.

8. **Summarize.** Give the user a breakdown grouped by `failure_cause` with
   counts and the spec lists, and call out: **newly-introduced failures
   (`New failure: yes`) and HIGH bug-likelihood specs first** (these are the
   ones worth re-running locally to catch real bugs — and a HIGH that is also
   a new failure is the top priority), then the dominant *actionable* cluster,
   what's a cascade of it, and what's pre-existing/unrelated. Note
   classification confidence where you hedged.

## Reading the code at the pipeline's commit

The project is open source on GitLab, so the exact code a pipeline ran is
always inspectable. `failures_raw_$PID.json` carries the pipeline `sha`.

- **Local checkout** (preferred; ask the user if one exists, e.g.
  `~/projects/paratoo-fdcp*`): read files at the pipeline's commit with
  `git show <sha>:paratoo-webapp/<spec_path>`, search app code with
  `git grep <pattern> <sha> -- paratoo-webapp/src`, and check behavior-change
  history with `git log --oneline -S "<asserted text>" -- paratoo-webapp/src`.
- **No checkout**: fetch any file at the pipeline's commit via the API:
  ```bash
  glab api "projects/<enc>/repository/files/$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "paratoo-webapp/<spec_path>")/raw?ref=<sha>"
  ```
  and search with `glab api "projects/<enc>/search?scope=blobs&search=<term>&ref=<sha>"`.

Custom Cypress commands live under `paratoo-webapp/test/cypress/support/` —
most spec assertions delegate to them, so the real asserted behavior is
usually there (e.g. `selectProtocol` with `willRejectEntering: true` asserts
`cy.testRoute('projects')`, i.e. "the app should NOT have navigated").

9. **Offer a ticket.** Identify the most actionable cluster (usually the largest
   group of related *root* failures). Use AskUserQuestion to ask whether to open
   a GitLab issue for it. If yes, draft from `reference/ticket_template.md`
   (concrete specs + the exact error signature + first_failed_job_url repro
   links + a suggested fix + a local verify command) and create it:
   ```bash
   glab issue create --title "<title>" --description "$(cat issue.md)" \
     --assignee <user> --label bug [-R <group/project>]
   ```
   Ask who to assign (default: the requesting user). Confirm before creating —
   opening an issue is outward-facing.

## Output recap

A completed run leaves exactly **two files** in the working directory (both
suffixed with the pipeline id so runs for different pipelines coexist):

- **`failed_specs_unique_$PID.xlsx`** — the primary deliverable: failed specs,
  sorted by spec (specs show `<spec> (priority)` or `<spec> (setup)` when they
  ran in the priority or setup stage, followed by the parallel shard bracket
  `[run 3/8]` / `[priority 2/6]` / `[setup]`; offline-suite specs show
  `<spec> (offline)[node-N]` for their child-pipeline node). Columns:
  `Failed spec, Passed on retry, New failure, bug_likelihood_(AI), Note,
  Locally reproducible, failure_cause, first_cypress_url, second_cypress_url,
  first_job_url, second_job_url`.
  - `New failure` is `yes`/`no` vs the previous run or `N/A` if not compared;
    `bug_likelihood_(AI)` is HIGH (likely real app bug, re-run locally first) /
    MEDIUM / LOW (likely Cypress glitch). `Locally reproducible` is an empty
    column for the user to fill in.
  - `first/second_job_url` are the spec's 1st/2nd **attempts** (chronological,
    all runs). A cell is **red** if the spec failed in that attempt; the
    failure-cause (bug-signal) job is **orange + bold** instead. `first/second_cypress_url`
    are the matching Cypress Cloud runs, coloured the same way. Link text is
    the job number.
  - `Note: JOB CRASHED` (red) = a `[SPEC START]` with no matching `[SPEC END]`.
  - `Note: NO SPECS RAN: <reason>` (red) = the job failed before its first
    spec started; the spec has no result (see step 2).
  - For **flaky (passed-on-retry)** specs, `failure_cause` is enriched with the
    first-failure error (`flaky (passed on retry) — first failure: <error>`).
    Flaky specs stay LOW even if that first failure was a value/data mismatch.
- **`all_specs_$PID.xlsx`** — every spec that ran (passed and failed), sorted,
  with `first_job_url, second_job_url` showing `job# (job name)`, green if the
  spec passed in that attempt / red if it failed. Lets you find which job a
  passed spec ran in.

Intermediates (`all_specs_$PID.csv`, `failed_specs_unique_$PID.csv`,
`failures_raw_$PID.json`, `mapping_$PID.json`, `spec_runs_$PID.json`) are
produced during the run and removed in step 7.

- Optional: a GitLab issue.

## Notes

- Every job whose name contains `cypress` is parsed for specs (cypress-run,
  cypress-priority, cypress-setup, cypress-smoke-test, and the offline suite's
  `cypress-offline-child` in child pipelines). A *successful* cypress job
  with no `[SPEC START]` markers contributes nothing; a *failed* one is
  reported as `NO SPECS RAN` (step 2), never dropped. Child (triggered)
  pipelines are followed via the parent's bridges, so offline specs are
  included; non-cypress job failures (commitlint, sonarcloud, setup) appear in the per-job sheet
  (`failed_specs_$PID.xlsx`) with an empty spec — mention them but they don't
  get a `failure_cause`.
- Specs with `Note: Unable to find outputs` had a `[SPEC START]` in that job's
  trace but no matching `[SPEC END]` — outcome unknown (crash/timeout/OOM),
  not a confirmed failure.
- The taxonomy in `reference/failure_taxonomy.md` is editable: teams should add
  their own recurring signatures over time.
