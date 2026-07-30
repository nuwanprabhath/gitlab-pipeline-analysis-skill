#!/usr/bin/env bash
# Unattended nightly GitLab pipeline report.
#
# Finds the latest nightly pipeline, runs the gitlab-pipeline-analysis skill
# headlessly via `claude -p`, and leaves the two .xlsx deliverables in OUT_DIR.
# No GitLab ticket is created; the previous run is compared automatically.
#
# Configure via config.env next to this script (see config.example.env).
# Intended to be driven by cron; safe to run by hand for a smoke test.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# cron gives a minimal PATH; append the usual user-install locations as
# fallbacks. Appended, not prepended, so an explicit PATH from the caller still
# takes precedence.
export PATH="$PATH:$HOME/.local/bin:$HOME/bin:/usr/local/bin:/usr/bin:/bin"

CONFIG="${NIGHTLY_REPORT_CONFIG:-$HERE/config.env}"
if [ ! -f "$CONFIG" ]; then
  echo "Missing config: $CONFIG (copy config.example.env to config.env)" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$CONFIG"

: "${PROJECT:?PROJECT not set in config}"
: "${OUT_DIR:?OUT_DIR not set in config}"
DETECTION_MODE="${DETECTION_MODE:-trigger_window}"
SCAN_COUNT="${SCAN_COUNT:-300}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-24}"
SKIP_IF_ALREADY_ANALYZED="${SKIP_IF_ALREADY_ANALYZED:-yes}"
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-30}"

OUT_DIR="${OUT_DIR/#\~/$HOME}"
mkdir -p "$OUT_DIR"
LOG_FILE="$OUT_DIR/nightly_$(date +%F_%H%M).log"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"; }

# --- desktop notifications ---------------------------------------------------
# cron runs without a session bus or display, so notify-send would silently do
# nothing. Point it at the logged-in user's bus explicitly; if that isn't
# resolvable (headless box, no notify-send, macOS) notifications are skipped
# and the run carries on regardless — a notification must never fail the job.
NOTIFY="${NOTIFY:-yes}"
if [ "$NOTIFY" = "yes" ] && command -v notify-send >/dev/null 2>&1; then
  : "${DISPLAY:=:0}"
  export DISPLAY
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "/run/user/$(id -u)/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
  fi
else
  NOTIFY="no"
fi

notify() {
  # notify <urgency> <title> <body>
  [ "$NOTIFY" = "yes" ] || return 0
  notify-send --app-name="Pipeline report" --urgency="$1" \
    --icon="${NOTIFY_ICON:-utilities-terminal}" "$2" "$3" >/dev/null 2>&1 || true
}

fail() {
  log "ERROR: $*"
  notify critical "Pipeline report failed" "$*"
  exit 1
}

command -v claude >/dev/null 2>&1 || fail "claude CLI not on PATH"
command -v glab   >/dev/null 2>&1 || fail "glab not on PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 not on PATH"
glab auth status >/dev/null 2>&1 || fail "glab is not authenticated (run: glab auth login)"

log "Looking for the latest nightly pipeline in $PROJECT (mode=$DETECTION_MODE)"

PIPELINE_ID="$(python3 "$HERE/find_nightly_pipeline.py" \
  -p "$PROJECT" \
  -m "$DETECTION_MODE" \
  --trigger-user "${TRIGGER_USER:-}" \
  --window-start-hour "${WINDOW_START_HOUR:-23}" \
  --window-end-hour "${WINDOW_END_HOUR:-2}" \
  --ref "${REF:-}" \
  --schedule-id "${SCHEDULE_ID:-}" \
  --scan "$SCAN_COUNT" \
  --max-age-hours "$MAX_AGE_HOURS" 2>>"$LOG_FILE")" \
  || fail "could not resolve a nightly pipeline (see log above)"

log "Resolved pipeline #$PIPELINE_ID"

if [ "$SKIP_IF_ALREADY_ANALYZED" = "yes" ] &&
   [ -f "$OUT_DIR/failed_specs_unique_${PIPELINE_ID}.xlsx" ]; then
  log "Already analyzed (failed_specs_unique_${PIPELINE_ID}.xlsx exists) — nothing to do."
  notify low "Pipeline #$PIPELINE_ID — already analyzed" \
    "Report already exists in $OUT_DIR; skipped."
  exit 0
fi

cd "$OUT_DIR"

PROMPT="Use the gitlab-pipeline-analysis skill to analyze GitLab pipeline ${PIPELINE_ID} \
of project ${PROJECT}. Work entirely in the current working directory (${OUT_DIR}) and \
write all outputs flat into it.

This is an UNATTENDED cron run. Follow these overrides to the skill:
- Never ask a question. AskUserQuestion is unavailable; if a step says to ask, take the \
default described below instead and continue.
- Step 3 (new-failure comparison): run compare_new_failures.py with --detect-only. If it \
prints a previous run's file, immediately compare against it — do not ask. If it prints \
nothing, skip the comparison and leave the New failure column as N/A.
- Step 9 (GitLab ticket): SKIP ENTIRELY. Do not draft, offer, or create any GitLab issue.
- Step 7: run both export_xlsx.py commands, but do NOT delete the intermediate JSON files \
(the calling script cleans those up).
- There is no local checkout of the app; read code at the pipeline commit via glab api if \
you need it.

Finish by printing the step 8 summary: failures grouped by failure_cause with counts, \
with newly-introduced failures and HIGH bug-likelihood specs called out first."

# Tight allowlist: reads and the skill's own scripts only. Notably absent are
# `rm` (this script does the cleanup) and any writing glab subcommand, so an
# unattended run cannot create issues/MRs or delete anything outside OUT_DIR.
ALLOWED_TOOLS=(
  "Read" "Write" "Edit" "Glob" "Grep" "TodoWrite"
  "Bash(python3:*)"
  "Bash(glab api:*)"
  "Bash(glab auth status)"
  "Bash(glab pipeline list:*)"
  "Bash(glab pipeline get:*)"
  "Bash(git log:*)" "Bash(git show:*)" "Bash(git grep:*)"
  "Bash(ls:*)"
)
DISALLOWED_TOOLS=(
  "Bash(glab issue:*)" "Bash(glab mr:*)" "Bash(glab repo:*)" "Bash(rm:*)"
  "WebFetch" "WebSearch"
)

CLAUDE_ARGS=(-p "$PROMPT")
if [ -n "${CLAUDE_MODEL:-}" ]; then
  CLAUDE_ARGS+=(--model "$CLAUDE_MODEL")
fi
CLAUDE_ARGS+=(--allowedTools "${ALLOWED_TOOLS[@]}")
CLAUDE_ARGS+=(--disallowedTools "${DISALLOWED_TOOLS[@]}")
if [ -n "${CLAUDE_EXTRA_ARGS:-}" ]; then
  # shellcheck disable=SC2206  # intentional word-splitting: these are extra flags
  CLAUDE_ARGS+=(${CLAUDE_EXTRA_ARGS})
fi

STARTED_AT="$(date '+%a %d %b, %H:%M')"
START_EPOCH="$(date +%s)"
log "Running the skill headlessly (this takes several minutes)..."
notify normal "Pipeline #$PIPELINE_ID — analysis started" \
  "Started $STARTED_AT
Project: $PROJECT"
set +e
claude "${CLAUDE_ARGS[@]}" >>"$LOG_FILE" 2>&1
CLAUDE_RC=$?
set -e

# Clean up the skill's intermediates ourselves, so `rm` stays off the allowlist.
rm -f "$OUT_DIR/failures_raw_${PIPELINE_ID}.json" \
      "$OUT_DIR/mapping_${PIPELINE_ID}.json" \
      "$OUT_DIR/spec_runs_${PIPELINE_ID}.json" \
      "$OUT_DIR/failed_specs_unique_${PIPELINE_ID}.csv" \
      "$OUT_DIR/all_specs_${PIPELINE_ID}.csv"

if [ "$LOG_RETENTION_DAYS" -gt 0 ] 2>/dev/null; then
  find "$OUT_DIR" -maxdepth 1 -name 'nightly_*.log' -mtime "+$LOG_RETENTION_DAYS" -delete
fi

if [ $CLAUDE_RC -ne 0 ]; then
  fail "claude exited with code $CLAUDE_RC — see $LOG_FILE"
fi

if [ ! -f "$OUT_DIR/failed_specs_unique_${PIPELINE_ID}.xlsx" ]; then
  fail "run finished but failed_specs_unique_${PIPELINE_ID}.xlsx was not produced — see $LOG_FILE"
fi

SUMMARY="$(python3 "$HERE/summarize_report.py" \
  "$OUT_DIR/failed_specs_unique_${PIPELINE_ID}.xlsx" 2>/dev/null || echo "report ready")"
ELAPSED_MIN=$(( ( $(date +%s) - START_EPOCH + 30 ) / 60 ))

log "Done. $SUMMARY. Report: $OUT_DIR/failed_specs_unique_${PIPELINE_ID}.xlsx"
notify normal "Pipeline #$PIPELINE_ID — analysis finished" \
  "$SUMMARY
Started $STARTED_AT, took ${ELAPSED_MIN} min
$OUT_DIR"
