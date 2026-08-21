"""
Syslog API — read-only endpoints for querying syslog data from ClickHouse.

Routes
------
GET /api/syslog/search      — filtered log search with pagination
GET /api/syslog/stats       — dashboard summary (severity counts, top hosts, top programs, collector status)
GET /api/syslog/timeseries  — time-bucketed event counts for charts
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import get_db
from app.dependencies import CurrentUser
from app.storage.factory import get_storage

router = APIRouter()


# ── /search ───────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_logs(
    _: CurrentUser,
    # Time range
    start:          Optional[datetime] = Query(None, description="ISO 8601 start time"),
    end:            Optional[datetime] = Query(None, description="ISO 8601 end time"),
    # Source filters
    source_ip:      Optional[str]  = Query(None),
    dest_ip:        Optional[str]  = Query(None),
    collector_ip:   Optional[str]  = Query(None),
    collector_name: Optional[str]  = Query(None),
    # Hierarchy filters
    org:            Optional[str]  = Query(None),
    log_group:      Optional[str]  = Query(None),
    site:           Optional[str]  = Query(None),
    # Syslog filters
    severity_max:   Optional[int]  = Query(None, ge=0, le=7, description="Max severity (0=emergency … 7=debug)"),
    facility:       Optional[int]  = Query(None, ge=0, le=23),
    program:        Optional[str]  = Query(None),
    # Text search
    q:              Optional[str]  = Query(None, description="Full-text search in message field"),
    # Matching mode for the field filters above (source_ip, dest_ip, collector_ip,
    # collector_name, program). "contains" is the default; "prefix" is what the
    # Explorer's "Exact" checkbox sends. Both are case-insensitive. `q` is always
    # "contains" — anchoring a message-body search to the start of the line
    # would never match anything useful.
    match_mode:     str = Query("contains", pattern="^(contains|prefix)$"),
    # Pagination
    limit:          int = Query(200, ge=1, le=1000),
    offset:         int = Query(0, ge=0),
):
    """
    Filtered syslog search.

    Field filters match case-insensitively — anywhere in the value by default,
    or anchored at the first character with `match_mode=prefix`.

    Returns `{total, limit, offset, records[]}` where each record maps
    directly to a ClickHouse syslog_events row.
    """
    try:
        return await get_storage().search(
            start=start,
            end=end,
            source_ip=source_ip,
            dest_ip=dest_ip,
            collector_ip=collector_ip,
            collector_name=collector_name,
            org=org,
            log_group=log_group,
            site=site,
            severity_max=severity_max,
            facility=facility,
            program=program,
            search=q,
            match_mode=match_mode,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    _: CurrentUser,
    hours: int = Query(24, ge=1, le=720, description="Lookback window in hours (max 30 days)"),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Dashboard summary stats for the last N hours.

    Returns:
    ```json
    {
      "hours": 24,
      "count_by_severity": [{"severity": 6, "severity_name": "info", "count": 1234}, ...],
      "top_hosts":          [{"source_ip": "...", "source_name": "...", "count": 42}, ...],
      "top_programs":       [{"program": "...", "count": 99}, ...],
      "collector_last_seen":[{"collector_ip": "...", "collector_name": "...", "last_seen": "..."}, ...]
    }
    ```
    """
    try:
        storage = get_storage()
        count_by_severity, top_hosts, top_programs, ch_last_seen = (
            await storage.count_by_severity(hours=hours),
            await storage.count_by_host(hours=hours),
            await storage.top_programs(hours=hours),
            await storage.collector_last_seen(),
        )

        # Build last_seen lookup from ClickHouse data keyed by collector_ip.
        # A collector can appear under multiple collector_names (e.g. its raw
        # IP before registration, then its assigned name after) — ch_last_seen
        # is ordered newest-first, so keep only the first (most recent) row
        # per IP rather than letting a later, staler row for the same IP win.
        ch_map: dict[str, dict] = {}
        for r in ch_last_seen:
            ch_map.setdefault(r["collector_ip"], r)

        # Load all registered collectors from SQLite registry
        async with db.execute(
            "SELECT collector_ip, collector_name FROM collector_registry WHERE enabled = 1 ORDER BY collector_ip"
        ) as cur:
            registry_rows = await cur.fetchall()

        # Registry (enabled collectors only) is the source of truth for which
        # collectors are shown here; last_seen comes from ClickHouse if available,
        # else null. Unregistered/disabled collectors are intentionally excluded —
        # they surface via the "Unknown collector" alert instead.
        collector_last_seen: list[dict] = []
        for row in registry_rows:
            ip, name = row[0], row[1]
            ch = ch_map.get(ip)
            collector_last_seen.append({
                "collector_ip":   ip,
                "collector_name": name or ip,
                "last_seen":      ch["last_seen"] if ch else None,
            })

        return {
            "hours": hours,
            "count_by_severity":   count_by_severity,
            "top_hosts":           top_hosts,
            "top_programs":        top_programs,
            "collector_last_seen": collector_last_seen,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /timeseries ───────────────────────────────────────────────────────────────

@router.get("/timeseries")
async def get_timeseries(
    _: CurrentUser,
    hours:          int          = Query(24, ge=1, le=720, description="Lookback window in hours"),
    bucket_minutes: int          = Query(5,  ge=1, le=1440, description="Bucket size in minutes"),
    log_group:      Optional[str] = Query(None, description="Filter to a single log group"),
):
    """
    Event counts bucketed by time interval, suitable for a line/bar chart.

    Returns `[{"bucket": "<ISO datetime>", "count": <int>}, ...]` ordered ascending by time.
    """
    try:
        return await get_storage().timeseries(
            hours=hours,
            bucket_minutes=bucket_minutes,
            log_group=log_group,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
