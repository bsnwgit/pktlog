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
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
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
    # fired_at/acked_at/resolved_at come from SQLite's datetime('now'), which
    # is UTC but has no timezone marker ("2026-07-15 19:29:31") — browsers
    # parse that as local time, not UTC. Normalize to a proper UTC ISO string.
    for col in ("fired_at", "acked_at", "resolved_at"):
        if d.get(col):
            d[col] = d[col].replace(" ", "T") + "Z"
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


@router.get("/rules/export")
async def export_rules(_: CurrentUser, db: aiosqlite.Connection = Depends(get_db)):
    """Export all alert rules as a CSV file download."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    async with db.execute(
        """SELECT name, description, rule_type, conditions, time_window_min,
                  severity, channels, cooldown_min, enabled
           FROM alert_rules ORDER BY id"""
    ) as cur:
        rows = await cur.fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "name", "description", "rule_type", "conditions", "time_window_min",
        "severity", "channels", "cooldown_min", "enabled",
    ])
    for r in rows:
        try:
            channels = ",".join(json.loads(r["channels"]) if r["channels"] else ["inapp"])
        except Exception:
            channels = "inapp"
        writer.writerow([
            r["name"], r["description"] or "", r["rule_type"],
            r["conditions"] or "{}", r["time_window_min"],
            r["severity"], channels, r["cooldown_min"],
            "true" if r["enabled"] else "false",
        ])
    buf.seek(0)
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="pktlog-alert-rules.csv"'},
    )


@router.post("/rules/import-csv")
async def import_rules_csv(
    file: UploadFile,
    user: AdminUser,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Import alert rules from a multipart CSV upload.

    CSV columns (header row required):
      name, description, rule_type, conditions, time_window_min, severity,
      channels, cooldown_min, enabled

    - conditions: a JSON object string, e.g. '{"collector_ip": "10.0.1.5"}' —
      the exact shape depends on rule_type, same as what the UI builds.
      Blank or invalid JSON is treated as {}.
    - channels: comma-separated, e.g. "inapp,slack". Blank defaults to "inapp".
    - Rows are always inserted as new rules (no de-dup by name).
    """
    import csv, io

    raw = await file.read()
    text = raw.decode("utf-8-sig")  # strip BOM (Excel exports)

    reader = csv.DictReader(io.StringIO(text))
    created = 0
    skipped = 0
    errors: list[str] = []

    for lineno, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        rule_type = (row.get("rule_type") or "").strip()
        if not name or not rule_type:
            errors.append(f"Row {lineno}: missing name or rule_type — skipped")
            skipped += 1
            continue

        conditions_raw = (row.get("conditions") or "").strip()
        try:
            conditions = json.loads(conditions_raw) if conditions_raw else {}
            if not isinstance(conditions, dict):
                raise ValueError("conditions must be a JSON object")
        except Exception as exc:
            errors.append(f"Row {lineno}: {name}: invalid conditions JSON ({exc}) — skipped")
            skipped += 1
            continue

        channels_raw = (row.get("channels") or "inapp").strip()
        channels = [c.strip() for c in channels_raw.split(",") if c.strip()] or ["inapp"]

        time_window_str = (row.get("time_window_min") or "5").strip()
        time_window_min = int(time_window_str) if time_window_str.lstrip("-").isdigit() else 5

        cooldown_str = (row.get("cooldown_min") or "30").strip()
        cooldown_min = int(cooldown_str) if cooldown_str.lstrip("-").isdigit() else 30

        enabled_str = (row.get("enabled") or "true").strip().lower()
        enabled = enabled_str not in ("false", "0", "no")

        try:
            await db.execute(
                """INSERT INTO alert_rules
                    (name, description, enabled, rule_type, conditions, time_window_min,
                     severity, channels, cooldown_min, created_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    name, (row.get("description") or "").strip(), int(enabled), rule_type,
                    json.dumps(conditions), time_window_min,
                    (row.get("severity") or "warning").strip(), json.dumps(channels),
                    cooldown_min, user["id"],
                ),
            )
            created += 1
        except Exception as exc:
            errors.append(f"Row {lineno}: {name}: {exc}")
            skipped += 1

    await db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


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
