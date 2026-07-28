"""
pktLog — Widget endpoints for pktHub NOC Builder integration.

Manifest: GET /api/widgets/manifest  → list of widget definitions
Views:    GET /widgets/{widget_type}  → server-rendered HTML page (iframe target)
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings

router = APIRouter()
settings = get_settings()

_DB = Path(__file__).parent.parent.parent / "pktlog.db"

# ── Manifest ──────────────────────────────────────────────────────────────────

MANIFEST = [
    {
        "id": "log_stream",
        "title": "Live Log Stream",
        "description": "Scrolling recent syslog events with severity color coding",
        "view_path": "/api/widgets/log_stream",
        "default_w": 700,
        "default_h": 400,
        "min_w": 400,
        "min_h": 200,
        "params": [
            {
                "key": "severity_max",
                "label": "Min Severity",
                "type": "select",
                "options": [
                    {"value": "", "label": "All"},
                    {"value": "4", "label": "≤ Warning (4)"},
                    {"value": "3", "label": "≤ Error (3)"},
                    {"value": "2", "label": "≤ Critical (2)"},
                    {"value": "0", "label": "Emergency only (0)"},
                ],
            }
        ],
    },
    {
        "id": "error_rate",
        "title": "Error Rate",
        "description": "Error and critical event counts by severity over time",
        "view_path": "/api/widgets/error_rate",
        "default_w": 460,
        "default_h": 280,
        "min_w": 280,
        "min_h": 180,
    },
    {
        "id": "facility_breakdown",
        "title": "Facility Breakdown",
        "description": "Syslog message volume by facility",
        "view_path": "/api/widgets/facility_breakdown",
        "default_w": 460,
        "default_h": 320,
        "min_w": 260,
        "min_h": 180,
    },
    {
        "id": "alert_events",
        "title": "Alert Events",
        "description": "High-severity syslog events and alert triggers",
        "view_path": "/api/widgets/alert_events",
        "default_w": 640,
        "default_h": 360,
        "min_w": 320,
        "min_h": 200,
    },
    {
        "id": "log_sources",
        "title": "Log Sources",
        "description": "Registered syslog devices with message counts and last-seen timestamp",
        "view_path": "/api/widgets/log_sources",
        "default_w": 640,
        "default_h": 340,
        "min_w": 340,
        "min_h": 200,
    },
    {
        "id": "top_devices",
        "title": "Top Devices",
        "description": "Highest-volume syslog senders in the last hour",
        "view_path": "/api/widgets/top_devices",
        "default_w": 500,
        "default_h": 320,
        "min_w": 300,
        "min_h": 200,
    }
]


@router.get("/widgets/manifest")
async def widget_manifest():
    return MANIFEST


# ── Shared HTML shell ─────────────────────────────────────────────────────────

def _widget_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a1628;color:#e2e8f0;font-family:'Inter',system-ui,sans-serif;font-size:13px;height:100vh;overflow:hidden;display:flex;flex-direction:column}}
  .header{{padding:10px 14px;border-bottom:1px solid #1e293b;display:flex;align-items:center;gap:8px;flex-shrink:0}}
  .header-title{{font-size:12px;font-weight:600;color:#94a3b8;letter-spacing:0.03em}}
  .content{{flex:1;overflow:auto;padding:12px}}
  .log-row{{padding:5px 8px;border-bottom:1px solid #0f172a;font-size:11px;font-family:monospace;display:flex;align-items:center;gap:8px;overflow:hidden}}
  .sev{{display:inline-block;width:14px;height:14px;border-radius:3px;flex-shrink:0;font-size:9px;text-align:center;line-height:14px;font-weight:700}}
  .sev-0,.sev-1,.sev-2{{background:#3f1515;color:#f87171}}
  .sev-3{{background:#431407;color:#fb923c}}
  .sev-4{{background:#422006;color:#fbbf24}}
  .sev-5,.sev-6{{background:#052e16;color:#4ade80}}
  .sev-7{{background:#1e293b;color:#94a3b8}}
  .ts{{color:#475569;font-size:10px;flex-shrink:0}}
  .host{{color:#60a5fa;flex-shrink:0}}
  .msg{{color:#cbd5e1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .empty{{text-align:center;padding:40px;color:#334155;font-size:12px}}
  table{{width:100%;border-collapse:collapse}}
  th{{text-align:left;font-size:10px;color:#475569;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;padding:4px 8px;border-bottom:1px solid #1e293b}}
  td{{padding:6px 8px;border-bottom:1px solid #0f172a;font-size:12px;color:#cbd5e1}}
  .badge{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600}}
  .bar-row{{display:flex;align-items:center;gap:8px;margin-bottom:8px}}
  .bar-label{{font-size:11px;color:#94a3b8;width:110px;flex-shrink:0;text-overflow:ellipsis;overflow:hidden;white-space:nowrap}}
  .bar-track{{flex:1;background:#1e293b;border-radius:3px;height:8px;overflow:hidden}}
  .bar-fill{{height:8px;border-radius:3px;transition:width 0.3s}}
  .bar-val{{font-size:10px;color:#475569;width:40px;text-align:right;flex-shrink:0}}
</style>
<script>setTimeout(()=>location.reload(),30000)</script>
</head>
<body>
{body}
</body>
</html>"""


_SEV_LABELS = {0:'EMG',1:'ALT',2:'CRT',3:'ERR',4:'WRN',5:'NTC',6:'INF',7:'DBG'}

FACILITY_NAMES = {
    0:"kern",1:"user",2:"mail",3:"daemon",4:"auth",5:"syslog",
    6:"lpr",7:"news",8:"uucp",9:"cron",10:"authpriv",11:"ftp",
    16:"local0",17:"local1",18:"local2",19:"local3",
    20:"local4",21:"local5",22:"local6",23:"local7",
}

def _clamp_sev(raw) -> int:
    try: return min(7, max(0, int(raw or 7)))
    except: return 7

def _fmt_ts(ts: str) -> str:
    if not ts: return ""
    return str(ts)[:19].replace("T", " ")


# ── Log Stream widget ─────────────────────────────────────────────────────────

@router.get("/widgets/log_stream", response_class=HTMLResponse, include_in_schema=False)
async def widget_log_stream():
    rows = []
    try:
        async with aiosqlite.connect(str(_DB)) as db:
            db.row_factory = aiosqlite.Row
            for q in [
                "SELECT received_at as ts, hostname as host, severity, message as msg FROM syslog_messages ORDER BY received_at DESC LIMIT 60",
                "SELECT created_at as ts, level as severity, message as msg, '' as host FROM logs ORDER BY created_at DESC LIMIT 60",
            ]:
                try:
                    async with db.execute(q) as cur:
                        rows = [dict(r) for r in await cur.fetchall()]
                    if rows: break
                except Exception:
                    continue
    except Exception:
        pass

    if rows:
        parts = []
        for r in rows:
            sev = _clamp_sev(r.get("severity", 7))
            label = _SEV_LABELS.get(sev, "INF")
            parts.append(
                f"<div class='log-row'>"
                f"<span class='sev sev-{sev}'>{label}</span>"
                f"<span class='ts'>{_fmt_ts(r.get('ts',''))}</span>"
                f"<span class='host'>{str(r.get('host') or '')[:20]}</span>"
                f"<span class='msg'>{str(r.get('msg') or '')[:200]}</span>"
                f"</div>"
            )
        content = "".join(parts)
    else:
        content = "<div class='empty'>No recent log entries</div>"

    body = f"""<div class="header"><div style="width:6px;height:6px;border-radius:50%;background:#4ade80"></div><span class="header-title">Live Log Stream</span></div>
<div class="content" style="padding:0">{content}</div>"""
    return HTMLResponse(_widget_page("Log Stream", body))


# ── Error Rate widget ─────────────────────────────────────────────────────────

@router.get("/widgets/error_rate", response_class=HTMLResponse, include_in_schema=False)
async def widget_error_rate():
    stats: dict = {}
    try:
        async with aiosqlite.connect(str(_DB)) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute("""
                    SELECT severity, COUNT(*) as cnt FROM syslog_messages
                    WHERE received_at > datetime('now', '-60 minutes')
                    GROUP BY severity ORDER BY severity
                """) as cur:
                    for r in await cur.fetchall(): stats[int(r['severity'])] = r['cnt']
            except Exception:
                pass
    except Exception:
        pass

    critical = sum(stats.get(i, 0) for i in range(3))
    error = stats.get(3, 0)
    warning = stats.get(4, 0)
    info = sum(stats.get(i, 0) for i in range(5, 8))
    total = critical + error + warning + info or 1

    def bar(pct, color):
        w = max(2, int(pct * 100))
        return f"<div style='height:8px;border-radius:4px;background:{color};width:{w}%;transition:width 0.3s'></div>"

    content = f"""<div style="display:flex;flex-direction:column;gap:14px">
  <div><div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="color:#f87171;font-size:11px;font-weight:600">Critical/Alert/Emergency</span><span style="color:#f87171;font-weight:700">{critical}</span></div>{bar(critical/total,'#f87171')}</div>
  <div><div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="color:#fb923c;font-size:11px;font-weight:600">Error</span><span style="color:#fb923c;font-weight:700">{error}</span></div>{bar(error/total,'#fb923c')}</div>
  <div><div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="color:#fbbf24;font-size:11px;font-weight:600">Warning</span><span style="color:#fbbf24;font-weight:700">{warning}</span></div>{bar(warning/total,'#fbbf24')}</div>
  <div><div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="color:#4ade80;font-size:11px;font-weight:600">Notice/Info/Debug</span><span style="color:#4ade80;font-weight:700">{info}</span></div>{bar(info/total,'#4ade80')}</div>
  <div style="margin-top:8px;padding-top:8px;border-top:1px solid #1e293b;font-size:10px;color:#475569">Last 60 minutes &bull; {total} total events</div>
</div>"""

    body = f"""<div class="header"><div style="width:6px;height:6px;border-radius:50%;background:#fb923c"></div><span class="header-title">Error Rate — last 60 min</span></div>
<div class="content">{content}</div>"""
    return HTMLResponse(_widget_page("Error Rate", body))


# ── Facility Breakdown widget ─────────────────────────────────────────────────

@router.get("/widgets/facility_breakdown", response_class=HTMLResponse, include_in_schema=False)
async def widget_facility_breakdown():
    rows = []
    try:
        async with aiosqlite.connect(str(_DB)) as db:
            db.row_factory = aiosqlite.Row
            try:
                async with db.execute("""
                    SELECT facility, COUNT(*) as cnt FROM syslog_messages
                    WHERE received_at > datetime('now', '-60 minutes')
                    GROUP BY facility ORDER BY cnt DESC LIMIT 16
                """) as cur:
                    rows = [dict(r) for r in await cur.fetchall()]
            except Exception:
                pass
    except Exception:
        pass

    COLORS = ["#60a5fa","#a78bfa","#4ade80","#2dd4bf","#f472b6",
              "#fbbf24","#fb923c","#f87171","#818cf8","#34d399","#e879f9","#38bdf8"]
    max_cnt = max((r["cnt"] or 0 for r in rows), default=1)

    if rows:
        bars = []
        for i, r in enumerate(rows):
            fac = r.get("facility")
            label = FACILITY_NAMES.get(int(fac or 0), f"fac{fac}")
            pct = int(((r["cnt"] or 0) / max_cnt) * 100)
            color = COLORS[i % len(COLORS)]
            bars.append(
                f"<div class='bar-row'>"
                f"<span class='bar-label' title='{label}'>{label}</span>"
                f"<div class='bar-track'><div class='bar-fill' style='width:{pct}%;background:{color}'></div></div>"
                f"<span class='bar-val'>{r['cnt']}</span>"
                f"</div>"
            )
        content = "".join(bars)
    else:
        content = "<div class='empty'>No syslog data in last 60 min</div>"

    body = f"""<div class="header"><div style="width:6px;height:6px;border-radius:50%;background:#818cf8"></div><span class="header-title">Facility Breakdown — last 60 min</span></div>
<div class="content">{content}</div>"""
    return HTMLResponse(_widget_page("Facility Breakdown", body))


# ── Alert Events widget ───────────────────────────────────────────────────────

@router.get("/widgets/alert_events", response_class=HTMLResponse, include_in_schema=False)
async def widget_alert_events():
    rows = []
    try:
        async with aiosqlite.connect(str(_DB)) as db:
            db.row_factory = aiosqlite.Row
            for q in [
                "SELECT fired_at as ts, severity, message as msg, rule_name FROM alert_events ORDER BY fired_at DESC LIMIT 30",
                "SELECT created_at as ts, severity, message as msg, rule_name FROM alerts ORDER BY created_at DESC LIMIT 30",
                "SELECT received_at as ts, severity, message as msg, hostname as rule_name FROM syslog_messages WHERE severity <= 3 ORDER BY received_at DESC LIMIT 30",
            ]:
                try:
                    async with db.execute(q) as cur:
                        rows = [dict(r) for r in await cur.fetchall()]
                    if rows: break
                except Exception:
                    continue
    except Exception:
        pass

    SEV_COLORS = {0:"#f87171",1:"#f87171",2:"#f87171",3:"#fb923c",4:"#fbbf24"}

    if rows:
        trs = []
        for r in rows:
            sev_raw = r.get("severity", 3)
            try: sev_int = int(sev_raw)
            except: sev_int = 3
            sev_int = min(7, max(0, sev_int))
            color = SEV_COLORS.get(sev_int, "#94a3b8")
            label = _SEV_LABELS.get(sev_int, "ERR")
            rule = str(r.get("rule_name") or "")[:24]
            msg = str(r.get("msg") or "")[:80]
            trs.append(
                f"<tr>"
                f"<td style='font-size:10px;color:#475569'>{_fmt_ts(r.get('ts',''))}</td>"
                f"<td><span class='badge' style='background:#1e293b;color:{color}'>{label}</span></td>"
                f"<td style='color:#60a5fa;font-size:11px'>{rule}</td>"
                f"<td style='color:#e2e8f0'>{msg}</td>"
                f"</tr>"
            )
        table = f"<table><thead><tr><th>Time</th><th>Sev</th><th>Source</th><th>Message</th></tr></thead><tbody>{''.join(trs)}</tbody></table>"
    else:
        table = "<div class='empty'>No alert events in last 60 min</div>"

    body = f"""<div class="header"><div style="width:6px;height:6px;border-radius:50%;background:#f87171"></div><span class="header-title">Alert Events</span></div>
<div class="content">{table}</div>"""
    return HTMLResponse(_widget_page("Alert Events", body))


# ── Log Sources ───────────────────────────────────────────────────────────────
@router.get("/widgets/log_sources", response_class=HTMLResponse, include_in_schema=False)
async def widget_log_sources():
    rows = []
    try:
        async with aiosqlite.connect(str(_DB)) as db:
            db.row_factory = aiosqlite.Row
            for q in [
                "SELECT hostname, ip_address as ip, last_seen, message_count as msg_count FROM devices ORDER BY message_count DESC LIMIT 25",
                "SELECT hostname, ip_address as ip, last_seen, 0 as msg_count FROM devices ORDER BY last_seen DESC LIMIT 25",
                "SELECT ip_address as ip, ip_address as hostname, last_seen, 0 as msg_count FROM devices ORDER BY last_seen DESC LIMIT 25",
            ]:
                try:
                    async with db.execute(q) as cur:
                        rows = [dict(r) for r in await cur.fetchall()]
                    if rows:
                        break
                except Exception:
                    continue
    except Exception:
        pass

    # Fallback: derive from syslog message table if devices table empty
    if not rows:
        try:
            async with aiosqlite.connect(str(_DB)) as db:
                db.row_factory = aiosqlite.Row
                for col in ("hostname", "host", "source", "src_host", "device"):
                    for tbl in ("syslog_messages", "messages", "logs", "syslog"):
                        try:
                            async with db.execute(
                                f"SELECT {col} as hostname, COUNT(*) as msg_count, MAX(timestamp) as last_seen"
                                f" FROM {tbl} WHERE timestamp > datetime('now', '-24 hours')"
                                f" GROUP BY {col} ORDER BY msg_count DESC LIMIT 25"
                            ) as cur:
                                rows = [dict(r) for r in await cur.fetchall()]
                            if rows:
                                break
                        except Exception:
                            continue
                    if rows:
                        break
        except Exception:
            pass

    def _ts(t):
        if not t:
            return "—"
        return str(t)[:19].replace("T", " ")

    if rows:
        trs = []
        for r in rows:
            host = r.get("hostname") or r.get("ip") or "—"
            ip = r.get("ip", "")
            msgs = f"{r.get('msg_count') or 0:,}"
            seen = _ts(r.get("last_seen", ""))
            trs.append(
                f"<tr><td>{host}</td>"
                f"<td style='font-family:monospace;font-size:11px;color:#475569'>{ip}</td>"
                f"<td>{msgs}</td>"
                f"<td style='font-size:10px;color:#475569'>{seen}</td></tr>"
            )
        content = (
            "<table><thead><tr><th>Host</th><th>IP</th><th>Messages</th><th>Last Seen</th></tr></thead>"
            f"<tbody>{chr(10).join(trs)}</tbody></table>"
        )
    else:
        content = "<div class='empty'>No device data available</div>"

    body = (
        "<div class='hdr'><div class='hdr-dot' style='background:#38bdf8'></div>"
        "<span class='hdr-title'>Log Sources</span></div>"
        f"<div class='content'>{content}</div>"
    )
    return HTMLResponse(_widget_page("Log Sources", body))


# ── Top Devices ───────────────────────────────────────────────────────────────
@router.get("/widgets/top_devices", response_class=HTMLResponse, include_in_schema=False)
async def widget_top_devices():
    rows = []
    try:
        async with aiosqlite.connect(str(_DB)) as db:
            db.row_factory = aiosqlite.Row
            for col in ("hostname", "host", "source", "src_host", "device"):
                for tbl in ("syslog_messages", "messages", "logs", "syslog"):
                    try:
                        async with db.execute(
                            f"SELECT {col} as device, COUNT(*) as cnt,"
                            f" SUM(CASE WHEN severity<=3 THEN 1 ELSE 0 END) as errors"
                            f" FROM {tbl}"
                            f" WHERE timestamp > datetime('now', '-1 hour')"
                            f" GROUP BY {col} ORDER BY cnt DESC LIMIT 15"
                        ) as cur:
                            rows = [dict(r) for r in await cur.fetchall()]
                        if rows:
                            break
                    except Exception:
                        continue
                if rows:
                    break
    except Exception:
        pass

    max_c = max((r["cnt"] or 0 for r in rows), default=1)

    if rows:
        bars = []
        for r in rows:
            dev = str(r.get("device") or "—")[:28]
            pct = int(((r["cnt"] or 0) / max_c) * 100)
            errs = r.get("errors") or 0
            err_txt = f" ({errs} err)" if errs else ""
            bars.append(
                f"<div class='bar-row'>"
                f"<span class='bar-lbl' title='{dev}'>{dev}</span>"
                f"<div class='bar-trk'><div class='bar-fill' style='width:{pct}%;background:#818cf8'></div></div>"
                f"<span class='bar-val'>{r['cnt']:,}{err_txt}</span>"
                f"</div>"
            )
        content = "".join(bars)
    else:
        content = "<div class='empty'>No device activity in the last hour</div>"

    body = (
        "<div class='hdr'><div class='hdr-dot' style='background:#818cf8'></div>"
        "<span class='hdr-title'>Top Devices — last 1 hr</span></div>"
        f"<div class='content'>{content}</div>"
    )
    return HTMLResponse(_widget_page("Top Devices", body))
