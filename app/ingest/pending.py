"""
Pending collector approvals — unregistered senders seen at ingest.

A sender that isn't in collector_registry has its messages dropped (see
normalizer.enrich). Without a record of that, the device is invisible to an
admin unless the "new_host" alert rule happens to be enabled — so the drop
path accumulates it here instead, and the Approval page reads the result.

Counters accumulate in memory on the ingest hot path and are flushed to SQLite
periodically (from the alert engine's tick). A device blasting syslog must not
turn into one write per message, which is the whole reason this isn't a
straight INSERT at the drop site.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

log = logging.getLogger("pktlog.ingest.pending")

# Raw lines can be up to 64 KB; a sample only has to be enough to recognise the
# device by, and this column is read by a list view.
_SAMPLE_MAX = 500

# ip -> {first_seen, last_seen, count, sample}. Mutated only from the ingest
# event loop and drained by flush(), both on the same loop, so no lock is
# needed — there is no await between any read and its matching write.
_seen: dict[str, dict] = {}


def record_unknown(collector_ip: str, raw: str) -> None:
    """Note a dropped message from an unregistered sender. Ingest hot path."""
    now = datetime.now(tz=timezone.utc).isoformat()
    entry = _seen.get(collector_ip)
    if entry is None:
        _seen[collector_ip] = {
            "first_seen": now,
            "last_seen":  now,
            "count":      1,
            "sample":     raw[:_SAMPLE_MAX],
        }
    else:
        entry["last_seen"] = now
        entry["count"] += 1
        entry["sample"] = raw[:_SAMPLE_MAX]


async def flush(db: aiosqlite.Connection) -> None:
    """Fold the in-memory counters into pending_collectors.

    Anything already in collector_registry is discarded rather than written —
    a *disabled* collector also lands on the drop path, and it is not awaiting
    approval, it was deliberately turned off.
    """
    if not _seen:
        return
    batch = dict(_seen)
    _seen.clear()

    for ip, e in batch.items():
        try:
            async with db.execute(
                "SELECT 1 FROM collector_registry WHERE collector_ip = ?", (ip,)
            ) as cur:
                if await cur.fetchone():
                    continue

            await db.execute(
                """
                INSERT INTO pending_collectors
                    (collector_ip, first_seen, last_seen, message_count, sample_message)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(collector_ip) DO UPDATE SET
                    last_seen      = excluded.last_seen,
                    message_count  = message_count + excluded.message_count,
                    sample_message = excluded.sample_message
                """,
                (ip, e["first_seen"], e["last_seen"], e["count"], e["sample"]),
            )
        except Exception as exc:
            log.warning("Could not record pending collector %s: %s", ip, exc)
    await db.commit()


async def clear(db: aiosqlite.Connection, collector_ip: str) -> None:
    """Drop a pending row — called once its collector_registry entry exists."""
    _seen.pop(collector_ip, None)
    await db.execute("DELETE FROM pending_collectors WHERE collector_ip = ?", (collector_ip,))
    await db.commit()
