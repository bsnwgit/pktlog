"""
Collector approval — the queue of senders waiting to be let in.

pktLog drops syslog from any IP that isn't in collector_registry. Those
senders are accumulated by app.ingest.pending and surfaced here so an admin
can approve them from the Approval page, without going through Settings —
which is locked to read-only whenever pktHub manages this app, and would
otherwise force a managed install over to pktHub just to admit a device.

Every route is admin-only: approving a collector is what decides whether data
is stored at all.

Routes
------
GET    /api/approval/pending          — senders awaiting a decision
GET    /api/approval/count            — pending total, for the nav badge
POST   /api/approval/approve          — register a sender and clear it
POST   /api/approval/{ip}/ignore      — hide a sender from the queue
POST   /api/approval/{ip}/unignore    — put an ignored sender back
DELETE /api/approval/{ip}             — forget a sender entirely
"""
from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import AdminUser
from app.ingest import pending

router = APIRouter()


class ApprovalIn(BaseModel):
    """An approval decision — the registry fields for the sender being let in."""
    collector_ip:   str
    collector_name: str
    org:            str = ""
    log_group:      str = ""
    site:           str = ""
    notes:          str = ""
    enabled:        bool = True


@router.get("/pending")
async def list_pending(
    _: AdminUser,
    include_ignored: bool = False,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Senders seen at ingest that aren't registered, newest activity first."""
    where = "" if include_ignored else "WHERE ignored = 0"
    async with db.execute(
        f"""SELECT collector_ip, first_seen, last_seen, message_count,
                   sample_message, ignored
            FROM pending_collectors
            {where}
            ORDER BY last_seen DESC"""
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.get("/count")
async def pending_count(_: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    """Number of senders awaiting a decision — drives the nav badge."""
    async with db.execute(
        "SELECT COUNT(*) FROM pending_collectors WHERE ignored = 0"
    ) as cur:
        row = await cur.fetchone()
    return {"pending": row[0] if row else 0}


@router.post("/approve", status_code=201)
async def approve(body: ApprovalIn, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    """Register a pending sender, then clear it from the queue.

    The registry row is what actually admits the data; dropping the pending
    row only tidies the queue, so it happens second and never on its own.
    """
    try:
        await db.execute(
            """INSERT INTO collector_registry
               (collector_ip, collector_name, org, log_group, site, notes, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (body.collector_ip, body.collector_name, body.org,
             body.log_group, body.site, body.notes, int(body.enabled)),
        )
        await db.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"Collector IP {body.collector_ip!r} is already registered",
            )
        raise HTTPException(status_code=500, detail=str(e))

    await pending.clear(db, body.collector_ip)

    async with db.execute(
        "SELECT * FROM collector_registry WHERE collector_ip = ?", (body.collector_ip,)
    ) as cur:
        row = await cur.fetchone()
    return dict(row)


@router.post("/{collector_ip:path}/ignore")
async def ignore(collector_ip: str, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    """Hide a sender from the queue without registering it.

    Its counters keep rising and its messages keep being dropped — this only
    says "I've seen this and I'm not admitting it", so a chatty unwanted
    device stops burying real ones.
    """
    cur = await db.execute(
        "UPDATE pending_collectors SET ignored = 1 WHERE collector_ip = ?", (collector_ip,)
    )
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"No pending collector {collector_ip!r}")
    return {"ok": True, "collector_ip": collector_ip, "ignored": True}


@router.post("/{collector_ip:path}/unignore")
async def unignore(collector_ip: str, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    """Put a previously ignored sender back in the queue."""
    cur = await db.execute(
        "UPDATE pending_collectors SET ignored = 0 WHERE collector_ip = ?", (collector_ip,)
    )
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"No pending collector {collector_ip!r}")
    return {"ok": True, "collector_ip": collector_ip, "ignored": False}


@router.delete("/{collector_ip:path}", status_code=204)
async def forget(collector_ip: str, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    """Forget a sender. It reappears if it sends again — this isn't a block."""
    await pending.clear(db, collector_ip)
