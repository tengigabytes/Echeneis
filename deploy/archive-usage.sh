#!/usr/bin/env bash
# Echeneis — compact user_usage.jsonl by moving entries older than 90 days
# into user_usage.archive.jsonl.gz. Intended for nightly cron.
#
# Runs the archive inside the bot container so module imports resolve
# against the same code that records usage in the first place.
set -euo pipefail

INSTALL_DIR="/opt/echeneis"
LOG_FILE="/var/log/echeneis-archive.log"

log() { echo "$(date -Is) $*" >> "${LOG_FILE}"; }

cd "${INSTALL_DIR}"

report=$(sudo docker compose exec -T bot \
    python -c "from echeneis.bot.user_usage import archive_old_entries; import json; print(json.dumps(archive_old_entries()))" \
    2>&1) || {
    log "ERROR: archive invocation failed: ${report}"
    exit 1
}

log "${report}"
