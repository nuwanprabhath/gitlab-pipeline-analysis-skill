---
name: gitlab-pipeline-analysis
description: >-
  Triage failed Cypress specs in a GitLab CI pipeline. Given a pipeline number or
  URL, generate failed_specs_<pipeline>.csv and failed_specs_unique_<pipeline>.csv
  (the latter with a failure_cause column), group the failures by root cause, and
  offer to open a GitLab issue for the dominant cluster. Use when asked to
  investigate, analyze, triage, or summarize CI / pipeline test failures, or to
  classify why specs failed.
version: 1.1.1
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
   first failing test and its error line plus all distinct error signatures.

4. **Classify each unique spec.** Read `failures_raw_$PID.json` and apply
   `reference/failure_taxonomy.md`. For each spec in
   `failed_specs_unique_$PID.csv` decide a concise `failure_cause` label. Key
   judgement calls:
   - Distinguish a **root cause** from a **cascade** (e.g. a dropdown that never
     resolved makes later "element never found" / stepper assertions fail —
     those are cascades, label them as such).
   - Mark `Passed on retry: yes` specs as `flaky (passed on retry)`.
   - Separate **pre-existing / unrelated** failures (publish-upload flakes,
     ordering dependencies, auth, test bugs) from the cluster under
     investigation, so they don't inflate the headline cause.
   - Hedge honestly: use `(likely ...)` / `(likely upstream cascade)` when the
     captured error doesn't definitively pin the root.
   Write your decisions to `mapping_$PID.json` as `{ "<spec>.cy.js": "<cause>" }`.

5. **Annotate the CSV.**
   ```bash
   python3 "$SKILL_DIR/scripts/annotate_failure_cause.py" \
     --mapping "mapping_$PID.json" --csv "failed_specs_unique_$PID.csv"
   ```
   Re-run after edits; any spec missing from the mapping shows as
   `UNCLASSIFIED`, so resolve those before finishing.

6. **Summarize.** Give the user a breakdown grouped by `failure_cause` with
   counts and the spec lists, and call out: the dominant *actionable* cluster,
   what's a cascade of it, and what's pre-existing/unrelated. Note classification
   confidence where you hedged.

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
- `failed_specs_unique_$PID.csv` — deduped, now with `failure_cause`.
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
