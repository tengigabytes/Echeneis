#!/usr/bin/env bash
# Echeneis — restrict the published gateway port to Cloudflare edge IPs.
#
# Docker publishes the gateway through DNAT, so the INPUT chain never sees
# that traffic; the filter has to live in DOCKER-USER. With this applied,
# requests that reach the port directly (bypassing the Cloudflare proxy)
# are dropped. SSH and container-to-container traffic are not affected.
#
# Only use this when the gateway hostname is proxied through Cloudflare.
# Idempotent: rebuilds its own chain on every run. Rules are not persisted;
# deploy/echeneis-cf-lock.service re-applies them at boot.
#
# Usage:
#   sudo bash deploy/cf-origin-lock.sh apply
#   sudo bash deploy/cf-origin-lock.sh remove
set -euo pipefail

PORT="${ECHENEIS_PUBLIC_PORT:-80}"
CHAIN="ECHENEIS-CF"

# https://www.cloudflare.com/ips-v4 (fetched 2026-10-06)
CF_RANGES=(
    173.245.48.0/20
    103.21.244.0/22
    103.22.200.0/22
    103.31.4.0/22
    141.101.64.0/18
    108.162.192.0/18
    190.93.240.0/20
    188.114.96.0/20
    197.234.240.0/22
    198.41.128.0/17
    162.158.0.0/15
    104.16.0.0/13
    104.24.0.0/14
    172.64.0.0/13
    131.0.72.0/22
)

iface=$(ip -o route get 1.1.1.1 | sed -n 's/.* dev \([^ ]*\).*/\1/p')
if [[ -z "${iface}" ]]; then
    echo "[ERROR] could not determine the public interface" >&2
    exit 1
fi

# Match on the pre-DNAT port, original direction only, so replies to
# outbound connections made by the containers are never caught.
jump=(-i "${iface}" -p tcp -m conntrack --ctorigdstport "${PORT}"
      --ctdir ORIGINAL -j "${CHAIN}")

remove() {
    while iptables -C DOCKER-USER "${jump[@]}" 2>/dev/null; do
        iptables -D DOCKER-USER "${jump[@]}"
    done
    iptables -F "${CHAIN}" 2>/dev/null || true
    iptables -X "${CHAIN}" 2>/dev/null || true
}

apply() {
    if ! iptables -nL DOCKER-USER >/dev/null 2>&1; then
        echo "[ERROR] DOCKER-USER chain not found — is Docker running?" >&2
        exit 1
    fi
    iptables -N "${CHAIN}" 2>/dev/null || iptables -F "${CHAIN}"
    for range in "${CF_RANGES[@]}"; do
        iptables -A "${CHAIN}" -s "${range}" -j RETURN
    done
    iptables -A "${CHAIN}" -j DROP
    iptables -C DOCKER-USER "${jump[@]}" 2>/dev/null ||
        iptables -I DOCKER-USER "${jump[@]}"
}

case "${1:-}" in
    apply)
        apply
        echo "[INFO]  port ${PORT} on ${iface} limited to ${#CF_RANGES[@]} Cloudflare ranges"
        ;;
    remove)
        remove
        echo "[INFO]  port ${PORT} restriction removed"
        ;;
    *)
        echo "Usage: $0 apply|remove" >&2
        exit 2
        ;;
esac
