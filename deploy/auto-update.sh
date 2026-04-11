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
LOG_FILE="/var/log/echeneis-deploy.log"
REPO="tengigabytes/Echeneis"
BRANCH="main"

mkdir -p "$(dirname "${STATE_FILE}")"
touch "${LOG_FILE}"

log() { echo "$(date -Is) $*" >> "${LOG_FILE}"; }

# shellcheck source=deploy/notify.sh
source "${INSTALL_DIR}/deploy/notify.sh"

cd "${INSTALL_DIR}"

# Fetch without merging.
git fetch --quiet origin "${BRANCH}"
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

response=$(curl -fsSL "${auth_header[@]}" \
    -H "Accept: application/vnd.github+json" \
    "${api_url}" 2>&1) || {
    log "ERROR: failed to query GitHub API for ${remote_sha}: ${response}"
    exit 1
}

state=$(echo "${response}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("state",""))')

# Also check check-runs (Actions uses these, not classic statuses).
checks_url="https://api.github.com/repos/${REPO}/commits/${remote_sha}/check-runs"
checks=$(curl -fsSL "${auth_header[@]}" \
    -H "Accept: application/vnd.github+json" \
    "${checks_url}" 2>&1) || {
    log "ERROR: failed to query check-runs for ${remote_sha}"
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
        ;;
    pending|none)
        # CI still running or not reported yet — try again next cron cycle.
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

# CI is green — run the existing update script.
deploy_start=$(date +%s)
if bash "${INSTALL_DIR}/deploy/update.sh" >> "${LOG_FILE}" 2>&1; then
    deploy_elapsed=$(( $(date +%s) - deploy_start ))
    echo "${remote_sha}" > "${STATE_FILE}"
    log "commit ${remote_sha:0:8} deployed successfully in ${deploy_elapsed}s"
    range="${short_new}"
    if [[ -n "${short_old}" ]]; then
        range="${short_old}..${short_new}"
    fi
    notify_telegram "✅ Echeneis 已部署 \`${range}\` (${deploy_elapsed}s)
${commit_subject}
[GitHub](${commit_url})"
else
    log "ERROR: update.sh failed for ${remote_sha:0:8}"
    notify_telegram "❌ Echeneis 部署失敗 \`${short_new}\`
${commit_subject}
查看 /var/log/echeneis-deploy.log"
    exit 1
fi
