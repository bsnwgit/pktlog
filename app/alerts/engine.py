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

        elif rule_type == "port_protocol":
            await self._evaluate_port_protocol(db, rule)

        elif rule_type == "top_talker":
            await self._evaluate_top_talker(db, rule)

        elif rule_type == "elephant_flow":
            await self._evaluate_elephant_flow(db, rule)

        elif rule_type == "inter_site_traffic":
            await self._evaluate_inter_site_traffic(db, rule)

        elif rule_type == "connection_burst":
            await self._evaluate_connection_burst(db, rule)

        elif rule_type == "port_scan":
            await self._evaluate_port_scan(db, rule)

        elif rule_type == "internal_spread":
            await self._evaluate_internal_spread(db, rule)

        elif rule_type == "protocol_anomaly":
            await self._evaluate_protocol_anomaly(db, rule)

        elif rule_type == "ingest_rate_low":
            await self._evaluate_ingest_rate_low(db, rule)

        elif rule_type == "clickhouse_size":
            await self._evaluate_clickhouse_size(db, rule)

    async def _evaluate_threshold(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        metric = conds.get("metric", "bytes")
        operator = conds.get("operator", "gt")
        threshold = float(conds.get("value", 0))
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        storage = get_storage()
        current = await storage.get_metric_in_window(metric, window_min, sampler_ip)
        ops = {"gt": current > threshold, "gte": current >= threshold,
               "lt": current < threshold, "lte": current <= threshold}
        if ops.get(operator, False):
            op_sym = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}.get(operator, operator)
            sampler_str = f" from {sampler_ip}" if sampler_ip else ""
            top_ips = await storage.get_threshold_top_ips(metric, window_min, sampler_ip)
            await self._fire(db, rule, (
                f"Threshold breached{sampler_str}: {metric} in last {window_min}m "
                f"= {current:,.0f} {op_sym} {threshold:,.0f}"
            ), {"metric": metric, "current": current, "threshold": threshold,
                "operator": operator, "sampler_ip": sampler_ip or "all",
                "top_sources": top_ips})
        else:
            await self._auto_resolve(db, rule, f"{metric} back within threshold (current: {current:,.0f})")

    async def _evaluate_rate_spike(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        metric = conds.get("metric", "bytes")
        multiplier = float(conds.get("multiplier", 3.0))
        baseline_days = int(conds.get("baseline_days", 7))
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        storage = get_storage()
        current = await storage.get_metric_in_window(metric, window_min, sampler_ip)
        baseline = await storage.get_metric_baseline(metric, baseline_days, window_min, sampler_ip)

        if baseline > 0 and current >= baseline * multiplier:
            ratio = current / baseline
            sampler_str = f" from {sampler_ip}" if sampler_ip else ""
            top_ips = await storage.get_threshold_top_ips(metric, window_min, sampler_ip)
            await self._fire(db, rule, (
                f"Rate spike detected{sampler_str}: {metric} in last {window_min}m "
                f"= {current:,.0f} ({ratio:.1f}× the {baseline_days}-day baseline of {baseline:,.0f})"
            ), {"metric": metric, "current": current, "baseline": baseline,
                "ratio": round(ratio, 2), "multiplier": multiplier,
                "sampler_ip": sampler_ip or "all",
                "top_sources": top_ips})
        else:
            await self._auto_resolve(db, rule, f"{metric} rate back to normal (current: {current:,.0f}, baseline: {baseline:,.0f})")

    async def _evaluate_port_protocol(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        port = int(conds.get("port", 0))
        if port <= 0:
            log.warning(f"port_protocol rule '{rule['name']}' has no valid port configured")
            return
        proto_str = conds.get("protocol", "any")
        direction = conds.get("direction", "any")
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        _PROTO_INT = {"TCP": 6, "UDP": 17}
        protocol_int = _PROTO_INT.get(proto_str) if proto_str != "any" else None

        storage = get_storage()
        count = await storage.get_port_flow_count(port, protocol_int, direction, window_min, sampler_ip)
        if count > 0:
            dir_str = {"src": "src port", "dst": "dst port", "any": "port"}.get(direction, "port")
            proto_label = proto_str if proto_str != "any" else "any protocol"
            sampler_str = f" on {sampler_ip}" if sampler_ip else ""
            top_ips = await storage.get_port_flow_top_ips(port, protocol_int, direction, window_min, sampler_ip)
            await self._fire(db, rule, (
                f"Traffic on {dir_str} {port}/{proto_label}{sampler_str}: "
                f"{count} flows in last {window_min}m"
            ), {"port": port, "protocol": proto_str, "direction": direction,
                "flow_count": count, "sampler_ip": sampler_ip or "all",
                "top_sources": top_ips})
        else:
            await self._auto_resolve(db, rule, f"No traffic on port {port}/{proto_str} in last {window_min}m")

    async def _evaluate_top_talker(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        metric = conds.get("metric", "bytes")
        threshold = float(conds.get("threshold", 0))
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        top_ip, value = await get_storage().get_top_talker_in_window(metric, window_min, sampler_ip)
        if not top_ip:
            await self._auto_resolve(db, rule, "No traffic in window")
            return

        if value >= threshold:
            sampler_str = f" on {sampler_ip}" if sampler_ip else ""
            unit = {"bytes": "bytes", "packets": "pkts", "flows": "flows"}.get(metric, metric)
            await self._fire(db, rule, (
                f"Top talker{sampler_str}: {top_ip} used {value:,.0f} {unit} "
                f"in last {window_min}m (threshold: {threshold:,.0f})"
            ), {"src_ip": top_ip, "value": value, "metric": metric,
                "threshold": threshold, "sampler_ip": sampler_ip or "all"})
        else:
            await self._auto_resolve(db, rule, f"Top talker {top_ip}: {value:,.0f} {metric} — within threshold")

    async def _evaluate_elephant_flow(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        threshold_mb = float(conds.get("threshold_mb", 100))
        threshold_bytes = threshold_mb * 1_000_000
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        storage = get_storage()
        count, max_bytes = await storage.get_elephant_flow_stats(threshold_bytes, window_min, sampler_ip)
        if count > 0:
            sampler_str = f" on {sampler_ip}" if sampler_ip else ""
            top_flows = await storage.get_elephant_flow_top(threshold_bytes, window_min, sampler_ip)
            await self._fire(db, rule, (
                f"Elephant flow detected{sampler_str}: {count} flow{'s' if count != 1 else ''} "
                f">{threshold_mb:.0f}MB (largest: {max_bytes/1_000_000:.1f}MB) in last {window_min}m"
            ), {"flow_count": count, "max_bytes": max_bytes, "threshold_mb": threshold_mb,
                "sampler_ip": sampler_ip or "all", "top_flows": top_flows})
        else:
            await self._auto_resolve(db, rule, f"No flows exceed {threshold_mb:.0f}MB threshold in last {window_min}m")

    async def _evaluate_inter_site_traffic(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        metric = conds.get("metric", "bytes")
        threshold = float(conds.get("threshold", 0))
        site_a = conds.get("site_a") or None
        site_b = conds.get("site_b") or None
        window_min = rule.get("time_window_min", 5)

        if threshold <= 0:
            log.warning(f"inter_site_traffic rule '{rule['name']}' has threshold <= 0; skipping to prevent constant firing")
            return

        storage = get_storage()
        value = await storage.get_inter_site_metric(metric, window_min, site_a, site_b)
        if value >= threshold:
            site_desc = f"{site_a}↔{site_b}" if site_a and site_b else (site_a or site_b or "cross-site")
            unit = {"bytes": "bytes", "packets": "pkts", "flows": "flows"}.get(metric, metric)
            top = await storage.get_inter_site_top_contributors(metric, window_min, site_a, site_b)
            await self._fire(db, rule, (
                f"Inter-site traffic spike ({site_desc}): {value:,.0f} {unit} "
                f"in last {window_min}m (threshold: {threshold:,.0f})"
            ), {"metric": metric, "value": value, "threshold": threshold,
                "site_a": site_a or "any", "site_b": site_b or "any",
                "top_contributors": top})
        else:
            await self._auto_resolve(db, rule, f"Inter-site {metric}: {value:,.0f} — within threshold")

    async def _evaluate_connection_burst(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        threshold = int(conds.get("threshold_connections", 1000))
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        storage = get_storage()
        top_ip, count = await storage.get_top_connection_count(window_min, sampler_ip)
        if not top_ip:
            await self._auto_resolve(db, rule, "No traffic in window")
            return

        if count >= threshold:
            sampler_str = f" on {sampler_ip}" if sampler_ip else ""
            top_dsts = await storage.get_top_dsts_for_ip(top_ip, window_min, sampler_ip)
            await self._fire(db, rule, (
                f"Connection burst{sampler_str}: {top_ip} made {count:,} connections "
                f"in last {window_min}m (threshold: {threshold:,})"
            ), {"src_ip": top_ip, "connection_count": count, "threshold": threshold,
                "sampler_ip": sampler_ip or "all",
                "top_destinations": top_dsts})
        else:
            await self._auto_resolve(db, rule, f"Connection burst: {top_ip} made {count:,} — within threshold")

    async def _evaluate_port_scan(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        threshold_ports = int(conds.get("threshold_ports", 50))
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        storage = get_storage()
        top_ip, distinct_ports = await storage.get_top_unique_dst_ports(window_min, sampler_ip)
        if not top_ip:
            await self._auto_resolve(db, rule, "No traffic in window")
            return

        if distinct_ports >= threshold_ports:
            sampler_str = f" on {sampler_ip}" if sampler_ip else ""
            sample_ports = await storage.get_top_ports_for_ip(top_ip, window_min, sampler_ip)
            await self._fire(db, rule, (
                f"Port scan detected{sampler_str}: {top_ip} hit {distinct_ports:,} distinct ports "
                f"in last {window_min}m (threshold: {threshold_ports})"
            ), {"src_ip": top_ip, "distinct_ports": distinct_ports, "threshold": threshold_ports,
                "sampler_ip": sampler_ip or "all",
                "sample_ports": sample_ports})
        else:
            await self._auto_resolve(db, rule, f"Port scan: {top_ip} hit {distinct_ports} distinct dst ports — below threshold")

    async def _evaluate_internal_spread(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        threshold_ips = int(conds.get("threshold_ips", 30))
        src_subnet = conds.get("src_subnet") or None
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        top_ip, distinct_dsts = await get_storage().get_top_unique_dst_ips(window_min, sampler_ip, src_subnet)
        if not top_ip:
            await self._auto_resolve(db, rule, "No traffic in window")
            return

        if distinct_dsts >= threshold_ips:
            subnet_str = f" (src subnet: {src_subnet})" if src_subnet else ""
            sampler_str = f" on {sampler_ip}" if sampler_ip else ""
            await self._fire(db, rule, (
                f"Internal spread detected{sampler_str}: {top_ip} reached {distinct_dsts:,} distinct "
                f"destinations in last {window_min}m (threshold: {threshold_ips}){subnet_str}"
            ), {"src_ip": top_ip, "distinct_destinations": distinct_dsts, "threshold": threshold_ips,
                "src_subnet": src_subnet or "any", "sampler_ip": sampler_ip or "all"})
        else:
            await self._auto_resolve(db, rule, f"Internal spread: {top_ip} reached {distinct_dsts} destinations — below threshold")

    async def _evaluate_protocol_anomaly(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        port = int(conds.get("port", 0))
        if port <= 0:
            log.warning(f"protocol_anomaly rule '{rule['name']}' has no valid port configured")
            return
        expected_proto_str = (conds.get("expected_proto") or "TCP").upper()
        direction = conds.get("direction", "dst")
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        _PROTO_INT = {"TCP": 6, "UDP": 17, "ICMP": 1}
        expected_proto_int = _PROTO_INT.get(expected_proto_str, 6)

        storage = get_storage()
        count = await storage.get_unexpected_proto_count(port, expected_proto_int, direction, window_min, sampler_ip)
        if count > 0:
            sampler_str = f" on {sampler_ip}" if sampler_ip else ""
            dir_str = {"src": "src port", "dst": "dst port", "any": "port"}.get(direction, "port")
            top_ips = await storage.get_unexpected_proto_top_ips(port, expected_proto_int, direction, window_min, sampler_ip)
            await self._fire(db, rule, (
                f"Protocol anomaly{sampler_str}: {count} flow{'s' if count != 1 else ''} on {dir_str} {port} "
                f"using unexpected protocol (expected {expected_proto_str}) in last {window_min}m"
            ), {"port": port, "expected_proto": expected_proto_str, "direction": direction,
                "flow_count": count, "sampler_ip": sampler_ip or "all",
                "top_sources": top_ips})
        else:
            await self._auto_resolve(db, rule, f"No unexpected protocol on port {port} in last {window_min}m")

    async def _evaluate_ingest_rate_low(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        min_fps = float(conds.get("min_flows_per_sec", 1.0))
        sampler_ip = conds.get("sampler_ip") or None
        window_min = rule.get("time_window_min", 5)

        total_flows = await get_storage().get_metric_in_window("flows", window_min, sampler_ip)
        fps = total_flows / (window_min * 60) if window_min > 0 else 0.0

        if fps < min_fps:
            sampler_str = f" from {sampler_ip}" if sampler_ip else ""
            await self._fire(db, rule, (
                f"Ingest rate low{sampler_str}: {fps:.2f} flows/sec in last {window_min}m "
                f"(minimum: {min_fps:.2f} flows/sec)"
            ), {"flows_per_sec": round(fps, 3), "min_flows_per_sec": min_fps,
                "total_flows": int(total_flows), "sampler_ip": sampler_ip or "all"})
        else:
            await self._auto_resolve(db, rule, f"Ingest rate normal: {fps:.2f} flows/sec — above minimum")

    async def _evaluate_clickhouse_size(self, db: aiosqlite.Connection, rule: dict) -> None:
        from app.storage.factory import get_storage
        conds = rule["conditions"]
        threshold_gb = float(conds.get("threshold_gb", 10.0))
        table = conds.get("table", "flows")

        size_gb = await get_storage().get_clickhouse_table_size_gb(table)
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

        # Check which are actually not in the device registry
        for ip in ips:
            async with db.execute("SELECT 1 FROM devices WHERE ip = ?", (ip,)) as cur:
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
                        f"Unknown sampler {ip} sent NetFlow data — not in device registry",
                        {"sampler_ip": ip},
                    )

    @staticmethod
    def notify_unknown_sampler(ip: str) -> None:
        """Called from ingest handler when an unrecognized source sends data."""
        _unknown_sampler_queue.append(ip)
