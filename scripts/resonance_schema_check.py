#!/usr/bin/env python3
"""
Check that what each resonance operation really returns matches what it declares.

The contract checker (resonance_contract_check.py) reads the published document
and asks whether it is well formed. This asks the harder question: is it true.
Those are different failures. A spec can be immaculate — every enum, every
limit, every description — and still declare a field as an integer that the
store holds as a string, and the only symptom is a 500 the moment somebody asks
a real question.

That is not hypothetical. `pid` is a String in ClickHouse and a VARCHAR in
DuckDB, empty rather than null when a log line carries no process id. Declared
as an integer, every single search failed response validation on its first
record and the panel said only "an internal error".

Run this on the install, against its own store. Shapes differ by backend, so a
pass on ClickHouse is not a pass on DuckDB, and neither is a pass on a spec
read over HTTP.

Usage, from the install directory:
    venv/bin/python scripts/resonance_schema_check.py

Exit status is 0 when every operation's real output validates.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _summarise(exc: Exception) -> list[str]:
    """Collapse pydantic's per-row repetition into one line per field."""
    counts: dict[str, int] = {}
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return [str(exc)[:200]]
    for err in exc.errors():
        loc = err.get("loc", ())
        field = ".".join(str(p) for p in loc if not isinstance(p, int)) or "?"
        key = f"{field}: {err.get('msg', '')} (saw {err.get('input', '')!r})"
        counts[key] = counts.get(key, 0) + 1
    return [f"x{n:<4} {k}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]


async def main() -> int:
    from app.config import get_settings
    from app.storage.factory import init_storage, get_storage
    import aiosqlite
    import app.api.resonance_data as rd
    from app.api.alerts import _event_to_dict, _rule_to_dict
    from app.api.syslog import build_summary

    cfg = get_settings()
    await init_storage()
    store = get_storage()
    print(f"store: {store.__class__.__name__}\n")

    failures: list[str] = []

    def verify(name: str, model, payload) -> None:
        try:
            model.model_validate(payload)
        except Exception as exc:
            failures.append(name)
            print(f"[FAIL] {name}")
            for line in _summarise(exc)[:10]:
                print(f"         {line}")
            return
        print(f"[ok  ] {name}")

    # Ask for a full page of each: a shape problem often lives in the one row
    # that has an empty field, not in the first row returned.
    limit = rd._SEARCH_MAX

    verify("searchSyslog", rd.SyslogSearchResult,
           rd._fit(dict(await store.search(limit=limit, offset=0)), "records"))

    buckets = await store.timeseries(hours=24, bucket_minutes=60)
    verify("getSyslogTimeline", rd.SyslogTimeline,
           {"hours": 24, "bucket_minutes": 60, "points": len(buckets), "buckets": buckets})

    async with aiosqlite.connect(cfg.db_path) as db:
        db.row_factory = aiosqlite.Row

        summary = await build_summary(db, 24)
        collectors = list(summary.get("collector_last_seen") or [])
        summary["collector_total"] = len(collectors)
        summary["collector_last_seen"] = collectors[:rd._LIST_MAX]
        verify("getSyslogSummary", rd.SyslogSummary, summary)

        async with db.execute(
            """SELECT id, collector_ip, collector_name, org, log_group, site, notes, enabled,
                      created_at, updated_at FROM collector_registry LIMIT ?""", (limit,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            row["enabled"] = bool(row["enabled"])
        verify("listCollectors", rd.CollectorList,
               rd._fit({"total": len(rows), "limit": limit, "offset": 0, "collectors": rows}, "collectors"))

        async with db.execute(
            """SELECT collector_ip, first_seen, last_seen, message_count, sample_message, ignored
               FROM pending_collectors LIMIT ?""", (limit,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            row["ignored"] = bool(row["ignored"])
        verify("listPendingCollectors", rd.PendingCollectorList,
               rd._fit({"total": len(rows), "limit": limit, "offset": 0, "pending": rows}, "pending"))

        async with db.execute(
            """SELECT e.*, r.name AS rule_name FROM alert_events e
               JOIN alert_rules r ON r.id = e.rule_id LIMIT ?""", (limit,)) as cur:
            rows = [_event_to_dict(r) for r in await cur.fetchall()]
        for row in rows:
            row["auto_resolved"] = bool(row.get("auto_resolved"))
        verify("listAlertEvents", rd.AlertEventList,
               rd._fit({"total": len(rows), "limit": limit, "offset": 0, "events": rows}, "events"))

        async with db.execute("SELECT * FROM alert_rules LIMIT ?", (limit,)) as cur:
            rows = [_rule_to_dict(r) for r in await cur.fetchall()]
        verify("listAlertRules", rd.AlertRuleList,
               rd._fit({"total": len(rows), "limit": limit, "offset": 0, "rules": rows}, "rules"))

        async with db.execute(
            "SELECT id, ts, level, level_no, logger, message, exc_info FROM app_logs LIMIT ?",
            (limit,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        verify("searchApplicationLog", rd.AppLogResult,
               rd._fit({"total": len(rows), "limit": limit, "offset": 0, "records": rows}, "records"))

    print()
    if failures:
        print(f"{len(failures)} operation(s) declare a shape their data does not have: "
              f"{', '.join(failures)}")
        print("Each one answers 500 to the assistant. Fix the model in app/api/resonance_data.py.")
        return 1
    print("every operation's real output matches its declared schema")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
