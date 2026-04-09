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

info "Rebuilding Docker images"
docker compose build

info "Restarting service"
systemctl restart echeneis

echo ""
info "Update complete. Status:"
systemctl status echeneis --no-pager -l
