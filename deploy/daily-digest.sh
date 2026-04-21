#!/usr/bin/env bash
# Echeneis — daily digest notifier.
#
# Queries the running gateway's /health and /models endpoints,
# formats a short summary, and sends it to Telegram.
#
# Usage:
#   sudo bash deploy/daily-digest.sh
#
# Install as cron (see deploy/echeneis-daily-digest.cron).
set -euo pipefail

INSTALL_DIR="/opt/echeneis"

# shellcheck source=deploy/notify.sh
source "${INSTALL_DIR}/deploy/notify.sh"

# Load .env so we have ECHENEIS_API_KEY.
_load_env

auth_header=()
if [[ -n "${ECHENEIS_API_KEY:-}" ]]; then
    auth_header=(-H "Authorization: Bearer ${ECHENEIS_API_KEY}")
fi

# Try candidate URLs in order: explicit env override, then both host-port
# mappings we ship (compose default 4000:4000 and the reverse-proxy variant
# 80:4000). First one that responds wins.
candidate_urls=()
if [[ -n "${ECHENEIS_GATEWAY_URL:-}" ]]; then
    candidate_urls+=("${ECHENEIS_GATEWAY_URL}")
fi
candidate_urls+=("http://localhost:4000" "http://localhost:80")

GATEWAY_URL=""
health_json=""
for url in "${candidate_urls[@]}"; do
    resp=$(curl -fsS -m 10 "${auth_header[@]}" "${url}/health/detail" 2>/dev/null || true)
    if [[ -n "${resp}" ]]; then
        GATEWAY_URL="${url}"
        health_json="${resp}"
        break
    fi
done

if [[ -z "${health_json}" ]]; then
    notify_telegram "⚠️ Echeneis 日報：gateway 無法連線 (tried: ${candidate_urls[*]})"
    exit 0
fi

models_json=$(curl -fsS -m 10 "${auth_header[@]}" "${GATEWAY_URL}/models" 2>/dev/null || echo "")

digest=$(python3 - "${health_json}" "${models_json}" <<'PY'
import json
import sys
from datetime import date

health_raw = sys.argv[1]
models_raw = sys.argv[2] if len(sys.argv) > 2 else ""

health = json.loads(health_raw)
providers = health.get("providers", {})
open_providers = [
    name
    for name, s in providers.items()
    if s.get("circuit") == "open" or not s.get("available", True)
]

lines = [f"📊 *Echeneis 日報 {date.today().isoformat()}*", ""]

if models_raw:
    try:
        models = json.loads(models_raw).get("models", [])
    except json.JSONDecodeError:
        models = []

    # Aggregate usage across models that have RPD limits.
    total_rpd_used = 0
    total_rpd_limit = 0
    rows = []
    for m in models:
        u = m.get("usage") or {}
        limit_rpd = u.get("limit_rpd") or 0
        used_rpd = u.get("used_rpd") or 0
        if limit_rpd:
            pct = 100 * used_rpd / limit_rpd
            rows.append((pct, m["name"], used_rpd, limit_rpd))
            total_rpd_used += used_rpd
            total_rpd_limit += limit_rpd

    if total_rpd_limit:
        lines.append(
            f"Requests today: {total_rpd_used} / {total_rpd_limit} "
            f"({100 * total_rpd_used / total_rpd_limit:.0f}% of aggregate quota)"
        )

    # Show any model above 70% usage (actionable signal).
    hot = [r for r in rows if r[0] >= 70]
    if hot:
        lines.append("")
        lines.append("⚠️ *Quota 接近上限*:")
        for pct, name, used, limit in sorted(hot, reverse=True):
            lines.append(f"  • {name}: {used}/{limit} ({pct:.0f}%)")

if open_providers:
    lines.append("")
    lines.append("⚠️ *Circuit open*: " + ", ".join(f"`{p}`" for p in open_providers))
else:
    lines.append("")
    lines.append("Provider 狀態: 全部 healthy ✅")

print("\n".join(lines))
PY
)

notify_telegram "${digest}"
