#!/usr/bin/env bash
# Echeneis — auto-deploy gate.
#
# Polls origin/main, and if a new commit exists AND its GitHub CI run
# is green, runs update.sh. Safe to run via cron every few minutes.
#
# Usage:
#   sudo bash deploy/auto-update.sh
#
# Install as cron (see deploy/echeneis-auto-update.cron).
set -euo pipefail

INSTALL_DIR="/opt/echeneis"
STATE_FILE="/var/lib/echeneis/deployed_sha"
PENDING_FILE="/var/lib/echeneis/last_pending_sha"
LOG_FILE="/var/log/echeneis-deploy.log"
REPO="tengigabytes/Echeneis"
BRANCH="main"

mkdir -p "$(dirname "${STATE_FILE}")"
touch "${LOG_FILE}"

log() { echo "$(date -Is) $*" >> "${LOG_FILE}"; }

# shellcheck source=deploy/notify.sh
source "${INSTALL_DIR}/deploy/notify.sh"

# Load /opt/echeneis/.env so GITHUB_TOKEN is available for the GitHub API
# calls below (5000 req/hr authenticated vs 60 anon — anon rate limits
# were silently killing cron runs before this fix).
_load_env

cd "${INSTALL_DIR}"

# Fetch without merging. Log failures (network, auth) instead of exiting
# silently through set -e.
fetch_err=$(git fetch --quiet origin "${BRANCH}" 2>&1) || {
    log "ERROR: git fetch failed: ${fetch_err}"
    exit 1
}
remote_sha=$(git rev-parse "origin/${BRANCH}")
deployed_sha=$(cat "${STATE_FILE}" 2>/dev/null || echo "")

if [[ "${remote_sha}" == "${deployed_sha}" ]]; then
    exit 0
fi

# New commit detected — check CI status before deploying.
# GitHub's combined status API covers both classic statuses and check-runs.
api_url="https://api.github.com/repos/${REPO}/commits/${remote_sha}/status"
auth_header=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    auth_header=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
fi

# github_api_get: curl a GitHub API URL with optional auth. If the auth
# call returns 401 (token expired / revoked), clear the auth header and
# retry anonymously — on public repos the read-only endpoints we use
# are accessible without auth, just at a lower rate limit. Without this
# fallback, a single expired PAT locks cron out of all future deploys.
github_api_get() {
    local url="$1"
    local out
    out=$(curl -fsSL "${auth_header[@]}" \
        -H "Accept: application/vnd.github+json" \
        "${url}" 2>&1) && {
        printf '%s' "${out}"
        return 0
    }
    if [[ ${#auth_header[@]} -gt 0 ]] && [[ "${out}" == *"error: 401"* ]]; then
        log "GitHub API 401 with token — revoked or expired, falling back to anonymous"
        auth_header=()
        out=$(curl -fsSL \
            -H "Accept: application/vnd.github+json" \
            "${url}" 2>&1) && {
            printf '%s' "${out}"
            return 0
        }
    fi
    printf '%s' "${out}"
    return 1
}

response=$(github_api_get "${api_url}") || {
    log "ERROR: failed to query GitHub API for ${remote_sha}: ${response}"
    exit 1
}

state=$(echo "${response}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("state",""))')

# Also check check-runs (Actions uses these, not classic statuses).
# auth_header may have been cleared by the first call's 401 fallback.
checks_url="https://api.github.com/repos/${REPO}/commits/${remote_sha}/check-runs"
checks=$(github_api_get "${checks_url}") || {
    log "ERROR: failed to query check-runs for ${remote_sha}: ${checks}"
    exit 1
}

checks_conclusion=$(echo "${checks}" | python3 -c '
import sys, json
data = json.load(sys.stdin)
runs = data.get("check_runs", [])
if not runs:
    print("none")
    sys.exit(0)
for r in runs:
    if r.get("status") != "completed":
        print("pending")
        sys.exit(0)
for r in runs:
    if r.get("conclusion") not in ("success", "skipped", "neutral"):
        print("failure")
        sys.exit(0)
print("success")
')

commit_subject=$(git log -1 --format='%s' "${remote_sha}" 2>/dev/null || echo "")
short_old="${deployed_sha:0:7}"
short_new="${remote_sha:0:7}"
commit_url="https://github.com/${REPO}/commit/${remote_sha}"

case "${checks_conclusion}" in
    success)
        log "commit ${remote_sha:0:8} CI green, deploying"
        rm -f "${PENDING_FILE}"
        ;;
    pending|none)
        # CI still running or not reported yet — try again next cron cycle.
        # Log once per (sha, state) transition so we can tell from the log
        # that cron is seeing pending rather than failing silently.
        last_pending=$(cat "${PENDING_FILE}" 2>/dev/null || echo "")
        marker="${remote_sha:0:8}:${checks_conclusion}"
        if [[ "${marker}" != "${last_pending}" ]]; then
            log "commit ${remote_sha:0:8} CI ${checks_conclusion}, waiting"
            echo "${marker}" > "${PENDING_FILE}"
        fi
        exit 0
        ;;
    failure)
        log "commit ${remote_sha:0:8} CI failed, skipping"
        # Record as deployed so we don't re-log on every cycle.
        # update.sh still won't run because we exit before reaching it.
        echo "${remote_sha}" > "${STATE_FILE}"
        notify_telegram "⚠️ Echeneis: commit \`${short_new}\` CI 失敗，已跳過
${commit_subject}
[GitHub](${commit_url})"
        exit 0
        ;;
    *)
        log "commit ${remote_sha:0:8} unknown CI state: ${checks_conclusion} (classic=${state})"
        exit 1
        ;;
esac

# CI is green — check if the bot has active tasks before restarting.
TASK_STATE="/var/lib/echeneis/state/active_tasks.json"
if [[ -f "${TASK_STATE}" ]]; then
    busy=$(python3 -c '
import json, time, sys
try:
    data = json.load(open(sys.argv[1]))
    for t in data.get("tasks", {}).values():
        elapsed_min = (time.time() - t["started_at"]) / 60
        if elapsed_min <= t.get("max_minutes", 30):
            print(t["name"])
            sys.exit(0)
except Exception:
    pass
' "${TASK_STATE}" 2>/dev/null || true)
    if [[ -n "${busy}" ]]; then
        log "commit ${remote_sha:0:8} deploy deferred — active task: ${busy}"
        exit 0
    fi
fi

deploy_start=$(date +%s)
if bash "${INSTALL_DIR}/deploy/update.sh" >> "${LOG_FILE}" 2>&1; then
    deploy_elapsed=$(( $(date +%s) - deploy_start ))
    log "commit ${remote_sha:0:8} deployed successfully in ${deploy_elapsed}s"
    range="${short_new}"
    if [[ -n "${short_old}" ]]; then
        range="${short_old}..${short_new}"
    fi
    # Collect commit subjects in the deployed range BEFORE updating
    # STATE_FILE so we still have the old deployed sha for the range.
    if [[ -n "${deployed_sha}" ]]; then
        change_summary=$(git log --format='- %s' "${deployed_sha}..${remote_sha}" 2>/dev/null || true)
    else
        change_summary=$(git log -1 --format='- %s' "${remote_sha}" 2>/dev/null || true)
    fi
    echo "${remote_sha}" > "${STATE_FILE}"
    message="✅ Echeneis 已部署 ${range} (${deploy_elapsed}s)"
    if [[ -n "${change_summary}" ]]; then
        message="${message}

${change_summary}"
    fi
    message="${message}

${commit_url}"
    notify_telegram "${message}" ""
else
    log "ERROR: update.sh failed for ${remote_sha:0:8}"
    notify_telegram "❌ Echeneis 部署失敗 \`${short_new}\`
${commit_subject}
查看 /var/log/echeneis-deploy.log"
    exit 1
fi
