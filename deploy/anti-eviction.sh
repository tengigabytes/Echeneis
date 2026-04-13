#!/usr/bin/env bash
# Anti-eviction: keep Oracle Cloud Free Tier VM alive.
#
# Oracle reclaims idle instances when CPU < 20% (95th percentile over 7 days).
# Targets ~80% total system CPU; nice -n 19 ensures real services take priority.
# Duration adapts based on 7-day duty accumulation — just enough to stay above
# the weekly minimum, no more.
#
# Scheduled via cron every hour.
set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────

TARGET_CPU=80           # Target total system CPU %
MIN_STRESS=25           # Floor: stress alone must exceed 20% threshold
MAX_STRESS=75           # Ceiling: leave headroom
MIN_DURATION=180        # 3 min — when well ahead of target
MAX_DURATION=600        # 10 min — when behind
BASE_DURATION=300       # 5 min — normal
WEEKLY_TARGET=604       # 504 min (5% of 10080) + 100 min safety margin
VM_BYTES="2g"           # RAM to hold during stress
NCPU=$(nproc)

STATE_DIR="${ECHENEIS_STATE_DIR:-/var/lib/echeneis/state}"
STATE_FILE="${STATE_DIR}/anti-eviction.log"

# Host /proc may be mounted at /host/proc inside containers.
if [[ -f /host/proc/loadavg ]]; then
    PROC_LOADAVG="/host/proc/loadavg"
else
    PROC_LOADAVG="/proc/loadavg"
fi

# ── Functions ───────────────────────────────────────────────────────────────

get_cpu_pct() {
    awk -v n="$NCPU" '{
        pct = ($1 / n) * 100
        if (pct > 100) pct = 100
        printf "%d", pct
    }' "$PROC_LOADAVG"
}

get_weekly_duty() {
    local cutoff=$(( $(date +%s) - 7 * 86400 ))
    if [[ ! -f "$STATE_FILE" ]]; then
        echo 0
        return
    fi
    awk -v cutoff="$cutoff" '
        $1 >= cutoff { sum += $2 / 60 }
        END { printf "%d", sum + 0 }
    ' "$STATE_FILE"
}

prune_state() {
    if [[ ! -f "$STATE_FILE" ]]; then return; fi
    local cutoff=$(( $(date +%s) - 8 * 86400 ))
    awk -v cutoff="$cutoff" '$1 >= cutoff' "$STATE_FILE" > "${STATE_FILE}.tmp" \
        && mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

# ── Main ────────────────────────────────────────────────────────────────────

mkdir -p "$STATE_DIR"

# 1. Stress load = headroom to 80%, clamped
current_cpu=$(get_cpu_pct)
stress_load=$(( TARGET_CPU - current_cpu ))
(( stress_load < MIN_STRESS )) && stress_load=$MIN_STRESS
(( stress_load > MAX_STRESS )) && stress_load=$MAX_STRESS

# 2. Duration: adapt to weekly duty deficit
weekly_duty=$(get_weekly_duty)
deficit=$(( WEEKLY_TARGET - weekly_duty ))

if (( deficit > 400 )); then
    duration=$MAX_DURATION
elif (( deficit <= 0 )); then
    duration=$MIN_DURATION
else
    range=$(( MAX_DURATION - MIN_DURATION ))
    duration=$(( MIN_DURATION + (range * deficit) / 400 ))
fi

logger -t anti-eviction \
    "CPU=${current_cpu}% stress=${stress_load}% duration=${duration}s duty=${weekly_duty}/${WEEKLY_TARGET}min deficit=${deficit}"

# 3. Emit plan to stdout (parsed by /eviction run handler)
echo "PLAN ${stress_load} ${duration} ${weekly_duty} ${WEEKLY_TARGET}"

# 4. Run stress-ng (nice -n 19: real services always win)
nice -n 19 stress-ng \
    --cpu "$NCPU" --cpu-load "$stress_load" \
    --vm 1 --vm-bytes "$VM_BYTES" --vm-keep --vm-hang 0 \
    --timeout "${duration}s" --quiet

# 5. Log duration for duty tracking
# Format: <epoch> <duration_seconds>
echo "$(date +%s) ${duration}" >> "$STATE_FILE"
prune_state

logger -t anti-eviction "Complete. +$(( duration / 60 ))min duty"
