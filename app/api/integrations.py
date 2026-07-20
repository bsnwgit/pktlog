"""
/api/integrations/* — named connections to sibling pkt* apps pktlog could
pull data from. No consumer feature reads from this table yet — it exists
so a connection can be configured ahead of any future integration being
wired up, same pattern pktIPAM/pktflow/pktWiFi use for their own sibling
connections. Separate from /api/suite/*, which is the INBOUND side
(pktHub, or another sibling app, calling into pktlog).
"""
from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import CurrentUser, AdminUser
from app.integrations.suite_client import SuiteClient

router = APIRouter()

_APPS = ("pktipam", "pktflow", "pktsnmp", "pktpcap", "pktwifi", "pkthub")


class IntegrationCreate(BaseModel):
    name: str
    app_name: str
    base_url: str
    suite_token: str
    enabled: bool = True


class IntegrationUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    suite_token: str | None = None
    enabled: bool | None = None


def _out(r) -> dict:
    return {
        "id": r["id"], "name": r["name"], "app_name": r["app_name"], "base_url": r["base_url"],
        "has_token": bool(r["suite_token"]), "enabled": bool(r["enabled"]),
        "health_status": r["health_status"], "last_health_check": r["last_health_check"],
    }


@router.get("")
async def list_integrations(user: CurrentUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT * FROM integrations ORDER BY name") as cur:
        rows = await cur.fetchall()
    return [_out(r) for r in rows]


@router.post("", status_code=201)
async def create_integration(body: IntegrationCreate, user: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    if body.app_name not in _APPS:
        raise HTTPException(status_code=400, detail=f"app_name must be one of {_APPS}")
    try:
        cur = await db.execute(
            """INSERT INTO integrations (name, app_name, base_url, suite_token, enabled)
               VALUES (?, ?, ?, ?, ?) RETURNING *""",
            (body.name, body.app_name, body.base_url.rstrip("/"), body.suite_token, int(body.enabled)),
        )
        row = await cur.fetchone()
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail=f"An integration named '{body.name}' already exists")
    return _out(row)


@router.put("/{integration_id}")
async def update_integration(integration_id: int, body: IntegrationUpdate, user: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT * FROM integrations WHERE id = ?", (integration_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    existing = dict(row)
    updates = body.model_dump(exclude_none=True)
    if "base_url" in updates:
        updates["base_url"] = updates["base_url"].rstrip("/")
    existing.update(updates)
    try:
        await db.execute(
            """UPDATE integrations SET name = ?, base_url = ?, suite_token = ?, enabled = ?,
               updated_at = datetime('now') WHERE id = ?""",
            (existing["name"], existing["base_url"], existing["suite_token"], int(existing["enabled"]), integration_id),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail=f"An integration named '{existing['name']}' already exists")
    async with db.execute("SELECT * FROM integrations WHERE id = ?", (integration_id,)) as cur:
        row = await cur.fetchone()
    return _out(row)


@router.delete("/{integration_id}", status_code=204)
async def delete_integration(integration_id: int, user: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute("DELETE FROM integrations WHERE id = ?", (integration_id,))
    await db.commit()


@router.post("/{integration_id}/test")
async def test_integration(integration_id: int, user: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT * FROM integrations WHERE id = ?", (integration_id,)) as cur:
        row = await cur.fetchone()
    if not row or not row["base_url"]:
        raise HTTPException(status_code=400, detail="Integration is not configured yet")

    client = SuiteClient(row["base_url"], row["suite_token"], suite_user="pktlog", suite_role="admin")
    healthy, detail = await client.health_check()
    status_str = "ok" if healthy else "error"
    await db.execute(
        "UPDATE integrations SET health_status = ?, last_health_check = datetime('now') WHERE id = ?",
        (status_str, integration_id),
    )
    await db.commit()
    return {"healthy": healthy, "detail": detail}
