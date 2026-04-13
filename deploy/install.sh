#!/usr/bin/env bash
# Echeneis — install systemd service and cron job.
# Run after vm-init.sh and editing .env. Re-runnable.
#
# Usage:
#   sudo bash deploy/install.sh
set -euo pipefail

INSTALL_DIR="/opt/echeneis"
SERVICE_FILE="${INSTALL_DIR}/deploy/echeneis.service"
CRON_FILE="${INSTALL_DIR}/deploy/echeneis-anti-eviction.cron"
AUTO_UPDATE_CRON="${INSTALL_DIR}/deploy/echeneis-auto-update.cron"
DAILY_DIGEST_CRON="${INSTALL_DIR}/deploy/echeneis-daily-digest.cron"

info()  { echo "[INFO]  $*"; }
error() { echo "[ERROR] $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (or with sudo)."
    exit 1
fi

# ── systemd service ──────────────────────────────────────────────────────────

info "Installing systemd service"
cp "${SERVICE_FILE}" /etc/systemd/system/echeneis.service
systemctl daemon-reload
info "Service installed. Enable with: systemctl enable --now echeneis"

# ── Anti-eviction cron ───────────────────────────────────────────────────────

info "Installing anti-eviction cron job"
cp "${CRON_FILE}" /etc/cron.d/echeneis-anti-eviction
chmod 644 /etc/cron.d/echeneis-anti-eviction
chmod +x "${INSTALL_DIR}/deploy/anti-eviction.sh"
info "Anti-eviction cron installed (hourly, adaptive load)"

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
echo "     sudo systemctl status echeneis"
echo "     curl http://localhost:4000/health"
