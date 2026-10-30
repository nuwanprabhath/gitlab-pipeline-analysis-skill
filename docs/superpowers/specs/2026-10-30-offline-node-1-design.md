# Design: report offline node 1 (`offline-floristics-*`) failures

Date: 2026-10-30. Tracks ternandsparrow/paratoo-fdcp#3096 (upstream half).
Committed on the feature branch at Luke's request; whether it ships in the PR is his call.

## Intent

paratoo-fdcp !1486 split offline node 1 into a chain of seven child-pipeline
jobs: `offline-floristics-setup`, five `offline-floristics-<consumer>` jobs,
and `offline-floristics-sync`. The skill keeps a child job only if its name
contains `cypress`, so none of node 1's jobs are analysed. A node-1 failure
is missing from the report. Both nightlies (`dev/1.0.12`, `dev/1.0.13`) now
run node 1.

The fix goes to Nuwan's repo as a GitHub PR he reviews. Luke then ports the
equivalent fix to the JS copy in paratoo-fdcp (`helper-scripts/ci/pipeline-analysis/`);
that port is out of scope here.

## Current state (verified 2026-10-30 at `f34d20a`)

1. **Child-job filter.** `is_cypress_job(name)` (`scripts/pipeline_failed_specs.py:192`)
   is `"cypress" in name.lower()`. It filters child-pipeline jobs in two places:
   - `pipeline_failed_specs.py` `gather_cypress_jobs` (`:300`)
   - `extract_failures.py` (`:195`, inside the function that builds `by_name`)

   Running `pipeline_failed_specs.py 2966178222` finds one job
   (`cypress-lint-gate`). The failed `offline-floristics-cover-fire` job
   (17305941476, child pipeline 2966178303) is absent.

2. **Aborted-job spec recovery is broken against the real shard script.**
   When a failed offline job ran no spec, `planned_offline_specs`
   (`pipeline_failed_specs.py:142`) fetches
   `paratoo-webapp/scripts/cypress-parallel-offline.js` at the pipeline SHA and
   runs `_NODE_EVAL` (`:121`), which calls `m.sortOfflineSpecs(node, group)`.
   The real script (both `dev/1.0.12` and `dev/1.0.13`, and before !1486):
   - exports only `{ NODE_1_SPEC_GROUPS, TEST_FOLDER }`, not `sortOfflineSpecs`;
   - reads `CI_NODE_INDEX` and `SPEC_GROUP` from `process.env` at module load,
     and `sortOfflineSpecs()` takes no arguments;
   - only requires `minimist` under `require.main === module`, so `require()`
     from a temp dir works.

   Result: `TypeError: m.sortOfflineSpecs is not a function`, empty plan,
   fallback to one job-level `NO SPECS RAN` row. The unit tests pass because
   `SHARD_SCRIPT` in `tests/test_pipeline_failed_specs.py:276` is a fake with the
   assumed signature. `extract_failures.py:304-306` uses the same helper.

3. **Spec group of a node-1 job.** `spec_group_from_job_name` (`:108`) reads only
   a `parallel: matrix` suffix (`cypress-offline-child-3: [drones]`). Node 1's
   group is a job variable `SPEC_GROUP`, equal to the job name minus
   `offline-floristics-` (`setup`, `recruitment`, `survivorship-online`,
   `survivorship-offline`, `condition-basal`, `cover-fire`, `sync`). The jobs
   API does not expose job variables, so the name is the only source.

## Goals

- A failed node-1 job's spec failures appear in `all_specs`,
  `failed_specs_unique`, the sidecar, and `extract_failures.py` output, labelled
  `(offline)[node-1]` like other offline specs.
- A failed node-1 job that ran no spec is reported as `NO SPECS RAN` against its
  own group's spec only (e.g. `cover-fire` → `offline-floristics-consumers-cover-fire.cy.js`),
  not all seven.
- Aborted-job recovery works for every offline node against the real script
  shape (nodes 2/4/5: whole node; node 3: its matrix group).

## Non-goals

- The JS port in paratoo-fdcp (Luke does it after this merges).
- Changing `is_cypress_job` for parent-pipeline jobs.
- Changes to `cypress-parallel-offline.js` in paratoo-fdcp. The skill must work
  against the script as it is on `dev/1.0.12` and `dev/1.0.13`.

## Anti-goals

- Pulling `deploy-offline-stack` / `teardown-offline-stack` into the report.
  A failed teardown is not a spec failure.
- Breaking the recovery for any script version that *does* export
  `sortOfflineSpecs(nodeIndex, specGroup)` (the test fake's shape).

## Decisions (Claude's calls, stated to Luke)

1. **Child-job filter matches the stage too.** A child-pipeline job is kept if
   its name *or* its stage contains `cypress`. Node 1's jobs are in stage
   `test-cypress-offline`; deploy/teardown are in `deploy-offline-stack` /
   `teardown-offline-stack`, so they stay out. This survives future job renames
   inside that stage. Parent-pipeline filtering is unchanged. Put the rule in
   one helper in `pipeline_failed_specs.py` (e.g. `is_cypress_child_job(job)`)
   and use it from both scripts. Skipped jobs (e.g. `offline-floristics-sync`
   when a consumer fails) already contribute nothing: no trace, not `failed`.
2. **Spec group from the node-1 name.** `spec_group_from_job_name` returns the
   matrix suffix if present, else the part after a leading
   `offline-floristics-`, else `""`.
3. **Evaluate the shard script the way CI runs it.** Run the fetched script with
   `CI_NODE_INDEX=<node>` and `SPEC_GROUP=<group>` in the subprocess
   environment. Append a line to the fetched source that exports
   `sortOfflineSpecs` if the function exists. Call it with `(node, group)` as
   well, so a script with the argument signature still works. Everything else
   (cache, timeout, error handling, `./` stripping, never raising) is unchanged.
4. **No whole-node guess for an ungrouped chain job.** An empty group makes the
   real script return the whole node (all seven node-1 specs). Use that plan
   only when the group is non-empty or the job is the one-job-per-node
   `cypress-offline-child` (nodes 2/4/5, and node 1 in pipelines before !1486).
   Any other ungrouped offline child job (e.g. a renamed node-1 job the stage
   filter kept) falls back to the job-level `NO SPECS RAN` row. Apply the rule
   in one place shared by both scripts.
5. **Tests.** Add a fixture shaped like the real script, copied from
   `cypress-parallel-offline.js` on `dev/1.0.13` (env-driven, no export,
   `require.main` guard, node-1 group table), and keep the existing fake as the
   argument-signature case. Cover: node-1 group recovery, unknown group (script
   throws → empty plan, no crash), child filter by stage, deploy/teardown
   excluded, the group parser for node-1 names. Extend the two integration test
   files with a node-1 child pipeline (one failed consumer with spec markers,
   one aborted consumer, skipped sync), and a test for the ungrouped-job guard.
6. **Release notes.** Bump `SKILL.md` `version` to `2.3.1` and add a
   `## [2.3.1]` `### Fixed` entry to `CHANGELOG.md` in the existing style
   (concrete pipeline/job IDs). Update the `SKILL.md` text that says only
   `cypress-offline-child` holds offline specs (§step 1 around line 62 and
   §Notes around line 340).

## Verification

- `python3 -m unittest discover -s tests -v` passes.
- `pipeline_failed_specs.py 2966178222` lists the cover-fire spec failure under
  node 1 (job 17305941476), and `extract_failures.py` on the same pipeline
  includes that job.
- Recovery checked against the real `cypress-parallel-offline.js` from
  `dev/1.0.12` and `dev/1.0.13` (scratch run already confirmed: `cover-fire` →
  1 spec, `setup` → 1 spec, empty group → 7 specs). No real pipeline with an
  aborted node-1 job is known, so this is covered by tests plus the scratch run.

## Delivery

- Branch in this repo from `master`. Commits as Luke Derby
  `<luke.derby@adelaide.edu.au>`; GitHub auth via `gh` account `LukeDerby99`
  (never `Duko99`).
- `LukeDerby99` has read-only access to `nuwanprabhath/gitlab-pipeline-analysis-skill`,
  so push the branch to a `LukeDerby99` fork. Push and report back; the PR is
  opened only on Luke's go-ahead.

## Left to the builder

Helper names, test layout, exact CHANGELOG wording, and whether
`extract_failures.py` imports the new helper or `pipeline_failed_specs` as `pfs`
(it already imports both ways).
