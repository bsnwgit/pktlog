"""
GET/POST/PUT/DELETE /api/alerts — alert rule and event management.

alert_rules are evaluated on a schedule by app.alerts.engine.AlertEngine;
this router is purely CRUD + read access for the UI. Rule mutation
(create/update/delete/toggle) requires admin; acknowledging events only
requires analyst-or-above; everything else just requires a logged-in user.
"""
from __future__ import annotations

import json
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import AdminUser, AnalystUser, CurrentUser

router = APIRouter()


class AlertRuleIn(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    rule_type: str
    conditions: dict[str, Any] = {}
    time_window_min: int = 5
    severity: str = "warning"
    channels: list[str] = ["inapp"]
    cooldown_min: int = 30


def _rule_to_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    d["conditions"] = json.loads(d["conditions"]) if d["conditions"] else {}
    d["channels"] = json.loads(d["channels"]) if d["channels"] else []
    return d


def _event_to_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["details"] = json.loads(d["details"]) if d["details"] else {}
    return d


# ── Rules ────────────────────────────────────────────────────────────────────

@router.get("/rules")
async def list_rules(_: CurrentUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT * FROM alert_rules ORDER BY name") as cur:
        rows = await cur.fetchall()
    return [_rule_to_dict(r) for r in rows]


@router.post("/rules", status_code=201)
async def create_rule(body: AlertRuleIn, user: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        """INSERT INTO alert_rules
           (name, description, enabled, rule_type, conditions, time_window_min,
            severity, channels, cooldown_min, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING id""",
        (body.name, body.description, int(body.enabled), body.rule_type,
         json.dumps(body.conditions), body.time_window_min, body.severity,
         json.dumps(body.channels), body.cooldown_min, user["id"]),
    ) as cur:
        new_id = (await cur.fetchone())[0]
    await db.commit()

    async with db.execute("SELECT * FROM alert_rules WHERE id = ?", (new_id,)) as cur:
        row = await cur.fetchone()
    return _rule_to_dict(row)


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: int, body: AlertRuleIn, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)
):
    await db.execute(
        """UPDATE alert_rules SET
             name = ?, description = ?, enabled = ?, rule_type = ?, conditions = ?,
             time_window_min = ?, severity = ?, channels = ?, cooldown_min = ?,
             updated_at = datetime('now')
           WHERE id = ?""",
        (body.name, body.description, int(body.enabled), body.rule_type,
         json.dumps(body.conditions), body.time_window_min, body.severity,
         json.dumps(body.channels), body.cooldown_min, rule_id),
    )
    await db.commit()

    async with db.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _rule_to_dict(row)


@router.patch("/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: int, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT enabled FROM alert_rules WHERE id = ?", (rule_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    new_enabled = 0 if row[0] else 1
    await db.execute(
        "UPDATE alert_rules SET enabled = ?, updated_at = datetime('now') WHERE id = ?",
        (new_enabled, rule_id),
    )
    await db.commit()
    return {"id": rule_id, "enabled": bool(new_enabled)}


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute(
        "DELETE FROM notification_log WHERE event_id IN (SELECT id FROM alert_events WHERE rule_id = ?)",
        (rule_id,),
    )
    await db.execute("DELETE FROM alert_events WHERE rule_id = ?", (rule_id,))
    await db.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
    await db.commit()


# ── Events ───────────────────────────────────────────────────────────────────

@router.get("/events")
async def list_events(
    _: CurrentUser,
    unacked_only: bool = Query(False),
    db: aiosqlite.Connection = Depends(get_db),
):
    q = (
        "SELECT e.*, r.name AS rule_name FROM alert_events e "
        "JOIN alert_rules r ON r.id = e.rule_id"
    )
    if unacked_only:
        q += " WHERE e.acked_at IS NULL"
    q += " ORDER BY e.fired_at DESC LIMIT 500"

    async with db.execute(q) as cur:
        rows = await cur.fetchall()
    return [_event_to_dict(r) for r in rows]


@router.post("/events/{event_id}/ack")
async def ack_event(event_id: int, user: AnalystUser, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute(
        "UPDATE alert_events SET acked_at = datetime('now'), acked_by = ? "
        "WHERE id = ? AND acked_at IS NULL",
        (user["id"], event_id),
    )
    await db.commit()
    return {"status": "ok"}


@router.post("/events/ack-all")
async def ack_all_events(user: AnalystUser, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute(
        "UPDATE alert_events SET acked_at = datetime('now'), acked_by = ? WHERE acked_at IS NULL",
        (user["id"],),
    )
    await db.commit()
    return {"status": "ok"}
