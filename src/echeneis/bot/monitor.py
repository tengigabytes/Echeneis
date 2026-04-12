"""Background system monitor for proactive Telegram alerts.

Periodically checks VM resources, provider quota, and gateway latency.
Sends fire-and-forget alerts when thresholds are breached.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import psutil

from echeneis.bot.gateway_client import GatewayClient, GatewayError
from echeneis.gateway.notifier import send_telegram

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_CPU_WARN_PCT = 80  # sustained CPU > 80%
_MEM_WARN_PCT = 90  # memory > 90%
_DISK_WARN_PCT = 85  # disk > 85%
_QUOTA_WARN_PCT = 90  # RPD > 90% of limit
_LATENCY_WARN_MS = 30_000  # p95 > 30s

# Cooldowns — don't repeat the same alert within this window.
_ALERT_COOLDOWN = 3600  # 1 hour
_CHECK_INTERVAL = 300  # 5 minutes

# Host /proc mounted at this path inside the container.
_HOST_PROC = "/host/proc"


# ---------------------------------------------------------------------------
# VM Resource Helpers
# ---------------------------------------------------------------------------

def _get_host_proc() -> str | None:
    """Return the host /proc path if available, else None."""
    if os.path.isdir(_HOST_PROC):
        return _HOST_PROC
    return None


def get_vm_resources() -> dict[str, Any]:
    """Collect CPU, memory, and disk metrics.

    Uses host /proc when mounted (Docker), otherwise falls back to
    container-local metrics.

    Returns:
        Dict with cpu_pct, load_1m, mem_used_gb, mem_total_gb, mem_pct,
        disk_used_gb, disk_total_gb, disk_pct.
    """
    host_proc = _get_host_proc()

    # CPU — use host /proc/loadavg if available
    if host_proc:
        try:
            with open(f"{host_proc}/loadavg") as f:
                load_1m = float(f.read().split()[0])
        except Exception:
            load_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    else:
        load_1m = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0

    cpu_count = psutil.cpu_count() or 1
    cpu_pct = (load_1m / cpu_count) * 100

    # Memory — use host /proc/meminfo if available
    if host_proc:
        mem = _parse_host_meminfo(host_proc)
    else:
        vm = psutil.virtual_memory()
        mem = {
            "total": vm.total,
            "available": vm.available,
        }
    mem_total = mem["total"]
    mem_used = mem_total - mem["available"]
    mem_pct = (mem_used / mem_total * 100) if mem_total > 0 else 0

    # Disk — check the state directory mount point
    state_dir = os.environ.get("ECHENEIS_STATE_DIR", "/app/state")
    try:
        disk = psutil.disk_usage(state_dir)
    except OSError:
        disk = psutil.disk_usage("/")
    disk_pct = disk.percent

    return {
        "cpu_pct": round(cpu_pct, 1),
        "load_1m": round(load_1m, 2),
        "cpu_count": cpu_count,
        "mem_used_gb": round(mem_used / (1024**3), 1),
        "mem_total_gb": round(mem_total / (1024**3), 1),
        "mem_pct": round(mem_pct, 1),
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "disk_pct": round(disk_pct, 1),
    }


def _parse_host_meminfo(proc_path: str) -> dict[str, int]:
    """Parse /proc/meminfo for total and available memory."""
    info: dict[str, int] = {}
    try:
        with open(f"{proc_path}/meminfo") as f:
            for line in f:
                parts = line.split()
                key = parts[0].rstrip(":")
                val = int(parts[1]) * 1024  # kB -> bytes
                if key == "MemTotal":
                    info["total"] = val
                elif key == "MemAvailable":
                    info["available"] = val
                if "total" in info and "available" in info:
                    break
    except Exception:
        vm = psutil.virtual_memory()
        info = {"total": vm.total, "available": vm.available}
    return info


async def get_quota_status(gateway: GatewayClient) -> list[dict[str, Any]]:
    """Fetch per-model quota usage from the gateway.

    Returns:
        List of dicts with model, used_rpd, limit_rpd, pct.
    """
    try:
        data = await gateway.models()
    except GatewayError:
        return []

    quotas: list[dict[str, Any]] = []
    for m in data.get("models", []):
        usage = m.get("usage", {})
        limit_rpd = usage.get("limit_rpd", 0)
        if not limit_rpd:
            continue
        used_rpd = usage.get("used_rpd", 0)
        pct = (used_rpd / limit_rpd * 100) if limit_rpd else 0
        quotas.append({
            "model": m["name"],
            "used_rpd": used_rpd,
            "limit_rpd": limit_rpd,
            "pct": round(pct, 1),
        })
    return quotas


async def get_gateway_latency(gateway: GatewayClient) -> float | None:
    """Measure gateway health endpoint round-trip as a proxy for responsiveness.

    Returns:
        Latency in milliseconds, or None on failure.
    """
    try:
        t0 = time.perf_counter()
        await gateway.health()
        return (time.perf_counter() - t0) * 1000
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Background Monitor
# ---------------------------------------------------------------------------

class SystemMonitor:
    """Periodic background monitor that sends proactive alerts."""

    def __init__(self, gateway: GatewayClient) -> None:
        self._gateway = gateway
        self._last_alert: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the background monitoring loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("System monitor started (interval=%ds)", _CHECK_INTERVAL)

    def stop(self) -> None:
        """Stop the background monitor."""
        if self._task and not self._task.done():
            self._task.cancel()

    def _should_alert(self, key: str) -> bool:
        """Check if enough time has passed since the last alert for this key."""
        now = time.monotonic()
        last = self._last_alert.get(key, 0)
        if now - last < _ALERT_COOLDOWN:
            return False
        self._last_alert[key] = now
        return True

    async def _loop(self) -> None:
        """Main monitoring loop."""
        # Wait a bit after startup before first check
        await asyncio.sleep(60)
        while True:
            try:
                await self._check_all()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("Monitor check error", exc_info=True)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _check_all(self) -> None:
        """Run all checks and send alerts if thresholds breached."""
        # VM resources
        vm = get_vm_resources()

        if vm["cpu_pct"] > _CPU_WARN_PCT and self._should_alert("cpu"):
            await send_telegram(
                f"🔴 CPU 使用率過高\n"
                f"Load: {vm['load_1m']} ({vm['cpu_count']} cores, {vm['cpu_pct']:.0f}%)"
            )

        if vm["mem_pct"] > _MEM_WARN_PCT and self._should_alert("mem"):
            await send_telegram(
                f"🔴 記憶體不足\n"
                f"RAM: {vm['mem_used_gb']}/{vm['mem_total_gb']} GB "
                f"({vm['mem_pct']:.0f}%)"
            )

        if vm["disk_pct"] > _DISK_WARN_PCT and self._should_alert("disk"):
            await send_telegram(
                f"🔴 磁碟空間不足\n"
                f"Disk: {vm['disk_used_gb']}/{vm['disk_total_gb']} GB "
                f"({vm['disk_pct']:.0f}%)"
            )

        # Provider quota
        quotas = await get_quota_status(self._gateway)
        for q in quotas:
            if q["pct"] >= _QUOTA_WARN_PCT:
                key = f"quota_{q['model']}"
                if self._should_alert(key):
                    await send_telegram(
                        f"💀 {q['model']} 配額即將耗盡\n"
                        f"RPD: {q['used_rpd']}/{q['limit_rpd']} ({q['pct']:.0f}%)"
                    )
