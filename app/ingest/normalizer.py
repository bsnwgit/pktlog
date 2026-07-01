"""
Normalizer — enriches a SyslogRecord with hierarchy metadata from SQLite registries.

Lookups performed (all cached in-memory, refreshed every 5 minutes):
  collector_registry  → collector_name, org, log_group, site   (keyed by collector_ip)
  devices             → source_name, site                       (keyed by source_ip)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiosqlite

from app.config import get_settings
from app.models.syslog import SyslogRecord

log = logging.getLogger("pktlog.ingest.normalizer")

_CACHE_TTL = 300  # seconds


class Normalizer:

    def __init__(self):
        self._collector_cache: dict[str, dict] = {}
        self._device_cache: dict[str, dict] = {}
        self._last_refresh: float = 0.0
        self._lock = asyncio.Lock()

    # ── Public ────────────────────────────────────────────────────────────────

    async def enrich(self, record: SyslogRecord) -> SyslogRecord:
        """Mutates record in-place, returns it for chaining."""
        await self._maybe_refresh()

        # Collector → collector_name, org, log_group, site
        col = self._collector_cache.get(record.collector_ip)
        if col:
            record.collector_name = col["collector_name"]
            record.org            = col["org"]
            record.log_group      = col["log_group"]
            if not record.site:
                record.site = col["site"]
        else:
            # Unknown collector — use IP as name, leave hierarchy blank
            record.collector_name = record.collector_ip

        # Device registry → source_name, site (site from device takes priority)
        dev = self._device_cache.get(record.source_ip)
        if dev:
            record.source_name = dev["name"]
            if dev["site"]:
                record.site = dev["site"]

        return record

    # ── Cache refresh ─────────────────────────────────────────────────────────

    async def _maybe_refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_refresh < _CACHE_TTL:
            return
        async with self._lock:
            if now - self._last_refresh < _CACHE_TTL:
                return  # another coroutine already refreshed
            await self._refresh()
            self._last_refresh = time.monotonic()

    async def _refresh(self) -> None:
        cfg = get_settings()
        try:
            async with aiosqlite.connect(cfg.db_path) as db:
                db.row_factory = aiosqlite.Row

                # collector_registry
                try:
                    async with db.execute(
                        "SELECT collector_ip, collector_name, org, log_group, site "
                        "FROM collector_registry WHERE enabled = 1"
                    ) as cur:
                        rows = await cur.fetchall()
                    self._collector_cache = {
                        r["collector_ip"]: {
                            "collector_name": r["collector_name"],
                            "org":            r["org"],
                            "log_group":      r["log_group"],
                            "site":           r["site"],
                        }
                        for r in rows
                    }
                except Exception:
                    pass  # table may not exist yet on first startup

                # devices
                try:
                    async with db.execute(
                        "SELECT ip, name, site FROM devices WHERE allowed = 1"
                    ) as cur:
                        rows = await cur.fetchall()
                    self._device_cache = {
                        r["ip"]: {"name": r["name"], "site": r["site"]}
                        for r in rows
                    }
                except Exception:
                    pass

            log.debug(
                "Normalizer cache refreshed: %d collectors, %d devices",
                len(self._collector_cache),
                len(self._device_cache),
            )
        except Exception as e:
            log.warning("Normalizer cache refresh failed: %s", e)


# ── Module-level singleton ────────────────────────────────────────────────────

_normalizer: Optional[Normalizer] = None


def get_normalizer() -> Normalizer:
    global _normalizer
    if _normalizer is None:
        _normalizer = Normalizer()
    return _normalizer
