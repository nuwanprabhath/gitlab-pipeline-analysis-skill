Install (or update) the GitLab pipeline failure-analysis skill from
https://github.com/nuwanprabhath/gitlab-pipeline-analysis-skill into my
personal Claude Code skills directory so it's available across all projects.

Steps:
1. Check prerequisites: `glab auth status` (GitLab CLI must be installed and
   authenticated — if not, tell me to run `glab auth login` first) and
   `python3 --version`.
2. Create ~/.claude/skills/ if it doesn't exist. Then:
   - If ~/.claude/skills/gitlab-pipeline-analysis already exists and is a git
     checkout, first read its current `version:` from SKILL.md's frontmatter
     (this is the "before" version), then update it in place: `cd` into it
     and run `git pull`.
   - Otherwise, clone the repo fresh into
     ~/.claude/skills/gitlab-pipeline-analysis.
3. Make the scripts executable: chmod +x scripts/*.py inside that directory.
4. Read the (possibly new) SKILL.md `version:` field (the "after" version).
   Summarize back to me what the skill does and how to invoke it (e.g.
   "Analyze pipeline <number>").
5. Don't run any pipeline analysis yet — just confirm the install/update
   succeeded. Report:
   - Fresh install vs. update, and the commit (`git log -1 --oneline`).
   - If it was an update and the version changed, show old → new version and
     print the matching section(s) of CHANGELOG.md so I know what's new.
   - If it was an update and the version did NOT change, say so explicitly
     (nothing new to report).
