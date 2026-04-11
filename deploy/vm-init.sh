#!/usr/bin/env bash
# Echeneis — Oracle Cloud Free Tier ARM VM bootstrap script.
# Run once on a fresh Ubuntu 24.04 ARM64 instance as root.
#
# Usage:
#   sudo bash vm-init.sh [--timezone Asia/Tokyo]
set -euo pipefail

TIMEZONE="${1:-Asia/Tokyo}"
REPO_URL="https://github.com/tengigabytes/Echeneis.git"
INSTALL_DIR="/opt/echeneis"
SWAP_SIZE="4G"

# ── Helpers ──────────────────────────────────────────────────────────────────

info()  { echo "[INFO]  $*"; }
error() { echo "[ERROR] $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (or with sudo)."
    exit 1
fi

# ── Timezone ─────────────────────────────────────────────────────────────────

info "Setting timezone to ${TIMEZONE}"
timedatectl set-timezone "${TIMEZONE}"

# ── System update ────────────────────────────────────────────────────────────

info "Updating system packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

# ── Essential packages ───────────────────────────────────────────────────────

info "Installing base packages"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates curl gnupg lsb-release \
    ufw fail2ban stress-ng git

# ── Docker CE (official ARM64 repo) ─────────────────────────────────────────

if ! command -v docker &>/dev/null; then
    info "Installing Docker CE"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu \
        $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list

    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-compose-plugin

    systemctl enable --now docker
    usermod -aG docker ubuntu
    info "Docker installed — user 'ubuntu' added to docker group"
else
    info "Docker already installed, skipping"
fi

# ── Cloudflared ──────────────────────────────────────────────────────────────

if ! command -v cloudflared &>/dev/null; then
    info "Installing cloudflared"
    ARCH=$(dpkg --print-architecture)
    CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
    curl -fsSL -o /tmp/cloudflared.deb "${CLOUDFLARED_URL}"
    dpkg -i /tmp/cloudflared.deb
    rm /tmp/cloudflared.deb
    info "cloudflared installed"
else
    info "cloudflared already installed, skipping"
fi

# ── Swap ─────────────────────────────────────────────────────────────────────

if ! swapon --show | grep -q /swapfile; then
    info "Creating ${SWAP_SIZE} swap"
    fallocate -l "${SWAP_SIZE}" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    if ! grep -q /swapfile /etc/fstab; then
        echo "/swapfile none swap sw 0 0" >> /etc/fstab
    fi
    sysctl vm.swappiness=10
    if ! grep -q vm.swappiness /etc/sysctl.conf; then
        echo "vm.swappiness=10" >> /etc/sysctl.conf
    fi
    info "Swap configured"
else
    info "Swap already active, skipping"
fi

# ── Firewall (UFW) ──────────────────────────────────────────────────────────

info "Configuring firewall"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw --force enable
info "UFW enabled — only SSH (22) open"

# ── Fail2ban ─────────────────────────────────────────────────────────────────

info "Enabling fail2ban"
systemctl enable --now fail2ban

# ── Clone repository ─────────────────────────────────────────────────────────

if [[ ! -d "${INSTALL_DIR}" ]]; then
    info "Cloning Echeneis to ${INSTALL_DIR}"
    git clone "${REPO_URL}" "${INSTALL_DIR}"
    chown -R ubuntu:ubuntu "${INSTALL_DIR}"
else
    info "${INSTALL_DIR} already exists, skipping clone"
fi

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  VM initialization complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. cp ${INSTALL_DIR}/.env.example ${INSTALL_DIR}/.env"
echo "  2. Edit ${INSTALL_DIR}/.env with your API keys"
echo "  3. Run: sudo bash ${INSTALL_DIR}/deploy/install.sh"
echo "  4. Set up Cloudflare Tunnel (see docs/deployment.md)"
echo ""
echo "NOTE: Log out and back in for docker group to take effect."
