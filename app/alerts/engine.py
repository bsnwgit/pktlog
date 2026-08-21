"""
Alert engine — runs on a schedule, evaluates all enabled alert rules,
fires AlertEvents, and dispatches notifications.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiosqlite

from app.config import get_settings

log = logging.getLogger("pktlog.alerts")
settings = get_settings()

_unknown_sampler_queue: list[str] = []


class AlertEngine:
    _instance: "Optional[AlertEngine]" = None

    def __init__(self, interval_seconds: int = 60):
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        AlertEngine._instance = self
        self._task = asyncio.create_task(self._run_loop())
        log.info(f"Alert engine started (interval={self._interval}s)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        while True:
            try:
                await self._evaluate_all()
            except Exception as e:
                log.error(f"Alert engine evaluation error: {e}")
            await asyncio.sleep(self._interval)

    async def _evaluate_all(self) -> None:
        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Fetch enabled rules
            async with db.execute(
                "SELECT * FROM alert_rules WHERE enabled = 1"
            ) as cur:
                rules = [dict(r) for r in await cur.fetchall()]

            for rule in rules:
                try:
                    rule["conditions"] = json.loads(rule["conditions"])
                    rule["channels"] = json.loads(rule["channels"])
                    await self._evaluate_rule(db, rule)
                except Exception as e:
                    log.warning(f"Error evaluating rule '{rule['name']}': {e}")

            # Process unknown sampler queue
            await self._check_unknown_samplers(db)

            # Fold ingest's unregistered-sender counters into pending_collectors
            # for the Approval page. Batched here rather than written per
            # dropped message; this loop is the app's one always-on tick.
            from app.ingest import pending
            try:
                await pending.flush(db)
            except Exception as e:
                log.warning(f"Could not flush pending collectors: {e}")

    async def _evaluate_rule(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage

        rule_type = rule["rule_type"]
        fired_message: Optional[str] = None
        details: dict = {}

        if rule_type == "data_gap":
            silence_min = rule["conditions"].get("silence_minutes", 10)
            collectors = await get_storage().collector_last_seen()
            last_seen = {
                c["collector_ip"]: datetime.fromisoformat(c["last_seen"])
                for c in collectors
            }
            now = datetime.now(tz=timezone.utc)

            # Load dismissed collector IPs — these should never trigger gap alerts
            async with db.execute("SELECT sampler_ip FROM sampler_dismissals") as cur:
                dismissed = {r[0] for r in await cur.fetchall()}
            dismissed.add("0.0.0.0")  # always ignore the null address

            # Fire for any collector that has gone silent (and is not dismissed).
            # Collectors that have never sent any data at all don't appear in
            # last_seen (collector_last_seen() only returns collectors with at
            # least one row in syslog_events), so they're not flagged here.
            gapped_samplers: set[str] = set()
            for collector_ip, ts in last_seen.items():
                if collector_ip in dismissed:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if (now - ts) > timedelta(minutes=silence_min):
                    gapped_samplers.add(collector_ip)
                    fired_message = f"No syslog messages from {collector_ip} for >{silence_min} minutes (last seen: {ts.isoformat()})"
                    details = {"sampler_ip": collector_ip, "last_seen": ts.isoformat()}
                    await self._fire(db, rule, fired_message, details)

            # Auto-resolve open events whose collector has recovered
            await self._auto_resolve_data_gap(db, rule, gapped_samplers, last_seen, silence_min)
            return  # handled per-collector above

        elif rule_type == "new_host":
            return  # handled via notify_unknown_sampler()

        elif rule_type == "threshold":
            await self._evaluate_threshold(db, rule)

        elif rule_type == "rate_spike":
            await self._evaluate_rate_spike(db, rule)

        elif rule_type == "top_talker":
            await self._evaluate_top_talker(db, rule)

        elif rule_type == "ingest_rate_low":
            await self._evaluate_ingest_rate_low(db, rule)

        elif rule_type == "clickhouse_size":
            await self._evaluate_clickhouse_size(db, rule)

    async def _evaluate_threshold(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        operator = conds.get("operator", "gt")
        threshold = float(conds.get("value", 0))
        collector_ip = conds.get("collector_ip") or None
        severity_max = conds.get("severity_max")
        program = conds.get("program") or None
        window_min = rule.get("time_window_min", 5)

        storage = get_storage()
        current = await storage.count_events_in_window(window_min, collector_ip, severity_max, program)
        ops = {"gt": current > threshold, "gte": current >= threshold,
               "lt": current < threshold, "lte": current <= threshold}
        if ops.get(operator, False):
            op_sym = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}.get(operator, operator)
            collector_str = f" from {collector_ip}" if collector_ip else ""
            top_sources = await storage.top_sources_in_window(window_min, collector_ip, severity_max, program)
            await self._fire(db, rule, (
                f"Threshold breached{collector_str}: {current:,} syslog events in last {window_min}m "
                f"{op_sym} {threshold:,.0f}"
            ), {"current": current, "threshold": threshold, "operator": operator,
                "sampler_ip": collector_ip or "all", "top_sources": top_sources})
        else:
            await self._auto_resolve(db, rule, f"Event count back within threshold (current: {current:,})")

    async def _evaluate_rate_spike(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        multiplier = float(conds.get("multiplier", 3.0))
        baseline_days = int(conds.get("baseline_days", 7))
        collector_ip = conds.get("collector_ip") or None
        severity_max = conds.get("severity_max")
        program = conds.get("program") or None
        window_min = rule.get("time_window_min", 5)

        storage = get_storage()
        current = await storage.count_events_in_window(window_min, collector_ip, severity_max, program)
        baseline = await storage.count_events_baseline(baseline_days, window_min, collector_ip, severity_max, program)

        if baseline > 0 and current >= baseline * multiplier:
            ratio = current / baseline
            collector_str = f" from {collector_ip}" if collector_ip else ""
            top_sources = await storage.top_sources_in_window(window_min, collector_ip, severity_max, program)
            await self._fire(db, rule, (
                f"Rate spike detected{collector_str}: {current:,} events in last {window_min}m "
                f"({ratio:.1f}× the {baseline_days}-day baseline of {baseline:,.0f})"
            ), {"current": current, "baseline": baseline, "ratio": round(ratio, 2),
                "multiplier": multiplier, "sampler_ip": collector_ip or "all",
                "top_sources": top_sources})
        else:
            await self._auto_resolve(
                db, rule, f"Event rate back to normal (current: {current:,}, baseline: {baseline:,.0f})"
            )

    async def _evaluate_top_talker(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        threshold = float(conds.get("threshold", 0))
        collector_ip = conds.get("collector_ip") or None
        severity_max = conds.get("severity_max")
        window_min = rule.get("time_window_min", 5)

        top_ip, count = await get_storage().top_talker_in_window(window_min, collector_ip, severity_max)
        if not top_ip:
            await self._auto_resolve(db, rule, "No events in window")
            return

        if count >= threshold:
            collector_str = f" on {collector_ip}" if collector_ip else ""
            await self._fire(db, rule, (
                f"Top talker{collector_str}: {top_ip} sent {count:,} events "
                f"in last {window_min}m (threshold: {threshold:,.0f})"
            ), {"src_ip": top_ip, "value": count, "threshold": threshold,
                "sampler_ip": collector_ip or "all"})
        else:
            await self._auto_resolve(db, rule, f"Top talker {top_ip}: {count:,} events — within threshold")

    async def _evaluate_ingest_rate_low(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        min_eps = float(conds.get("min_events_per_sec", 1.0))
        collector_ip = conds.get("collector_ip") or None
        window_min = rule.get("time_window_min", 5)

        total = await get_storage().count_events_in_window(window_min, collector_ip)
        eps = total / (window_min * 60) if window_min > 0 else 0.0

        if eps < min_eps:
            collector_str = f" from {collector_ip}" if collector_ip else ""
            await self._fire(db, rule, (
                f"Ingest rate low{collector_str}: {eps:.2f} events/sec in last {window_min}m "
                f"(minimum: {min_eps:.2f} events/sec)"
            ), {"events_per_sec": round(eps, 3), "min_events_per_sec": min_eps,
                "total_events": int(total), "sampler_ip": collector_ip or "all"})
        else:
            await self._auto_resolve(db, rule, f"Ingest rate normal: {eps:.2f} events/sec — above minimum")

    async def _evaluate_clickhouse_size(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        threshold_gb = float(conds.get("threshold_gb", 10.0))
        table = conds.get("table", "syslog_events")

        size_gb = await get_storage().table_size_gb(table)
        if size_gb <= 0:
            return  # DuckDB backend or table empty — not applicable

        if size_gb >= threshold_gb:
            await self._fire(db, rule, (
                f"ClickHouse storage: '{table}' table is {size_gb:.1f}GB "
                f"(threshold: {threshold_gb:.1f}GB)"
            ), {"table": table, "size_gb": round(size_gb, 2), "threshold_gb": threshold_gb})
        else:
            await self._auto_resolve(db, rule, f"ClickHouse '{table}': {size_gb:.2f}GB — within limit")


    async def _auto_resolve(self, db: aiosqlite.Connection, rule: dict, reason: str) -> None:
        """Mark open events for this rule as auto-resolved when the condition has cleared."""
        async with db.execute(
            "SELECT id FROM alert_events WHERE rule_id = ? AND acked_at IS NULL AND resolved_at IS NULL",
            (rule["id"],),
        ) as cur:
            open_ids = [r[0] for r in await cur.fetchall()]
        if not open_ids:
            return
        for eid in open_ids:
            await db.execute(
                "UPDATE alert_events SET resolved_at = datetime('now'), auto_resolved = 1 WHERE id = ?",
                (eid,),
            )
        await db.commit()
        log.info(
            f"Auto-resolved {len(open_ids)} event(s) for rule '{rule['name']}': {reason}"
        )

    async def _auto_resolve_data_gap(
        self,
        db: aiosqlite.Connection,
        rule: dict,
        gapped_samplers: set,
        last_seen: dict,
        silence_min: int,
    ) -> None:
        """Auto-resolve data_gap events whose sampler has recovered."""
        async with db.execute(
            "SELECT id, details FROM alert_events WHERE rule_id = ? AND acked_at IS NULL AND resolved_at IS NULL",
            (rule["id"],),
        ) as cur:
            open_events = [(r[0], json.loads(r[1])) for r in await cur.fetchall()]
        if not open_events:
            return
        now = datetime.now(tz=timezone.utc)
        for eid, details in open_events:
            sampler_ip = details.get("sampler_ip", "")
            if sampler_ip in gapped_samplers:
                continue  # still gapped — don't resolve
            ts = last_seen.get(sampler_ip)
            if ts is None:
                continue  # no data at all — can't confirm recovery
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts) <= timedelta(minutes=silence_min):
                await db.execute(
                    "UPDATE alert_events SET resolved_at = datetime('now'), auto_resolved = 1 WHERE id = ?",
                    (eid,),
                )
                log.info(
                    f"Auto-resolved data_gap event {eid} for rule '{rule['name']}': "
                    f"{sampler_ip} recovered (last seen {ts.isoformat()})"
                )
        await db.commit()

    async def _fire(self, db: aiosqlite.Connection, rule: dict, message: str, details: dict) -> None:
        """Record an alert event and dispatch notifications, respecting cooldown."""
        # Check cooldown
        if rule.get("last_fired"):
            last = datetime.fromisoformat(rule["last_fired"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (datetime.now(tz=timezone.utc) - last) < timedelta(minutes=rule["cooldown_min"]):
                return  # Still in cooldown

        # Record event
        async with db.execute(
            "INSERT INTO alert_events (rule_id, severity, message, details) VALUES (?,?,?,?) RETURNING id",
            (rule["id"], rule["severity"], message, json.dumps(details)),
        ) as cur:
            row = await cur.fetchone()
            event_id = row[0]

        # Update last_fired timestamp
        await db.execute(
            "UPDATE alert_rules SET last_fired = datetime('now') WHERE id = ?", (rule["id"],)
        )
        await db.commit()

        log.warning(f"ALERT [{rule['severity'].upper()}] {rule['name']}: {message}")

        # Broadcast to WebSocket clients (fire-and-forget)
        try:
            from app.api.ws import broadcast_alert_fired
            asyncio.create_task(broadcast_alert_fired(
                event_id=event_id,
                rule_name=rule["name"],
                severity=rule["severity"],
                message=message,
                details=details,
            ))
        except Exception:
            pass

        # Dispatch to notification channels
        for channel in rule.get("channels", ["inapp"]):
            await self._dispatch(db, event_id, channel, rule["name"], message, rule["severity"])

    async def _dispatch(
        self, db: aiosqlite.Connection, event_id: int,
        channel: str, rule_name: str, message: str, severity: str,
    ) -> None:
        """Send notification to a channel, log the result."""
        try:
            if channel == "inapp":
                status = "sent"  # In-app is the alert_events table itself
            elif channel == "slack":
                status = await self._send_slack(db, rule_name, message, severity)
            elif channel == "email":
                status = await self._send_email(db, rule_name, message, severity)
            elif channel == "pagerduty":
                status = await self._send_pagerduty(db, rule_name, message, severity)
            elif channel == "webhook":
                status = await self._send_webhook(db, rule_name, message, severity)
            elif channel == "tracecat":
                status = await self._send_tracecat(db, event_id, rule_name, message, severity)
            else:
                status = "skipped"
        except Exception as e:
            log.error(f"Notification dispatch error ({channel}): {e}")
            status = "failed"

        await db.execute(
            "INSERT INTO notification_log (event_id, channel, status) VALUES (?,?,?)",
            (event_id, channel, status),
        )
        await db.commit()

    async def _send_slack(self, db, rule_name, message, severity) -> str:
        async with db.execute("SELECT value FROM settings WHERE key='notify_slack_webhook_url'") as cur:
            row = await cur.fetchone()
        if not row:
            return "skipped"
        import httpx
        url = json.loads(row[0])
        emoji = {"critical": ":red_circle:", "warning": ":large_yellow_circle:"}.get(severity, ":white_circle:")
        payload = {"text": f"{emoji} *pktLog Alert — {rule_name}*\n{message}"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=5)
        return "sent" if resp.status_code == 200 else "failed"

    async def _send_email(self, db, rule_name, message, severity) -> str:
        async def _get(key):
            async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as c:
                row = await c.fetchone()
            return json.loads(row[0]) if row else None

        if not await _get("notify_email_enabled"):
            return "skipped"

        host      = await _get("notify_email_smtp_host")   or ""
        port      = await _get("notify_email_smtp_port")   or 587
        tls_val   = await _get("notify_email_smtp_tls")
        use_tls   = tls_val if tls_val is not None else True
        username  = await _get("notify_email_username")    or ""
        password  = await _get("notify_email_password")    or ""
        from_addr = await _get("notify_email_from")        or "pktlog@localhost"
        to_addrs  = await _get("notify_email_default_to")  or []

        if not host or not to_addrs:
            return "skipped"

        try:
            import aiosmtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            sev_upper = severity.upper()
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[pktLog {sev_upper}] {rule_name}"
            msg["From"] = from_addr
            msg["To"] = ", ".join(to_addrs)
            body = f"pktLog Alert\n\nRule: {rule_name}\nSeverity: {sev_upper}\n\n{message}"
            msg.attach(MIMEText(body, "plain"))

            await aiosmtplib.send(
                msg,
                hostname=host,
                port=int(port),
                use_tls=bool(use_tls),
                username=username or None,
                password=password or None,
            )
            return "sent"
        except Exception as e:
            log.error(f"Email send error: {e}")
            return "failed"

    async def _send_pagerduty(self, db, rule_name, message, severity) -> str:
        async def _get(key):
            async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as c:
                row = await c.fetchone()
            return json.loads(row[0]) if row else None

        if not await _get("notify_pagerduty_enabled"):
            return "skipped"

        key = await _get("notify_pagerduty_integration_key") or ""
        if not key:
            return "skipped"

        sev_map = {"critical": "critical", "warning": "warning", "info": "info"}
        payload = {
            "routing_key": key,
            "event_action": "trigger",
            "payload": {
                "summary": f"[pktLog] {rule_name}: {message}",
                "severity": sev_map.get(severity, "warning"),
                "source": "pktlog",
            },
        }

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=payload,
                    timeout=10,
                )
            return "sent" if resp.status_code in (200, 202) else "failed"
        except Exception as e:
            log.error(f"PagerDuty send error: {e}")
            return "failed"

    async def _send_webhook(self, db, rule_name, message, severity) -> str:
        async def _get(key):
            async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as c:
                row = await c.fetchone()
            return json.loads(row[0]) if row else None

        if not await _get("notify_webhook_enabled"):
            return "skipped"

        url      = await _get("notify_webhook_url")             or ""
        method   = await _get("notify_webhook_method")          or "POST"
        template = await _get("notify_webhook_payload_template") or ""
        headers_cfg = await _get("notify_webhook_headers")      or {}

        if not url:
            return "skipped"

        try:
            from jinja2 import Template
            now = datetime.now(tz=timezone.utc).isoformat()
            rendered = Template(template).render(
                alert_name=rule_name,
                message=message,
                severity=severity,
                fired_at=now,
            )
            body = json.loads(rendered)
        except Exception as e:
            log.error(f"Webhook template render error: {e}")
            return "failed"

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method.upper(), url,
                    json=body,
                    headers=headers_cfg,
                    timeout=10,
                )
            return "sent" if resp.status_code < 300 else "failed"
        except Exception as e:
            log.error(f"Webhook send error: {e}")
            return "failed"

    async def _send_tracecat(
        self, db, event_id: int, rule_name: str, message: str, severity: str
    ) -> str:
        async def _get(key):
            async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as c:
                row = await c.fetchone()
            return json.loads(row[0]) if row else None

        if not await _get("notify_tracecat_enabled"):
            return "skipped"

        webhook_url = await _get("notify_tracecat_webhook_url") or ""
        api_token   = await _get("notify_tracecat_api_token")   or ""

        if not webhook_url:
            return "skipped"

        # Fetch full event details so the workflow has rich context
        async with db.execute(
            "SELECT details, fired_at FROM alert_events WHERE id = ?", (event_id,)
        ) as cur:
            row = await cur.fetchone()
        details = json.loads(row[0]) if row else {}
        fired_at = row[1] if row else datetime.now(tz=timezone.utc).isoformat()

        payload = {
            "source": "pktlog",
            "event_id": event_id,
            "alert_name": rule_name,
            "severity": severity,
            "message": message,
            "fired_at": fired_at,
            "details": details,
        }

        headers: dict = {"Content-Type": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(webhook_url, json=payload, headers=headers, timeout=10)
            if resp.status_code < 300:
                return "sent"
            log.warning(f"TraceCat webhook returned {resp.status_code}: {resp.text[:200]}")
            return "failed"
        except Exception as e:
            log.error(f"TraceCat send error: {e}")
            return "failed"

    async def _check_unknown_samplers(self, db: aiosqlite.Connection) -> None:
        global _unknown_sampler_queue
        if not _unknown_sampler_queue:
            return
        ips = list(set(_unknown_sampler_queue))
        _unknown_sampler_queue.clear()

        # Check which are actually not in the collector registry
        for ip in ips:
            async with db.execute(
                "SELECT 1 FROM collector_registry WHERE collector_ip = ?", (ip,)
            ) as cur:
                exists = await cur.fetchone()
            if not exists:
                async with db.execute(
                    "SELECT * FROM alert_rules WHERE rule_type='new_host' AND enabled=1"
                ) as cur:
                    rule = await cur.fetchone()
                if rule:
                    rule_dict = dict(rule)
                    rule_dict["conditions"] = json.loads(rule_dict["conditions"])
                    rule_dict["channels"] = json.loads(rule_dict["channels"])
                    await self._fire(
                        db, rule_dict,
                        f"Unknown collector {ip} sent syslog data — not in collector registry",
                        {"sampler_ip": ip},
                    )

    @staticmethod
    def notify_unknown_sampler(ip: str) -> None:
        """Called from the normalizer when an unrecognized collector_ip sends data."""
        _unknown_sampler_queue.append(ip)
