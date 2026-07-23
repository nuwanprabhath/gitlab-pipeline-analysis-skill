# gitlab-pipeline-analysis-skill

A [Claude Code](https://claude.com/claude-code) **skill** for triaging failed
Cypress specs in a GitLab CI pipeline. Give it a pipeline number; it produces two
CSVs of failed specs, classifies *why* each spec failed, summarizes the breakdown
by root cause, and offers to open a GitLab issue for the most actionable cluster.

Built for the `ternandsparrow/paratoo-fdcp` Cypress suite, but the project is a
flag (`-p group/project`) and the failure taxonomy is editable, so other Cypress
repos can adopt it.

## What you get

- **`failed_specs_unique_<pid>.xlsx`** — the primary deliverable: deduplicated,
  sorted by spec, with `New failure` (regression vs the previous run),
  `bug_likelihood_(AI)`, `failure_cause`, clickable job links, red cells for
  likely-real bugs and green rows for flaky (passed-on-retry) specs.
- **`failed_specs_<pid>.xlsx`** — one row per failed spec per job (incl. retries).
- CSV versions of both (the machine-readable working files behind the workbooks).
- A grouped, human-readable breakdown of failures by cause.
- (Optional) a drafted, repro-linked GitLab issue for the dominant cluster.

## Requirements

- [`glab`](https://gitlab.com/gitlab-org/cli) installed and authenticated
  (`glab auth login`, verify with `glab auth status`).
- Python 3.8+ — **standard library only, no `pip install`** (the `.xlsx`
  writer is built in), so it runs on any OS out of the box.
- Claude Code (to run the skill). The scripts also work standalone (see below).

## Install (as a Claude Code skill)

Clone into your personal or project skills directory:

```bash
# personal (all projects)
git clone <repo-url> ~/.claude/skills/gitlab-pipeline-analysis

# or project-local
git clone <repo-url> .claude/skills/gitlab-pipeline-analysis
```

Then in Claude Code just ask, e.g.:

> Analyze pipeline 2635113652 and tell me what's failing.

Claude will discover the skill via its `description` and run the workflow in
[`SKILL.md`](SKILL.md). You can also clone anywhere and point Claude at the
folder.

### Updating

The install directory is a plain git checkout, so pulling picks up fixes:

```bash
cd ~/.claude/skills/gitlab-pipeline-analysis && git pull
```

If you installed via a Claude Code prompt, use one that checks for an
existing checkout and pulls instead of re-cloning — see
[`INSTALL_PROMPT.md`](INSTALL_PROMPT.md) for a copy-pasteable version that
works for both first install and updates.

## Usage (standalone scripts)

From any directory (CSVs are written to the current directory):

```bash
SKILL=~/.claude/skills/gitlab-pipeline-analysis

# 1. Failed-spec CSVs (defaults: failed_specs_<pid>.csv, failed_specs_unique_<pid>.csv)
python3 "$SKILL/scripts/pipeline_failed_specs.py" 2635113652

# 2. Flag specs that are new vs the previous run (populates the New failure column).
#    Auto-detects the most recent prior failed_specs_unique_*.csv in the folder;
#    --detect-only just prints it. Omit/skip on a first-ever run (stays N/A).
python3 "$SKILL/scripts/compare_new_failures.py" \
  --current failed_specs_unique_2635113652.csv

# 3. Root failure per spec (for classification)
python3 "$SKILL/scripts/extract_failures.py" 2635113652

# 4. Classify failures_raw_<pid>.json against reference/failure_taxonomy.md, write
#    mapping_<pid>.json = { "<spec>.cy.js": {"failure_cause": "...", "bug_likelihood": "LOW|MEDIUM|HIGH"} }

# 5. Add the failure_cause + bug_likelihood_(AI) columns
python3 "$SKILL/scripts/annotate_failure_cause.py" \
  --mapping mapping_2635113652.json --csv failed_specs_unique_2635113652.csv

# 6. Export the formatted Excel deliverables (sorted, coloured, clickable links).
#    --remove-source deletes each CSV after export; then drop the JSON working
#    files, leaving only the two .xlsx in the folder.
python3 "$SKILL/scripts/export_xlsx.py" --csv failed_specs_unique_2635113652.csv --remove-source
python3 "$SKILL/scripts/export_xlsx.py" --csv failed_specs_2635113652.csv --remove-source
rm -f failures_raw_2635113652.json mapping_2635113652.json
```

Each skill run leaves exactly two files — `failed_specs_unique_<pid>.xlsx`
(primary) and `failed_specs_<pid>.xlsx` — flat in the working directory. Keep
all runs in one folder (the pipeline-id suffix prevents collisions) so the
`New failure` comparison can find prior runs; don't nest runs in per-pipeline
subfolders.

A pipeline URL works in place of the number. Use `-p group/project` for a
different repo. If you have a local app checkout, the re-run command resolves
exact spec sub-paths; otherwise it emits `test/cypress/integration/**/<spec>`
globs (set `PARATOO_WEBAPP_INTEGRATION_DIR` to point at a checkout).

## How classification works

The extraction, new-failure comparison, and annotation scripts are
deterministic. **Classification is judgement** — reading the captured errors
(and, for anything that isn't a known Cypress-glitch signature, the spec/custom-
command code at the pipeline's commit) to decide root cause vs. cascade vs.
flake and a `bug_likelihood_(AI)` of LOW/MEDIUM/HIGH — which is why the skill
drives it through Claude using
[`reference/failure_taxonomy.md`](reference/failure_taxonomy.md). The taxonomy
maps common error signatures to labels, separates Cypress glitches from real app
bugs, and encodes the rules (root vs cascade, flaky-on-retry, stale-test vs
regression). Extend it as new patterns show up.

## Repo layout

```
SKILL.md                          # the skill: workflow Claude follows
README.md
scripts/
  pipeline_failed_specs.py        # → failed_specs_<pid>.csv + failed_specs_unique_<pid>.csv
  compare_new_failures.py         # New failure column: yes/no/N/A vs the previous run (.csv or .xlsx)
  extract_failures.py             # → failures_raw_<pid>.json (root failure + spec code refs)
  annotate_failure_cause.py       # mapping_<pid>.json → failure_cause + bug_likelihood_(AI)
  error_kind_enforce.py           # deterministic guard: value/app-error specs can't ship as LOW/glitch
  export_xlsx.py                  # CSV → formatted .xlsx (sort, colours, clickable links)
  xlsx.py                         # tiny dependency-free OOXML reader/writer
reference/
  failure_taxonomy.md             # signatures → cause labels + bug-likelihood rubric
  ticket_template.md              # structure for the GitLab issue
examples/
  failed_specs_unique.example.csv # sample annotated output
tests/                             # unit tests for the scripts (stdlib unittest, no deps)
```

## Development

Unit tests cover the parsing/classification logic in `scripts/` (log parsing,
retry detection, spec-path resolution, CSV annotation, new-failure comparison,
and the `.xlsx` reader/writer + formatting) using only the standard library —
no dependencies to install. Run them from the repo root:

```bash
python3 -m unittest discover -s tests -v
```

Tests use synthetic GitLab CI trace text (see `tests/fixtures.py`), not live
`glab` calls, so they run offline and fast. When changing parsing logic in
`pipeline_failed_specs.py` or `extract_failures.py`, add a fixture-based case
rather than only testing against a live pipeline — that's what keeps
regressions like the OOM-crash detection fix (see `CHANGELOG.md`) from
resurfacing silently.

## License

MIT — see [LICENSE](LICENSE).
