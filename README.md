# gitlab-pipeline-analysis-skill

A [Claude Code](https://claude.com/claude-code) **skill** for triaging failed
Cypress specs in a GitLab CI pipeline. Give it a pipeline number; it produces two
CSVs of failed specs, classifies *why* each spec failed, summarizes the breakdown
by root cause, and offers to open a GitLab issue for the most actionable cluster.

Built for the `ternandsparrow/paratoo-fdcp` Cypress suite, but the project is a
flag (`-p group/project`) and the failure taxonomy is editable, so other Cypress
repos can adopt it.

## What you get

- **`failed_specs.csv`** — one row per failed spec per job (including retries).
- **`failed_specs_unique.csv`** — deduplicated, with `Passed on retry`,
  `first_failed_job_url`, and a **`failure_cause`** column.
- A grouped, human-readable breakdown of failures by cause.
- (Optional) a drafted, repro-linked GitLab issue for the dominant cluster.

## Requirements

- [`glab`](https://gitlab.com/gitlab-org/cli) installed and authenticated
  (`glab auth login`, verify with `glab auth status`).
- Python 3.8+.
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

# 1. Failed-spec CSVs
python3 "$SKILL/scripts/pipeline_failed_specs.py" 2635113652 \
  -o failed_specs.csv -u failed_specs_unique.csv

# 2. Root failure per spec (for classification)
python3 "$SKILL/scripts/extract_failures.py" 2635113652 -o failures_raw.json

# 3. Classify failures_raw.json against reference/failure_taxonomy.md, write
#    mapping.json = { "<spec>.cy.js": "<cause>", ... }   (this is the judgement step)

# 4. Add the failure_cause column
python3 "$SKILL/scripts/annotate_failure_cause.py" \
  --mapping mapping.json --csv failed_specs_unique.csv
```

A pipeline URL works in place of the number. Use `-p group/project` for a
different repo. If you have a local app checkout, the re-run command resolves
exact spec sub-paths; otherwise it emits `test/cypress/integration/**/<spec>`
globs (set `PARATOO_WEBAPP_INTEGRATION_DIR` to point at a checkout).

## How classification works

Steps 1, 2 and 4 are deterministic scripts. **Step 3 is judgement** — reading the
captured errors and deciding root cause vs. cascade vs. flake — which is why the
skill drives it through Claude using
[`reference/failure_taxonomy.md`](reference/failure_taxonomy.md). The taxonomy
maps common error signatures to concise labels and encodes the rules (root vs
cascade, flaky-on-retry, pre-existing/unrelated). Extend it as new patterns show
up.

## Repo layout

```
SKILL.md                          # the skill: workflow Claude follows
README.md
scripts/
  pipeline_failed_specs.py        # → failed_specs.csv + failed_specs_unique.csv
  extract_failures.py             # → failures_raw.json (root failure per spec)
  annotate_failure_cause.py       # mapping.json → failure_cause column
reference/
  failure_taxonomy.md             # signatures → cause labels + classification rules
  ticket_template.md              # structure for the GitLab issue
examples/
  failed_specs_unique.example.csv # sample annotated output
```

## License

MIT — see [LICENSE](LICENSE).
