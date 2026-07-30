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
     (this is the "before" version), then update it in place following step 2a.
   - Otherwise, clone the repo fresh into
     ~/.claude/skills/gitlab-pipeline-analysis.

2a. **Updating an existing checkout — handle local modifications without
    asking me.** This directory is a *consumer* checkout, not a workspace:
    anything uncommitted here is either a leftover from editing the installed
    copy in place, or work that belongs in the development repo. Never treat it
    as authoritative, and never block the update on it.

    Do this, in order:
    - Record the current commit first: `PREV=$(git rev-parse HEAD)` — you need
      it to judge the stash afterwards.
    - `git fetch origin`, then `git status --porcelain`.
    - **If clean:** `git pull` and continue.
    - **If there are local modifications:** preserve them with `git stash push
      -u -m "pre-install-<today's date>"`, then `git pull`. Nothing is lost —
      the stash keeps everything and is recoverable. (`-u` covers untracked
      files but not ignored ones, so a local `automation/config.env` is
      untouched.)
    - **Do NOT `git stash pop` afterwards.** Replaying stale local edits onto
      the freshly-pulled tree is exactly what causes the conflicts this step
      exists to avoid. Leave the stash in place and assess it instead.
    - After pulling, decide whether the stashed content was redundant. For each
      file in `git stash show --name-only stash@{0}`, in this order:
      1. `git diff --quiet stash@{0} -- <file>` — exit 0 means the stashed copy
         already matches the updated tree. **Redundant.**
      2. Otherwise, check whether it matches any version upstream already has:
         for each commit in `git log --format=%h $PREV..HEAD -- <file>`, test
         `git diff --quiet stash@{0} <commit> -- <file>`. A match means the
         stash is just an older snapshot of work already committed upstream —
         the usual case for an out-of-date `version:` line or a CHANGELOG
         missing the newest section. **Redundant.**
      3. No match on either — treat as **possibly unique**.
    - Report the verdict in step 5, and say it plainly:
      - **Redundant** — state that nothing unique was lost and give me the exact
        command to clean up (`git stash drop stash@{0}`). Do not drop it
        yourself.
      - **Possibly unique** — say so prominently, show me what is unique, and
        tell me it is preserved in `stash@{0}`. Recommend I move that work to
        the development repo rather than re-applying it here, because editing
        the installed copy in place is what caused the divergence.
    - If `git pull` still fails for any other reason (diverged branch,
      detached HEAD, a different remote), stop and show me the error rather
      than force-resetting.

3. Make the scripts executable: chmod +x scripts/*.py inside that directory.
   If an `automation/` directory is present, also chmod +x its `.sh` and `.py`
   files (its `config.env`, if you have one, is gitignored and survives pulls).
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
   - If step 2a stashed anything, the verdict on it (redundant vs possibly
     unique), and the cleanup or recovery command for `stash@{0}`.
   - Confirm the working tree is clean now (`git status --porcelain` is empty),
     so the next update is a plain fast-forward.
