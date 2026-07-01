"""
GET/POST/PUT /api/collectors — collector registry management.

The collector_registry table maps inbound collector IPs to human names and
the org/log_group/site hierarchy used for enrichment at ingest time.
"""
from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.dependencies import CurrentUser, AdminUser

router = APIRouter()


class CollectorIn(BaseModel):
    collector_ip:   str
    collector_name: str
    org:            str = "Corp"
    log_group:      str = ""
    site:           str = ""
    notes:          str = ""
    enabled:        bool = True


class CollectorUpdate(BaseModel):
    collector_name: Optional[str] = None
    org:            Optional[str] = None
    log_group:      Optional[str] = None
    site:           Optional[str] = None
    notes:          Optional[str] = None
    enabled:        Optional[bool] = None


@router.get("/")
async def list_collectors(_: CurrentUser, db: aiosqlite.Connection = Depends(get_db)):
    """Return all collector registry entries ordered by collector_ip."""
    async with db.execute(
        """SELECT id, collector_ip, collector_name, org, log_group, site, notes, enabled,
                  created_at, updated_at
           FROM collector_registry
           ORDER BY collector_ip"""
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/", status_code=201)
async def create_collector(body: CollectorIn, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    """Add a new collector to the registry."""
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
            raise HTTPException(status_code=409, detail=f"Collector IP {body.collector_ip!r} already exists")
        raise HTTPException(status_code=500, detail=str(e))

    async with db.execute(
        "SELECT * FROM collector_registry WHERE collector_ip = ?", (body.collector_ip,)
    ) as cur:
        row = await cur.fetchone()
    return dict(row)


@router.put("/{collector_ip:path}")
async def update_collector(
    collector_ip: str,
    body: CollectorUpdate,
    _: AdminUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Update fields on an existing collector. Only provided fields are changed."""
    # Build dynamic SET clause
    fields = {}
    if body.collector_name is not None: fields["collector_name"] = body.collector_name
    if body.org            is not None: fields["org"]            = body.org
    if body.log_group      is not None: fields["log_group"]      = body.log_group
    if body.site           is not None: fields["site"]           = body.site
    if body.notes          is not None: fields["notes"]          = body.notes
    if body.enabled        is not None: fields["enabled"]        = int(body.enabled)

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields["updated_at"] = "datetime('now')"
    set_clause = ", ".join(
        f"{k} = datetime('now')" if v == "datetime('now')" else f"{k} = ?"
        for k, v in fields.items()
    )
    values = [v for v in fields.values() if v != "datetime('now')"]
    values.append(collector_ip)

    await db.execute(
        f"UPDATE collector_registry SET {set_clause} WHERE collector_ip = ?",
        values,
    )
    await db.commit()

    async with db.execute(
        "SELECT * FROM collector_registry WHERE collector_ip = ?", (collector_ip,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Collector {collector_ip!r} not found")
    return dict(row)


@router.delete("/{collector_ip:path}", status_code=204)
async def delete_collector(
    collector_ip: str,
    _: AdminUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Remove a collector from the registry."""
    await db.execute("DELETE FROM collector_registry WHERE collector_ip = ?", (collector_ip,))
    await db.commit()
