#!/usr/bin/env bash
# Anti-eviction: keep Oracle Cloud Free Tier VM alive.
# Oracle reclaims idle instances when CPU < 20% (95th percentile over 7 days).
# This script runs stress-ng on all 4 CPUs for 30 seconds.
# Scheduled via cron every 4 hours.
set -euo pipefail

logger -t anti-eviction "Starting CPU stress (30s, 4 CPUs)"
stress-ng --cpu 4 --timeout 30s --quiet
logger -t anti-eviction "CPU stress complete"
