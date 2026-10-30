# Offline node 1 jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report failures from offline node 1's `offline-floristics-*` child jobs, and make aborted offline jobs resolve to their own planned specs against the real shard script.

**Architecture:** Three shared helpers in `scripts/pipeline_failed_specs.py` (child-job filter, spec-group parser, per-job plan) are used by both `pipeline_failed_specs.py` and `extract_failures.py`. `planned_offline_specs` runs the fetched shard script with `CI_NODE_INDEX`/`SPEC_GROUP` in the environment and an appended export, so it works with the real env-driven script and the old argument-driven shape.

**Tech Stack:** Python 3 stdlib, `unittest`, `node` (subprocess), `glab`.

**Spec:** `docs/superpowers/specs/2026-10-30-offline-node-1-design.md`

## Global Constraints

- Branch `fix/offline-node-1-jobs` (exists, design commit `68514c6`). Commits as `Luke Derby <luke.derby@adelaide.edu.au>`, ending with the Co-Authored-By / Claude-Session trailers from the session.
- GitHub: `gh` account `LukeDerby99` only, never `Duko99`. Push to a `LukeDerby99` fork; do **not** open the PR — push and report back, ready for Luke to open it.
- Parent-pipeline filtering (`is_cypress_job(name)`) is unchanged.
- `deploy-offline-stack` / `teardown-offline-stack` must never enter the report.
- `planned_offline_specs` never raises; on any failure it returns `[]`.
- Test runner: `python3 -m unittest discover -s tests -v` (stdlib, no deps). Baseline: 219 tests, OK.
- Code comments: one line, the non-obvious why; no ticket numbers.
- Version `2.3.1`; CHANGELOG in the existing Keep-a-Changelog style.

## Review Focus

1. A node-1 job renamed outside `offline-floristics-*` but still in stage `test-cypress-offline` → kept by the filter, but no 7-spec guess; one job-level `NO SPECS RAN` row. (Task 2 test `test_ungrouped_chain_job_is_not_guessed`.)
2. A pre-!1486 pipeline whose node-1 job is `cypress-offline-child` → whole-node plan still used. (Task 2 test `test_plain_offline_child_uses_whole_node`.)
3. A shard script at an old SHA that exports `sortOfflineSpecs(nodeIndex, specGroup)` → still resolves. (Existing `PlannedOfflineSpecsTests` fake, kept unchanged in Task 3.)
4. Unknown group (script throws `Unknown SPEC_GROUP`) → `[]`, stderr note, no crash. (Task 3 test `test_real_script_unknown_group_degrades_to_empty`.)
5. Skipped `offline-floristics-sync` after a consumer fails → contributes nothing. (Task 1 integration test asserts no row for `offline-floristics-sync.cy.js`.)

---

### Task 1: Child-job filter matches the stage

**Files:**
- Modify: `scripts/pipeline_failed_specs.py:192-205` (add helper after `is_cypress_job`), `:300` (use it), docstrings of `fetch_bridges`/`gather_cypress_jobs` that say only `cypress-offline-child` holds specs
- Modify: `scripts/extract_failures.py:52` (import), `:195` (use it), docstring at `:169-175`
- Test: `tests/test_pipeline_failed_specs.py`, `tests/test_pipeline_failed_specs_integration.py`, `tests/test_extract_failures_integration.py`

**Interfaces:**
- Produces: `is_cypress_child_job(job: dict) -> bool` in `pipeline_failed_specs.py` — `is_cypress_job(job["name"]) or is_cypress_job(job.get("stage", ""))`.

- [ ] **Step 1: Write the failing unit tests** in a new `IsCypressChildJobTests` class in `tests/test_pipeline_failed_specs.py`:

```python
def test_node_1_chain_jobs_kept_by_stage(self):
    for name in ("offline-floristics-setup", "offline-floristics-cover-fire", "offline-floristics-sync"):
        self.assertTrue(pfs.is_cypress_child_job({"name": name, "stage": "test-cypress-offline"}), name)

def test_offline_stack_jobs_excluded(self):
    self.assertFalse(pfs.is_cypress_child_job({"name": "deploy-offline-stack", "stage": "deploy-offline-stack"}))
    self.assertFalse(pfs.is_cypress_child_job({"name": "teardown-offline-stack", "stage": "teardown-offline-stack"}))

def test_name_match_still_works_without_stage(self):
    self.assertTrue(pfs.is_cypress_child_job({"name": "cypress-offline-child"}))
```

- [ ] **Step 2: Write the failing integration tests.**
  - `tests/test_pipeline_failed_specs_integration.py`, `GatherCypressJobsTests.test_node_1_chain_jobs_are_collected`: bridge `cypress-offline-node-1` → child 66 with jobs `deploy-offline-stack` (stage `deploy-offline-stack`), `offline-floristics-setup`, `offline-floristics-cover-fire`, `offline-floristics-sync` (stage `test-cypress-offline`), `teardown-offline-stack`. Assert the gathered ids are exactly the three `offline-floristics-*` jobs, each with `_offline_node == "1"`.
  - Same file, new `Node1ChainMainTests` using the `NoSpecsRanTests.run_main` patching pattern: child jobs `offline-floristics-setup` (success, trace passes `offline-floristics-setup.cy.js`), `offline-floristics-cover-fire` (failed, trace has `[SPEC START]`/`[SPEC END] ... ✖ FAILED` for `test/cypress/integration/offline/offline-floristics-consumers-cover-fire.cy.js`), `offline-floristics-sync` (status `skipped`, empty trace). Assert `offline-floristics-consumers-cover-fire.cy.js` is in the unique rows, its sidecar run has `node == "1"`, and no row or sidecar entry exists for `offline-floristics-sync.cy.js`.
  - `tests/test_extract_failures_integration.py`, `CypressJobAttemptsChildPipelineTests.test_keeps_node_1_chain_jobs_by_stage`: same child job list via `fake` glab; assert keys `offline-floristics-cover-fire [node-1]` and `offline-floristics-setup [node-1]` present, no `deploy-offline-stack`/`teardown-offline-stack` key.

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m unittest tests.test_pipeline_failed_specs tests.test_pipeline_failed_specs_integration tests.test_extract_failures_integration -v 2>&1 | tail -20`
Expected: `AttributeError: ... no attribute 'is_cypress_child_job'` and assertion failures on the missing node-1 jobs.

- [ ] **Step 4: Implement `is_cypress_child_job`** and replace the child-job `is_cypress_job(j["name"])` calls at `pipeline_failed_specs.py:300` and `extract_failures.py:195` with it. Update the docstrings that say child specs live only in `cypress-offline-child`.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`, test count above 219.

- [ ] **Step 6: Commit** — `fix: analyse offline node 1 chain jobs in child pipelines`

---

### Task 2: Spec group from node-1 names, and no whole-node guess for ungrouped chain jobs

**Files:**
- Modify: `scripts/pipeline_failed_specs.py:105-112` (`spec_group_from_job_name`), add `planned_specs_for_job` after `planned_offline_specs`, call site `:483-488`
- Modify: `scripts/extract_failures.py:303-306` (call site in `aborted_job_records`)
- Test: `tests/test_pipeline_failed_specs.py`, `tests/test_pipeline_failed_specs_integration.py`, `tests/test_extract_failures_integration.py`

**Interfaces:**
- Consumes: `planned_offline_specs(project, sha, node, spec_group) -> list[str]` (existing; Task 3 changes only its internals).
- Produces:
  - `spec_group_from_job_name(name: str) -> str` — matrix suffix, else the text after a leading `offline-floristics-`, else `""`.
  - `planned_specs_for_job(project: str, sha: str, job: dict) -> list[str]` — `[]` if the job has no `_offline_node`; `[]` if the group is `""` and `job["name"] != "cypress-offline-child"`; else `planned_offline_specs(project, sha, job["_offline_node"], group)`. Calls `planned_offline_specs` through the module global so existing `patch.object(pfs, "planned_offline_specs", ...)` tests keep working.

- [ ] **Step 1: Write the failing unit tests.** In `SpecGroupFromJobNameTests`:

```python
def test_node_1_chain_job_name(self):
    self.assertEqual(pfs.spec_group_from_job_name("offline-floristics-cover-fire"), "cover-fire")
    self.assertEqual(pfs.spec_group_from_job_name("offline-floristics-survivorship-online"), "survivorship-online")
    self.assertEqual(pfs.spec_group_from_job_name("offline-floristics-setup"), "setup")
```

New `PlannedSpecsForJobTests` (patch `pfs.planned_offline_specs` with a recorder returning `["x.cy.js"]`):

```python
def test_chain_job_uses_its_group(self):          # name offline-floristics-cover-fire, node "1"
    self.assertEqual(calls, [("1", "cover-fire")])
def test_plain_offline_child_uses_whole_node(self):  # name cypress-offline-child, node "1"
    self.assertEqual(calls, [("1", "")])
def test_ungrouped_chain_job_is_not_guessed(self):   # name offline-floristics, node "1"
    self.assertEqual(result, []); self.assertEqual(calls, [])
def test_parent_job_has_no_plan(self):               # no _offline_node
    self.assertEqual(result, []); self.assertEqual(calls, [])
```

- [ ] **Step 2: Write the failing integration tests.**
  - `tests/test_pipeline_failed_specs_integration.py`: extend the `NoSpecsRanTests` fixtures (`BRIDGES`/child jobs/`planned` map) or add a sibling class with an aborted `offline-floristics-cover-fire` job (failed, no spec markers) in a node-1 child pipeline; planned map `("1", "cover-fire") -> ["test/cypress/integration/offline/offline-floristics-consumers-cover-fire.cy.js"]`. Assert exactly that one spec gets a `NO SPECS RAN` row and no other `offline-floristics-*` spec does.
  - `tests/test_extract_failures_integration.py`, `NoSpecsRanExtractTests`: add an aborted `offline-floristics-cover-fire [node-1]` attempt; assert one `job-aborted` record keyed `offline-floristics-consumers-cover-fire.cy.js`.

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m unittest tests.test_pipeline_failed_specs tests.test_pipeline_failed_specs_integration tests.test_extract_failures_integration 2>&1 | tail -20`
Expected: failures on `"" != "cover-fire"` and missing `planned_specs_for_job`.

- [ ] **Step 4: Implement** `spec_group_from_job_name` change and `planned_specs_for_job`; replace both call sites' inline `if job.get("_offline_node"): planned = pfs.planned_offline_specs(...)` with it. In `pipeline_failed_specs.py` keep the lazy `fetch_pipeline_sha` (fetch only when `job.get("_offline_node")`).

- [ ] **Step 5: Run the full suite** — `python3 -m unittest discover -s tests 2>&1 | tail -3`, expected `OK`.

- [ ] **Step 6: Commit** — `fix: resolve node 1 spec groups from offline-floristics job names`

---

### Task 3: Evaluate the shard script the way CI runs it

**Files:**
- Modify: `scripts/pipeline_failed_specs.py:115-176` (`_NODE_EVAL`, `planned_offline_specs`)
- Create: `tests/shard_script_dev_1_0_13.js` — verbatim copy of `paratoo-webapp/scripts/cypress-parallel-offline.js` from paratoo-fdcp `origin/dev/1.0.13` (last change `ce089e96c2`), with a first-line `//` comment naming that source and commit
- Test: `tests/test_pipeline_failed_specs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: unchanged signature `planned_offline_specs(project, sha, node, spec_group) -> list[str]`.

- [ ] **Step 1: Copy the fixture**

Run: `git -C /home/luke/paratoo-fdcp show origin/dev/1.0.13:paratoo-webapp/scripts/cypress-parallel-offline.js` into `tests/shard_script_dev_1_0_13.js`, then prepend the source comment.

- [ ] **Step 2: Write the failing tests** — new `PlannedOfflineSpecsRealScriptTests` (skip without `node`; same `planned()` helper, `script=` the fixture file's text):

```python
def test_real_script_node_1_group(self):
    self.assertEqual(self.planned("1", "cover-fire"),
                     ["test/cypress/integration/offline/offline-floristics-consumers-cover-fire.cy.js"])
def test_real_script_node_3_group(self):
    self.assertEqual(self.planned("3", "qdaseq0009"),
                     ["test/cypress/integration/offline/offline-acoustic.cy.js",
                      "test/cypress/integration/offline/offline-partial-connection.cy.js",
                      "test/cypress/integration/offline/offline-recruitment-survivorship.cy.js"])
def test_real_script_whole_node(self):
    self.assertEqual(len(self.planned("1", "")), 7)
    self.assertEqual(self.planned("4", ""),
                     ["test/cypress/integration/offline/offline-floristics-and-dependencies-pro.cy.js"])
def test_real_script_unknown_group_degrades_to_empty(self):
    self.assertEqual(self.planned("1", "no-such-group"), [])
```

Keep the existing `PlannedOfflineSpecsTests` (argument-signature fake) unchanged; it must still pass.

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m unittest tests.test_pipeline_failed_specs.PlannedOfflineSpecsRealScriptTests -v`
Expected: the first three FAIL with `[] != [...]` (stderr shows `m.sortOfflineSpecs is not a function`); the unknown-group test passes already.

- [ ] **Step 4: Implement.** Before writing the temp script, append `"\nif (typeof sortOfflineSpecs === 'function') module.exports.sortOfflineSpecs = sortOfflineSpecs\n"` to the fetched source. Run the subprocess with `env={**os.environ, "CI_NODE_INDEX": str(node), "SPEC_GROUP": spec_group}`. Keep `_NODE_EVAL` calling `m.sortOfflineSpecs(Number(argv[2]), argv[3] || '')` (the real script ignores the args). Update the comment above `OFFLINE_SHARD_SCRIPT` in one line: the script reads node/group from env, as in CI.

- [ ] **Step 5: Run the full suite** — `python3 -m unittest discover -s tests 2>&1 | tail -3`, expected `OK`.

- [ ] **Step 6: Commit** — `fix: evaluate the offline shard script with CI's env so aborted jobs resolve`

---

### Task 4: Live check, docs, release notes, push

**Files:**
- Modify: `SKILL.md` (frontmatter `version: 2.3.1`; step 1 text near line 62 and §Notes near line 340: child jobs are kept by name or stage, node 1 runs as `offline-floristics-*` jobs)
- Modify: `CHANGELOG.md` (new `## [2.3.1] - <today>` above `## [2.3.0]`, `### Fixed`)
- Modify: `README.md` only if it states that offline specs live only in `cypress-offline-child` (grep first)

- [ ] **Step 1: Live run on the evidence pipeline**

Run (in the scratchpad, not the repo):
`python3 /home/luke/gitlab-pipeline-analysis-skill/scripts/pipeline_failed_specs.py 2966178222 2>&1 | tail -15`
Expected: more than one cypress job attempt; `job 17305941476 (offline-floristics-cover-fire)` is processed; `failed_specs_unique_2966178222.csv` contains `offline-floristics-consumers-cover-fire.cy.js`. Then run `scripts/extract_failures.py` on 2966178222 (check `--help` for args) and confirm a record for job 17305941476.

- [ ] **Step 2: Real-script check on `dev/1.0.12`** — evaluate `planned_offline_specs` against the `dev/1.0.12` script (patch `fetch_repo_file` in a one-off `python3 -c` or reuse the test helper) for `("1","cover-fire")`; expected one spec.

- [ ] **Step 3: Write docs and CHANGELOG.** CHANGELOG entry names: the filter fix (pipeline 2966178222, job 17305941476, both scripts), the spec-group parsing, and the recovery fix (`sortOfflineSpecs` was never exported; script reads env), plus the ungrouped-job guard.

- [ ] **Step 4: Full suite** — `python3 -m unittest discover -s tests 2>&1 | tail -3`, expected `OK`.

- [ ] **Step 5: Commit** — `docs: release 2.3.1 notes for offline node 1`

- [ ] **Step 6: Push to the fork.** `gh auth status` shows `LukeDerby99` active; `gh repo fork nuwanprabhath/gitlab-pipeline-analysis-skill --remote --remote-name fork` (keeps `origin` as upstream); `git push -u fork fix/offline-node-1-jobs`. Report the branch URL to Luke; do not open the PR. The design and plan docs are on the branch; ask Luke whether to drop them before the PR.
