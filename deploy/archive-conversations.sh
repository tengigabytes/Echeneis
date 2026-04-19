#!/usr/bin/env bash
# Echeneis — daily FIFO drop of conversation entries older than 90 days.
# Runs inside the bot container so the same code path handles the rewrite.
set -euo pipefail

INSTALL_DIR="/opt/echeneis"
LOG_FILE="/var/log/echeneis-archive.log"

log() { echo "$(date -Is) [convo] $*" >> "${LOG_FILE}"; }

cd "${INSTALL_DIR}"

report=$(sudo docker compose exec -T bot \
    python -c "from echeneis.bot.conversation import ConversationManager; import json; print(json.dumps(ConversationManager().archive_all()))" \
    2>&1) || {
    log "ERROR: archive invocation failed: ${report}"
    exit 1
}

log "${report}"
