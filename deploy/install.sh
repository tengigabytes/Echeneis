#!/usr/bin/env bash
# Echeneis — install systemd services and cron jobs.
# Run after vm-init.sh and editing .env. Re-runnable.
#
# Usage:
#   sudo bash deploy/install.sh
set -euo pipefail

INSTALL_DIR="/opt/echeneis"
SERVICE_FILE="${INSTALL_DIR}/deploy/echeneis.service"
IDLE_SERVICE_FILE="${INSTALL_DIR}/deploy/echeneis-idle.service"
AUTO_UPDATE_CRON="${INSTALL_DIR}/deploy/echeneis-auto-update.cron"
DAILY_DIGEST_CRON="${INSTALL_DIR}/deploy/echeneis-daily-digest.cron"

info()  { echo "[INFO]  $*"; }
error() { echo "[ERROR] $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (or with sudo)."
    exit 1
fi

# ── systemd service (main compose stack) ─────────────────────────────────────

info "Installing systemd service"
cp "${SERVICE_FILE}" /etc/systemd/system/echeneis.service
systemctl daemon-reload
info "Service installed. Enable with: systemctl enable --now echeneis"

# ── Anti-eviction idle service ───────────────────────────────────────────────

info "Installing anti-eviction idle service"

# Clean up legacy cron-based approach if present.
if [[ -f /etc/cron.d/echeneis-anti-eviction ]]; then
    info "Removing legacy anti-eviction cron"
    rm -f /etc/cron.d/echeneis-anti-eviction
fi

cp "${IDLE_SERVICE_FILE}" /etc/systemd/system/echeneis-idle.service
systemctl daemon-reload
systemctl enable --now echeneis-idle.service
info "Idle service enabled (target ~21% total CPU, Nice=19)"

# ── Auto-update cron ─────────────────────────────────────────────────────────

info "Installing auto-update cron job"
cp "${AUTO_UPDATE_CRON}" /etc/cron.d/echeneis-auto-update
chmod 644 /etc/cron.d/echeneis-auto-update
chmod +x "${INSTALL_DIR}/deploy/auto-update.sh"
chmod +x "${INSTALL_DIR}/deploy/notify.sh"
mkdir -p /var/lib/echeneis /var/lib/echeneis/state
info "Auto-update cron installed (every 3 minutes, gated on GitHub CI)"

# ── Daily digest cron ────────────────────────────────────────────────────────

info "Installing daily digest cron job"
cp "${DAILY_DIGEST_CRON}" /etc/cron.d/echeneis-daily-digest
chmod 644 /etc/cron.d/echeneis-daily-digest
chmod +x "${INSTALL_DIR}/deploy/daily-digest.sh"
info "Daily digest cron installed (23:00 daily)"

# ── Usage archive cron ───────────────────────────────────────────────────────

info "Installing usage archive cron job"
cp "${INSTALL_DIR}/deploy/echeneis-archive-usage.cron" /etc/cron.d/echeneis-archive-usage
chmod 644 /etc/cron.d/echeneis-archive-usage
chmod +x "${INSTALL_DIR}/deploy/archive-usage.sh"
info "Usage archive cron installed (03:15 daily, 90-day retention)"

# ── Conversation archive cron ────────────────────────────────────────────────

info "Installing conversation archive cron job"
cp "${INSTALL_DIR}/deploy/echeneis-archive-conversations.cron" \
    /etc/cron.d/echeneis-archive-conversations
chmod 644 /etc/cron.d/echeneis-archive-conversations
chmod +x "${INSTALL_DIR}/deploy/archive-conversations.sh"
info "Conversation archive cron installed (03:25 daily, 90-day FIFO retention)"

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Set up Cloudflare Tunnel:"
echo "     cloudflared service install <YOUR_TUNNEL_TOKEN>"
echo ""
echo "  2. Start Echeneis:"
echo "     sudo systemctl enable --now echeneis"
echo ""
echo "  3. Verify:"
echo "     sudo systemctl status echeneis echeneis-idle"
echo "     curl http://localhost:4000/health"
