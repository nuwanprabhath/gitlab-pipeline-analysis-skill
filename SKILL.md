---
name: gitlab-pipeline-analysis
description: >-
  Triage failed Cypress specs in a GitLab CI pipeline. Given a pipeline number or
  URL, generate failed_specs_<pipeline>.csv and failed_specs_unique_<pipeline>.csv
  (the latter with failure_cause and bug_likelihood_(AI) columns that separate
  real app bugs from Cypress glitches), group the failures by root cause, and
  offer to open a GitLab issue for the dominant cluster. Use when asked to
  investigate, analyze, triage, or summarize CI / pipeline test failures, or to
  classify why specs failed.
version: 1.2.0
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
`python3 "$SKILL_DIR/scripts/<name>.py" ...`. Write CSVs/JSON to the user's
current working directory (or a path they specify), not into the skill repo.

All output filenames below are suffixed with the pipeline id by default
(`_<pipeline>`) so analyzing several pipelines in the same folder never
overwrites a previous run. Let `PID` be that pipeline id.

## Steps

1. **Get the pipeline.** Accept a number or a full pipeline URL from the user.
   Confirm it resolves: `glab api projects/<enc>/pipelines/<id> | ...` (optional
   sanity check of status/ref/sha).

2. **Generate the CSVs.**
   ```bash
   python3 "$SKILL_DIR/scripts/pipeline_failed_specs.py" <pipeline> \
     [-p <group/project>]
   ```
   Defaults to writing `failed_specs_$PID.csv` and
   `failed_specs_unique_$PID.csv` (override with `-o`/`-u` if needed).
   `failed_specs_$PID.csv` has one row per failed spec per job/retry;
   `failed_specs_unique_$PID.csv` is deduped (`Failed spec, Passed on retry,
   first_failed_job_url, Note`). Specs marked `Passed on retry: yes (...)` are
   FLAKY, not hard failures. Specs marked `Note: Unable to find outputs`
   started (per a `[SPEC START]` marker) but the job never logged a matching
   `[SPEC END]` — it likely crashed, timed out, or was OOM-killed mid-spec, so
   pass/fail is unknown; call these out separately rather than folding them
   into the failure-cause breakdown.

3. **Extract root failures** (for classification):
   ```bash
   python3 "$SKILL_DIR/scripts/extract_failures.py" <pipeline> \
     [-p <group/project>]
   ```
   Defaults to writing `failures_raw_$PID.json` (override with `-o`). This
   uses the LATEST attempt of each cypress job and records, per spec, the
   first failing test, its error line, the spec's repo path (`spec_path`),
   the stack frames pointing into repo test code (`first_error_frames`, e.g.
   `test/cypress/support/commands.js:1382` — custom commands hold most
   asserted behavior; `first_error_spec_line` is set when a frame hits the
   spec itself), and all distinct error signatures. The JSON header also
   carries the pipeline's commit `sha` — the exact code the pipeline ran.

4. **Classify each unique spec — from test intent, not just the raw error.**
   Read `failures_raw_$PID.json` and apply `reference/failure_taxonomy.md`.
   The raw error alone is misleading: `expected false to be true` can be a
   route assertion, a store check, or anything — you MUST know what the test
   was asserting before labelling it. For each failed spec:

   a. Match `first_error` against the taxonomy's known Cypress-glitch families
      (dropdown races, covered-by-popup clicks, listbox never opening). Those
      are LOW and need no code dive.
   b. For everything else — especially bare assertion errors, exact-text or
      data mismatches, and route/behavior assertions — **read the test code**
      (see "Reading the code at the pipeline's commit" below): open the
      files in `first_error_frames` (and `spec_path` at
      `first_error_spec_line` when set), work out what the test is supposed
      to verify, and follow any custom command it calls into
      `test/cypress/support/`. State the test's *intent* in the
      `failure_cause`, e.g. `app allowed editing collection with differing
      plot context (guard not enforced or intentionally removed)` — never
      just `expected false to be true`.
   c. For deterministic behavior mismatches, check whether the app changed
      intentionally (`git log -S "<asserted text>"` on app src at the
      checkout): if the asserted behavior was deliberately removed/replaced,
      the verdict is `stale test (app behavior changed: <feature>)`.
   d. Distinguish a **root cause** from a **cascade**, mark
      `Passed on retry: yes` specs as `flaky (passed on retry)`, keep
      pre-existing/unrelated failures out of the headline cluster, and hedge
      honestly (`(likely ...)`).
   e. Assign `bug_likelihood` per the rubric in the taxonomy: HIGH = evidence
      of a real app bug (deterministic value/text/data mismatches, app error
      states, corroborated across specs); MEDIUM = deterministic but
      ambiguous (count mismatches, stale tests, missing UI states); LOW =
      known Cypress-glitch families, cascades, ordering, test bugs, flaky.

   Write decisions to `mapping_$PID.json` as
   `{ "<spec>.cy.js": {"failure_cause": "<cause>", "bug_likelihood": "LOW|MEDIUM|HIGH"} }`
   (a plain string value is still accepted and leaves the likelihood blank).

5. **Annotate the CSV.**
   ```bash
   python3 "$SKILL_DIR/scripts/annotate_failure_cause.py" \
     --mapping "mapping_$PID.json" --csv "failed_specs_unique_$PID.csv"
   ```
   Adds/refreshes both `failure_cause` and `bug_likelihood_(AI)` columns.
   Re-run after edits; any spec missing from the mapping shows as
   `UNCLASSIFIED`, so resolve those before finishing.

6. **Summarize.** Give the user a breakdown grouped by `failure_cause` with
   counts and the spec lists, and call out: the **HIGH bug-likelihood specs
   first** (these are the ones worth re-running locally to catch real bugs),
   then the dominant *actionable* cluster, what's a cascade of it, and what's
   pre-existing/unrelated. Note classification confidence where you hedged.

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

7. **Offer a ticket.** Identify the most actionable cluster (usually the largest
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

- `failed_specs_$PID.csv` — per-job/retry rows.
- `failed_specs_unique_$PID.csv` — deduped, fixed column order:
  `Failed spec, Passed on retry, bug_likelihood_(AI), Note, failure_cause,
  first_failed_job_url`. `bug_likelihood_(AI)` and `failure_cause` are empty
  until step 5 annotates them (HIGH = likely real app bug, re-run locally
  first; LOW = likely Cypress glitch/cascade/flake).
- `failures_raw_$PID.json`, `mapping_$PID.json` — intermediate working files
  (safe to delete). All filenames are suffixed with the pipeline id so
  re-running for a different pipeline in the same folder doesn't clobber a
  prior analysis.
- Optional: a GitLab issue.

## Notes

- Only cypress-run / cypress-priority jobs are parsed for specs; non-cypress job
  failures (commitlint, sonarcloud, setup) appear in `failed_specs_$PID.csv`
  with an empty spec — mention them but they don't get a `failure_cause`.
- Specs with `Note: Unable to find outputs` had a `[SPEC START]` in that job's
  trace but no matching `[SPEC END]` — outcome unknown (crash/timeout/OOM),
  not a confirmed failure.
- The taxonomy in `reference/failure_taxonomy.md` is editable: teams should add
  their own recurring signatures over time.
