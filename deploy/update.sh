#!/usr/bin/env bash
# Echeneis — pull latest code, rebuild, and restart.
#
# Usage:
#   sudo bash deploy/update.sh
set -euo pipefail

INSTALL_DIR="/opt/echeneis"

info() { echo "[INFO]  $*"; }

cd "${INSTALL_DIR}"

info "Pulling latest code"
git pull --ff-only

# git pull as root writes updated files as root. Restore ownership to
# the repo owner so subsequent cron invocations (also root) don't hit
# git's safe.directory / dubious-ownership guard on mixed ownership.
repo_owner=$(stat -c '%U:%G' "${INSTALL_DIR}/.git")
if [[ -n "${repo_owner}" && "${repo_owner}" != ":" ]]; then
    chown -R "${repo_owner}" "${INSTALL_DIR}" 2>/dev/null || true
fi

GIT_SHA=$(git rev-parse --short HEAD)
info "Rebuilding Docker images (${GIT_SHA})"
GIT_SHA="${GIT_SHA}" docker compose build --build-arg GIT_SHA="${GIT_SHA}"

info "Restarting service"
systemctl restart echeneis

echo ""
info "Update complete. Status:"
systemctl status echeneis --no-pager -l
