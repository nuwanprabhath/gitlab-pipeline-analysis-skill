Set up the unattended nightly pipeline report from the
gitlab-pipeline-analysis skill on this machine, so a cron job finds the latest
nightly GitLab pipeline, runs the skill headlessly, and leaves the Excel
reports in a folder — with no interaction from me.

The automation lives in the skill repo under `automation/`
(`nightly_report.sh`, `find_nightly_pipeline.py`, `config.example.env`). Do not
rewrite them; configure and wire them up.

Steps:

1. **Check prerequisites** and stop with a clear message if any fail:
   - The skill is installed: `~/.claude/skills/gitlab-pipeline-analysis/SKILL.md`
     exists (if not, tell me to run the install prompt in `INSTALL_PROMPT.md`
     first). Report its `version:`.
   - `claude --version`, `glab auth status`, `python3 --version`.
   - `crontab -l` works (if there is no crontab yet that's fine).
   - `notify-send` (for the start/finish desktop notifications). If it's
     missing, tell me the install command for this distro (Ubuntu:
     `sudo apt install libnotify-bin`) and ask whether to install it or run
     without notifications — it's optional, the job works either way.
   - This is Linux/macOS. On Windows, stop and say cron isn't available.
     On macOS note that `notify-send` doesn't exist and cron needs Full Disk
     Access, so it's really a Linux setup.

2. **Ask me these questions** (use AskUserQuestion, one round, with the stated
   defaults pre-selected as the recommended option):
   - **Output folder** — where the `.xlsx` reports and run logs go. Default:
     the current working directory. It must be stable across runs: the skill's
     "New failure" column compares against the previous run's file in that
     same folder.
   - **Frequency** — when the cron job runs. Default: **weekdays at 9:00 AM**
     local (`0 9 * * 1-5`). Offer daily-at-9 and a custom cron expression as
     alternatives. Show me the human-readable meaning of whatever expression
     we end up with.
   - **Nightly detection method** — how to pick "the latest nightly pipeline".
     Default: **trigger-user + overnight window** (newest pipeline started by
     user `tomantonic` between 11pm and 2am local). Alternatives:
     *ref pattern* (newest pipeline on a ref such as `dev/*`) and
     *GitLab schedule id* (newest pipeline from a specific CI/CD schedule).
     If I pick a non-default, ask for the one extra value it needs
     (trigger username / ref / schedule id).
   - **GitLab project** — default `ternandsparrow/paratoo-fdcp`.

3. **Write the config.** Copy
   `~/.claude/skills/gitlab-pipeline-analysis/automation/config.example.env` to
   `automation/config.env` in the same folder and set `PROJECT`, `OUT_DIR`,
   `DETECTION_MODE` and its mode-specific value from my answers. Leave the
   other keys at their defaults. `config.env` is gitignored, so a later
   `git pull` of the skill won't overwrite it. If `config.env` already exists,
   show me the current values and confirm before overwriting.

4. **Make the runner executable**: `chmod +x automation/nightly_report.sh`.

5. **Confirm notifications reach my desktop.** Before the long run, fire a test
   notification (`notify-send "Pipeline report" "test"`) and ask me whether it
   appeared. Cron has no session bus, so if it works in the terminal but not
   from cron later, the cause is `DBUS_SESSION_BUS_ADDRESS`/`DISPLAY` — the
   runner sets both, but on a non-standard session they may need adjusting.
   Skip this step if I chose to run without notifications.

6. **Smoke-test it before scheduling.** Run
   `automation/nightly_report.sh` once in the foreground and show me the
   result. It takes several minutes. Tell me which pipeline it resolved and
   whether the two `.xlsx` files appeared in the output folder. If it fails,
   diagnose from the log it wrote and fix the config — do not install the cron
   entry until a manual run succeeds.

7. **Install the cron entry**, preserving any existing crontab (read it, append,
   write back — never replace it wholesale). Show me the line before adding it:

   ```
   <SCHEDULE> /home/<me>/.claude/skills/gitlab-pipeline-analysis/automation/nightly_report.sh
   ```

   Cron runs with a minimal environment, so if `claude`/`glab` live somewhere
   unusual, add the needed `PATH=` line at the top of the crontab. If an entry
   for `nightly_report.sh` is already present, update it in place instead of
   adding a duplicate.

8. **Confirm and summarize**: the resolved config values, the cron schedule in
   plain English, where the reports and logs land, and how to change things
   later (edit `automation/config.env` for detection/project/output, `crontab -e`
   for the schedule). Also tell me how to disable it (remove the crontab line).

Notes for you while doing this:
- The runner uses `claude -p` with an explicit tool allowlist — it deliberately
  cannot create GitLab issues, and it never uses `bypassPermissions`. Don't
  loosen that.
- The unattended run never opens a ticket and always compares against the most
  recent previous report in the output folder if one exists.
