"""Tests for monitor CPU measurement — busy % must not be derived from load."""

from __future__ import annotations

import os

import pytest

from echeneis.bot import monitor
from echeneis.bot.monitor import cpu_usage, get_vm_resources


def test_iowait_counts_as_idle():
    # 100s elapsed: 80 idle, 15 iowait, 5 busy.
    busy, iowait = cpu_usage((1000.0, 500.0, 100.0), (1100.0, 580.0, 115.0))

    assert busy == pytest.approx(5.0)
    assert iowait == pytest.approx(15.0)


def test_no_elapsed_time_reports_zero():
    assert cpu_usage((10.0, 5.0, 1.0), (10.0, 5.0, 1.0)) == (0.0, 0.0)


async def test_high_load_on_idle_cpu_is_not_reported_as_busy(monkeypatch, tmp_path):
    # Production 2026-10-16: load 7.52 on 4 cores from tasks blocked on disk
    # reads, while the CPU was ~97% idle. The old load-based figure was 188%.
    samples = iter([(0.0, 0.0, 0.0), (400.0, 340.0, 50.0)])
    monkeypatch.setattr(monitor, "_cpu_times", lambda: next(samples))
    monkeypatch.setattr(monitor, "_get_host_proc", lambda: None)
    monkeypatch.setattr(os, "getloadavg", lambda: (7.52, 7.2, 6.6), raising=False)
    monkeypatch.setattr(monitor.psutil, "cpu_count", lambda: 4)
    monkeypatch.setenv("ECHENEIS_STATE_DIR", str(tmp_path))

    vm = await get_vm_resources(sample_s=0)

    assert vm["cpu_pct"] == pytest.approx(2.5)
    assert vm["iowait_pct"] == pytest.approx(12.5)
    assert vm["load_1m"] == 7.52
    assert vm["cpu_count"] == 4
