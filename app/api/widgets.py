"""
pktLog — Widget endpoints for pktHub NOC Builder integration.

Manifest: GET /api/widgets/manifest  → list of widget definitions
Views:    GET /widgets/{widget_type}  → server-rendered HTML page (iframe target)
"""
from __future__ import annotations

import html
from contextvars import ContextVar
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.dependencies import require_suite_token

# These views are embedded as unauthenticated iframes by pktHub's NOC Builder,
# so they can't require a login session — but they do render internal log and
# alert data, so every route on this router requires a valid X-Suite-Token
# (the trusted-proxy secret pktHub already sends on every proxied request).
# ── Refresh interval ──────────────────────────────────────────────────────────
# pktHub's Settings → NOC → "Widget refresh" governs how often a tile reloads
# itself. It arrives as ?refresh=<seconds> on the widget URL; captured here as a
# router dependency so the ~150 view functions need no signature change.
_REFRESH: ContextVar = ContextVar("widget_refresh", default=30)


async def _capture_refresh(request: Request) -> None:
    raw = request.query_params.get("refresh")
    try:
        _REFRESH.set(max(5, min(int(raw), 3600)) if raw else 30)
    except (TypeError, ValueError):
        _REFRESH.set(30)


router = APIRouter(dependencies=[Depends(_capture_refresh), Depends(require_suite_token)])
settings = get_settings()

_DB = Path(__file__).parent.parent.parent / "pktlog.db"

# ── Manifest ──────────────────────────────────────────────────────────────────
# `category` groups these in pktHub's NOC library picker. Every data surface the
# app renders in its own UI should have an entry here — the NOC builder can only
# offer what this list declares.
_WINDOW_PARAM = {
    "key": "hours", "label": "Window", "type": "select",
    "options": [{"value": "1", "label": "1 hour"}, {"value": "6", "label": "6 hours"},
                {"value": "24", "label": "24 hours"}, {"value": "168", "label": "7 days"}],
}

MANIFEST = [
    {
        "id": "log_stream",
        "category": "Events",
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
        "category": "Volume",
        "title": "Error Rate",
        "description": "Error and critical event counts by severity over time",
        "view_path": "/api/widgets/error_rate",
        "default_w": 460,
        "default_h": 280,
        "min_w": 280,
        "min_h": 180,
        "params": [_WINDOW_PARAM],
    },
    {
        "id": "facility_breakdown",
        "category": "Events",
        "title": "Facility Breakdown",
        "description": "Syslog message volume by facility",
        "view_path": "/api/widgets/facility_breakdown",
        "default_w": 460,
        "default_h": 320,
        "min_w": 260,
        "min_h": 180,
        "params": [_WINDOW_PARAM],
    },
    {
        "id": "alert_events",
        "category": "Alerts",
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
        "category": "Sources",
        "title": "Log Sources",
        "description": "Registered syslog devices with message counts and last-seen timestamp",
        "view_path": "/api/widgets/log_sources",
        "default_w": 640,
        "default_h": 340,
        "min_w": 340,
        "min_h": 200,
        "params": [_WINDOW_PARAM],
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
        "params": [_WINDOW_PARAM],
        "category": "Sources",
    },

    # ── Overview ──────────────────────────────────────────────────────────────
    {
        "id": "log_summary", "title": "Log Summary", "category": "Overview",
        "description": "Event, error and source counts over the selected window",
        "view_path": "/api/widgets/log_summary",
        "default_w": 620, "default_h": 200, "min_w": 320, "min_h": 150,
        "params": [_WINDOW_PARAM],
    },
    {
        "id": "severity_breakdown", "title": "Severity Breakdown", "category": "Overview",
        "description": "Event volume by syslog severity",
        "view_path": "/api/widgets/severity_breakdown",
        "default_w": 480, "default_h": 320, "min_w": 280, "min_h": 190,
        "params": [_WINDOW_PARAM],
    },
    {
        "id": "alert_summary", "title": "Alert Summary", "category": "Overview",
        "description": "Active alert counts by severity",
        "view_path": "/api/widgets/alert_summary",
        "default_w": 420, "default_h": 200, "min_w": 260, "min_h": 150,
    },

    # ── Volume (charts) ───────────────────────────────────────────────────────
    {
        "id": "ingest_trend", "title": "Ingest Trend", "category": "Volume",
        "description": "Events received over time",
        "view_path": "/api/widgets/ingest_trend",
        "default_w": 660, "default_h": 300, "min_w": 300, "min_h": 170,
        "params": [_WINDOW_PARAM],
    },

    # ── Sources ───────────────────────────────────────────────────────────────
    {
        "id": "top_programs", "title": "Top Programs", "category": "Sources",
        "description": "Highest-volume programs/tags in the window",
        "view_path": "/api/widgets/top_programs",
        "default_w": 520, "default_h": 340, "min_w": 300, "min_h": 200,
        "params": [_WINDOW_PARAM],
    },
    {
        "id": "top_hosts", "title": "Top Hosts", "category": "Sources",
        "description": "Highest-volume source hosts in the window",
        "view_path": "/api/widgets/top_hosts",
        "default_w": 560, "default_h": 340, "min_w": 300, "min_h": 200,
        "params": [_WINDOW_PARAM],
    },
    {
        "id": "silent_sources", "title": "Silent Sources", "category": "Sources",
        "description": "Registered devices that have sent nothing in the window",
        "view_path": "/api/widgets/silent_sources",
        "default_w": 600, "default_h": 340, "min_w": 320, "min_h": 200,
        "params": [_WINDOW_PARAM],
    },

    # ── Collectors ────────────────────────────────────────────────────────────
    {
        "id": "collector_status", "title": "Collector Status", "category": "Collectors",
        "description": "Registered collectors and time since last received event",
        "view_path": "/api/widgets/collector_status",
        "default_w": 620, "default_h": 320, "min_w": 320, "min_h": 190,
    },
]


@router.get("/widgets/manifest")
async def widget_manifest():
    return MANIFEST



# ── Widget states ──────────────────────────────────────────────────────────────
# A blank tile on a wallboard reads as "all quiet", so the three reasons a widget
# can show nothing must look different from each other:
#   empty — the query ran and there genuinely is nothing
#   cfg   — the widget needs a param chosen in the NOC editor before it can run
#   err   — the query failed; this must never be mistaken for "nothing to report"
# Query helpers record failures here rather than swallowing them; _page() renders
# the error state instead of whatever half-built body the caller produced. The
# ContextVar is per-request: each request runs in its own task context.
_WIDGET_ERR: ContextVar = ContextVar("widget_err", default=None)


def _note_err(exc: BaseException) -> None:
    _WIDGET_ERR.set(f"{type(exc).__name__}: {exc}"[:200])


def _state(kind: str, msg: str, sub: str = "") -> str:
    icon = {"empty": "○", "cfg": "⚙", "err": "⚠"}.get(kind, "○")
    sub_html = f'<div class="state-sub">{html.escape(str(sub))}</div>' if sub else ""
    return (f'<div class="state state-{kind}"><div class="state-icon">{icon}</div>'
            f'<div class="state-msg">{html.escape(str(msg))}</div>{sub_html}</div>')


def _empty(msg: str) -> str:
    return _state("empty", msg)


def _needs(msg: str) -> str:
    """The widget is fine — it is waiting on a filter the NOC editor must set."""
    return _state("cfg", msg, "Select it in the widget's Filters panel")


# ── Shared HTML shell ─────────────────────────────────────────────────────────

def _widget_page(title: str, body: str) -> str:
    # Widget titles carry device/metric/subnet names chosen in the NOC editor
    # and read back from device data, and these pages render on an
    # unauthenticated display URL — escape before interpolating.
    title = html.escape(str(title))
    # A failed query leaves a body saying "nothing here" — which is a lie.
    _err = _WIDGET_ERR.get()
    if _err:
        body = _state("err", "Widget unavailable", _err)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#04060a;color:#e2e8f0;font-family:'Inter',system-ui,sans-serif;font-size:13px;height:100vh;overflow:hidden;display:flex;flex-direction:column}}
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
  .bar-label,.bar-lbl{{font-size:11px;color:#94a3b8;width:130px;flex-shrink:0;text-overflow:ellipsis;overflow:hidden;white-space:nowrap}}
  .bar-track,.bar-trk{{flex:1;background:#1e293b;border-radius:3px;height:8px;overflow:hidden}}
  .bar-fill{{height:8px;border-radius:3px;transition:width 0.3s}}
  .bar-val{{font-size:10px;color:#475569;width:70px;text-align:right;flex-shrink:0}}
  .hdr{{padding:8px 14px;border-bottom:1px solid #1e293b;display:flex;align-items:center;gap:8px;flex-shrink:0;height:36px}}
  .hdr-dot{{width:6px;height:6px;border-radius:50%;background:#818cf8;flex-shrink:0}}
  .hdr-title{{font-size:11px;font-weight:600;color:#94a3b8;letter-spacing:0.03em}}
  .tile-row{{display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap}}
  .tile{{flex:1;min-width:84px;background:#111827;border:1px solid #1e293b;border-radius:8px;padding:10px 12px}}
  .tile-label{{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px}}
  .tile-value{{font-size:22px;font-weight:700;color:#e2e8f0}}
  .chart-wrap{{width:100%;height:100%;min-height:90px;display:flex;flex-direction:column}}
  .chart-meta{{display:flex;gap:12px;font-size:10px;color:#475569;margin-bottom:6px;flex-wrap:wrap}}
  .chart-meta b{{color:#94a3b8;font-weight:600}}
  .chart-svg{{flex:1;width:100%;min-height:0}}
  .legend{{display:flex;gap:12px;font-size:10px;color:#94a3b8;margin-top:6px;flex-wrap:wrap}}
  .legend i{{width:8px;height:2px;display:inline-block;margin-right:4px;vertical-align:middle}}
  .state{{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;min-height:80px;text-align:center;padding:18px;gap:5px}}
  .state-icon{{font-size:17px;line-height:1;opacity:0.85}}
  .state-msg{{font-size:12px;font-weight:500}}
  .state-sub{{font-size:10px;color:#64748b;max-width:92%;word-break:break-word}}
  .state-empty{{color:#64748b}}
  .state-cfg{{color:#fbbf24}}
  .state-err{{color:#f87171}}
</style>
<script>setTimeout(()=>location.reload(),{_REFRESH.get() * 1000})</script>
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
    except Exception as exc:
        _note_err(exc)

    if rows:
        parts = []
        for r in rows:
            sev = _clamp_sev(r.get("severity", 7))
            label = _SEV_LABELS.get(sev, "INF")
            parts.append(
                f"<div class='log-row'>"
                f"<span class='sev sev-{sev}'>{label}</span>"
                f"<span class='ts'>{html.escape(_fmt_ts(r.get('ts','')))}</span>"
                f"<span class='host'>{html.escape(str(r.get('host') or '')[:20])}</span>"
                f"<span class='msg'>{html.escape(str(r.get('msg') or '')[:200])}</span>"
                f"</div>"
            )
        content = "".join(parts)
    else:
        content = _empty("No recent log entries")

    body = f"""<div class="header"><div style="width:6px;height:6px;border-radius:50%;background:#4ade80"></div><span class="header-title">Live Log Stream</span></div>
<div class="content" style="padding:0">{content}</div>"""
    return HTMLResponse(_widget_page("Log Stream", body))


# ── Error Rate widget ─────────────────────────────────────────────────────────

@router.get("/widgets/error_rate", response_class=HTMLResponse, include_in_schema=False)
async def widget_error_rate(hours: int = 24):
    """Event volume at each severity. Previously queried a `syslog_messages`
    table in SQLite that has never existed — syslog lives in the telemetry
    backend, so this reads it the way the app's own pages do."""
    hours = _hours(hours)
    st    = _storage()
    rows  = []
    if st:
        rows = await st.count_by_severity(hours=hours)

    if rows:
        palette = {0:"#f87171",1:"#f87171",2:"#f87171",3:"#fb923c",
                   4:"#fbbf24",5:"#4ade80",6:"#4ade80",7:"#94a3b8"}
        errs = sum(r.get("count") or 0 for r in rows if (r.get("severity") or 9) <= 3)
        tot  = sum(r.get("count") or 0 for r in rows)
        pct  = (errs / tot * 100) if tot else 0
        content = _tiles([("Events", f"{tot:,}"), ("Error+", f"{errs:,}"), ("Error rate", f"{pct:.1f}%")])
        content += "".join(
            _bars([(f"{_SEV_LABELS.get(r.get('severity'),'?')} {r.get('severity_name') or ''}".strip(),
                    r.get("count") or 0, f"{(r.get('count') or 0):,}")],
                  color=palette.get(r.get("severity"), "#818cf8"))
            for r in sorted(rows, key=lambda r: r.get("severity") or 0))
    else:
        content = _empty("No events in window")
    return HTMLResponse(_widget_page("Error Rate", _shell(f"Error Rate — last {hours}h", content)))


@router.get("/widgets/facility_breakdown", response_class=HTMLResponse, include_in_schema=False)
async def widget_facility_breakdown(hours: int = 24):
    """Volume per syslog facility. One bounded count per facility (0-23) against
    the telemetry backend — the old SQLite table it used never existed."""
    hours = _hours(hours)
    st    = _storage()
    if not st:
        return HTMLResponse(_widget_page("Facility Breakdown",
                                         _shell("Facility Breakdown", _empty("Storage backend unavailable"))))
    from datetime import datetime, timedelta, timezone
    start = datetime.now(timezone.utc) - timedelta(hours=hours)

    counts = []
    for fac in range(24):
        res = await st.search(start=start, facility=fac, limit=1)
        n = (res or {}).get("total") or 0
        if n:
            counts.append((FACILITY_NAMES.get(fac, str(fac)), n))
    counts.sort(key=lambda kv: -kv[1])

    content = _bars([(name, n, f"{n:,}") for name, n in counts[:20]]) \
        if counts else _empty("No events in window")
    return HTMLResponse(_widget_page("Facility Breakdown",
                                     _shell(f"Facility — last {hours}h", content)))


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
    except Exception as exc:
        _note_err(exc)

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
            rule = html.escape(str(r.get("rule_name") or "")[:24])
            msg = html.escape(str(r.get("msg") or "")[:80])
            trs.append(
                f"<tr>"
                f"<td style='font-size:10px;color:#475569'>{html.escape(_fmt_ts(r.get('ts','')))}</td>"
                f"<td><span class='badge' style='background:#1e293b;color:{color}'>{label}</span></td>"
                f"<td style='color:#60a5fa;font-size:11px'>{rule}</td>"
                f"<td style='color:#e2e8f0'>{msg}</td>"
                f"</tr>"
            )
        table = f"<table><thead><tr><th>Time</th><th>Sev</th><th>Source</th><th>Message</th></tr></thead><tbody>{''.join(trs)}</tbody></table>"
    else:
        table = _empty("No alert events in last 60 min")

    body = f"""<div class="header"><div style="width:6px;height:6px;border-radius:50%;background:#f87171"></div><span class="header-title">Alert Events</span></div>
<div class="content">{table}</div>"""
    return HTMLResponse(_widget_page("Alert Events", body))


# ── Log Sources ───────────────────────────────────────────────────────────────
@router.get("/widgets/log_sources", response_class=HTMLResponse, include_in_schema=False)
async def widget_log_sources(hours: int = 24):
    """Registered devices joined to what each has actually sent in the window."""
    hours   = _hours(hours)
    st      = _storage()
    sent    = {}
    if st:
        for r in await st.count_by_host(hours=hours, limit=5000):
            if r.get("source_ip"):
                sent[str(r["source_ip"])] = r.get("count") or 0
    devices = await _sqlite_rows(
        "SELECT ip, name, site FROM devices WHERE allowed = 1 ORDER BY name")

    if devices:
        trs = "".join(
            f"<tr><td>{html.escape(str(d['name']))}</td><td>{html.escape(str(d['ip']))}</td>"
            f"<td>{html.escape(str(d.get('site') or ''))}</td>"
            f"<td>{sent.get(str(d['ip']), 0):,}</td></tr>"
            for d in devices[:60])
        content = ("<table><thead><tr><th>Device</th><th>Address</th><th>Site</th>"
                   f"<th>Events ({hours}h)</th></tr></thead><tbody>{trs}</tbody></table>")
    else:
        content = _empty("No syslog sources registered")
    return HTMLResponse(_widget_page("Log Sources", _shell(f"Log Sources — last {hours}h", content)))


# ── Top Devices ───────────────────────────────────────────────────────────────
@router.get("/widgets/top_devices", response_class=HTMLResponse, include_in_schema=False)
async def widget_top_devices(hours: int = 1):
    """Highest-volume senders. Reads the telemetry backend rather than guessing
    at SQLite table names, which is what this did before and why it was blank."""
    hours = _hours(hours)
    st    = _storage()
    rows  = await st.count_by_host(hours=hours, limit=15) if st else []
    content = _bars([(r.get("source_name") or r.get("source_ip") or "—",
                      r.get("count") or 0, f"{(r.get('count') or 0):,}") for r in rows]) \
        if rows else _empty("No device activity in window")
    return HTMLResponse(_widget_page("Top Devices", _shell(f"Top Devices — last {hours}h", content)))


# ── Log Summary widget ────────────────────────────────────────────────────────
# ── Shared helpers for the storage-backed widgets ─────────────────────────────
# Event data lives in the pluggable telemetry backend (ClickHouse/DuckDB), not
# in SQLite — these read it the same way the app's own pages do.
def _hours(raw) -> int:
    try:
        return max(1, min(int(raw or 24), 720))
    except (TypeError, ValueError):
        return 24


def _shell(title: str, content: str, dot: str = "#818cf8") -> str:
    return (
        f"<div class='hdr'><div class='hdr-dot' style='background:{dot}'></div>"
        f"<span class='hdr-title'>{html.escape(title)}</span></div>"
        f"<div class='content'>{content}</div>"
    )


def _tiles(pairs) -> str:
    return "<div class='tile-row'>" + "".join(
        f"<div class='tile'><div class='tile-label'>{html.escape(str(label))}</div>"
        f"<div class='tile-value'>{html.escape(str(value))}</div></div>"
        for label, value in pairs
    ) + "</div>"


def _bars(rows, color: str = "#818cf8") -> str:
    """rows = [(label, numeric_value, display_value)] — scaled to the largest."""
    peak = max((r[1] or 0) for r in rows) if rows else 0
    return "".join(
        f"<div class='bar-row'><span class='bar-lbl' title='{html.escape(str(lbl))}'>{html.escape(str(lbl))}</span>"
        f"<div class='bar-trk'><div class='bar-fill' style='width:{(val / peak * 100) if peak else 0:.1f}%;background:{color}'></div></div>"
        f"<span class='bar-val'>{html.escape(str(disp))}</span></div>"
        for lbl, val, disp in rows
    )


_SERIES_COLORS = ("#818cf8", "#60a5fa", "#4ade80", "#f87171", "#fbbf24")


def _line_chart(series, height: int = 120) -> str:
    """series = [(label, [float, ...])] — equal-length samples, oldest first.

    Server-rendered inline SVG so the iframe stays dependency-free: pktLog ships
    no charting library to these views, and the NOC display must render without
    network access to anything but this app."""
    series = [(lbl, [v for v in vals if v is not None]) for lbl, vals in series]
    series = [(lbl, vals) for lbl, vals in series if len(vals) >= 2]
    if not series:
        return _empty("Not enough samples to plot")

    W, H, PAD = 600, height, 4
    lo = min(min(v) for _, v in series)
    hi = max(max(v) for _, v in series)
    span = (hi - lo) or 1.0

    def _y(v: float) -> float:
        return PAD + (H - 2 * PAD) * (1 - (v - lo) / span)

    paths, legend = [], []
    for i, (lbl, vals) in enumerate(series):
        color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
        step  = W / (len(vals) - 1)
        pts   = [(j * step, _y(v)) for j, v in enumerate(vals)]
        line  = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        area  = f"{line} L{W:.1f},{H} L0,{H} Z"
        paths.append(
            f'<path d="{area}" fill="{color}" opacity="0.10"/>'
            f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.5" '
            f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
        )
        legend.append(f"<span><i style='background:{color}'></i>{html.escape(str(lbl))} "
                      f"<b>{vals[-1]:,.0f}</b></span>")

    meta = (f"<div class='chart-meta'><span>min <b>{lo:,.0f}</b></span>"
            f"<span>peak <b>{hi:,.0f}</b></span>"
            f"<span>samples <b>{max(len(v) for _, v in series)}</b></span></div>")
    return (
        f"<div class='chart-wrap'>{meta}"
        f'<svg class="chart-svg" viewBox="0 0 {W} {H}" preserveAspectRatio="none" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(paths)}</svg>'
        f"<div class='legend'>{''.join(legend)}</div></div>"
    )


def _storage():
    """None when the backend has not initialised — widgets degrade to a message
    rather than surfacing a stack trace on the wall."""
    try:
        from app.storage.factory import get_storage
        return get_storage()
    except Exception as exc:
        _note_err(exc)
        return None


async def _sqlite_rows(sql: str, params: tuple = ()) -> list[dict]:
    try:
        async with aiosqlite.connect(str(_DB)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                return [dict(r) for r in await cur.fetchall()]
    except Exception as exc:
        _note_err(exc)
        return []


@router.get("/widgets/log_summary", response_class=HTMLResponse, include_in_schema=False)
async def widget_log_summary(hours: int = 24):
    hours = _hours(hours)
    st    = _storage()
    sev, hosts = [], []
    if st:
        try:
            sev = await st.count_by_severity(hours=hours)
        except Exception:
            sev = []
        try:
            hosts = await st.count_by_host(hours=hours, limit=1000)
        except Exception:
            hosts = []

    total  = sum(r.get("count") or 0 for r in sev)
    errors = sum(r.get("count") or 0 for r in sev if (r.get("severity") or 9) <= 3)
    warns  = sum(r.get("count") or 0 for r in sev if (r.get("severity") or 9) == 4)
    devices = await _sqlite_rows("SELECT COUNT(*) AS n FROM devices WHERE allowed = 1")

    content = _tiles([
        ("Events",   f"{total:,}"),
        ("Errors",   f"{errors:,}"),
        ("Warnings", f"{warns:,}"),
        ("Sending",  f"{len(hosts):,}"),
        ("Devices",  (devices[0]["n"] if devices else 0) or 0),
    ])
    return HTMLResponse(_widget_page(
        "Log Summary", _shell(f"Log Summary — last {hours}h", content)))


# ── Severity Breakdown widget ─────────────────────────────────────────────────
@router.get("/widgets/severity_breakdown", response_class=HTMLResponse, include_in_schema=False)
async def widget_severity_breakdown(hours: int = 24):
    hours = _hours(hours)
    st    = _storage()
    rows  = []
    if st:
        try:
            rows = await st.count_by_severity(hours=hours)
        except Exception as exc:
            _note_err(exc)
            rows = []

    if rows:
        # Colour each bar by its own severity so the shape reads at a glance.
        palette = {0: "#f87171", 1: "#f87171", 2: "#f87171", 3: "#fb923c",
                   4: "#fbbf24", 5: "#4ade80", 6: "#4ade80", 7: "#94a3b8"}
        content = "".join(
            _bars([(f"{_SEV_LABELS.get(r.get('severity'), '?')} "
                    f"{r.get('severity_name') or ''}".strip(),
                    r.get("count") or 0, f"{(r.get('count') or 0):,}")],
                  color=palette.get(r.get("severity"), "#818cf8"))
            for r in sorted(rows, key=lambda r: r.get("severity") or 0)
        )
    else:
        content = _empty("No events in window")
    return HTMLResponse(_widget_page(
        "Severity Breakdown", _shell(f"Severity — last {hours}h", content)))


# ── Alert Summary widget ──────────────────────────────────────────────────────
@router.get("/widgets/alert_summary", response_class=HTMLResponse, include_in_schema=False)
async def widget_alert_summary():
    rows   = await _sqlite_rows(
        "SELECT LOWER(severity) AS sev, COUNT(*) AS n FROM alert_events "
        "WHERE resolved_at IS NULL GROUP BY sev"
    )
    counts = {r["sev"]: r["n"] for r in rows}
    content = _tiles([
        ("Active",   sum(counts.values())),
        ("Critical", counts.get("critical", 0)),
        ("Warning",  counts.get("warning", 0)),
        ("Info",     counts.get("info", 0)),
    ])
    return HTMLResponse(_widget_page("Alert Summary", _shell("Alert Summary", content)))


# ── Ingest Trend widget (chart) ───────────────────────────────────────────────
@router.get("/widgets/ingest_trend", response_class=HTMLResponse, include_in_schema=False)
async def widget_ingest_trend(hours: int = 24):
    hours  = _hours(hours)
    bucket = 1 if hours <= 1 else (5 if hours <= 6 else (15 if hours <= 24 else 60))
    st     = _storage()
    rows   = []
    if st:
        try:
            rows = await st.timeseries(hours=hours, bucket_minutes=bucket)
        except Exception as exc:
            _note_err(exc)
            rows = []

    content = _line_chart([("Events", [r.get("count") for r in rows])])
    return HTMLResponse(_widget_page(
        "Ingest Trend", _shell(f"Ingest — last {hours}h", content)))


# ── Top Programs widget ───────────────────────────────────────────────────────
@router.get("/widgets/top_programs", response_class=HTMLResponse, include_in_schema=False)
async def widget_top_programs(hours: int = 24):
    hours = _hours(hours)
    st    = _storage()
    rows  = []
    if st:
        try:
            rows = await st.top_programs(hours=hours, limit=20)
        except Exception as exc:
            _note_err(exc)
            rows = []

    if rows:
        content = _bars([
            (r.get("program") or r.get("tag") or "—",
             r.get("count") or 0, f"{(r.get('count') or 0):,}")
            for r in rows
        ])
    else:
        content = _empty("No program activity in window")
    return HTMLResponse(_widget_page(
        "Top Programs", _shell(f"Top Programs — last {hours}h", content)))


# ── Top Hosts widget ──────────────────────────────────────────────────────────
@router.get("/widgets/top_hosts", response_class=HTMLResponse, include_in_schema=False)
async def widget_top_hosts(hours: int = 24):
    hours = _hours(hours)
    st    = _storage()
    rows  = []
    if st:
        try:
            rows = await st.count_by_host(hours=hours, limit=25)
        except Exception as exc:
            _note_err(exc)
            rows = []

    if rows:
        content = _bars([
            (r.get("source_name") or r.get("source_ip") or "—",
             r.get("count") or 0, f"{(r.get('count') or 0):,}")
            for r in rows
        ])
    else:
        content = _empty("No host activity in window")
    return HTMLResponse(_widget_page(
        "Top Hosts", _shell(f"Top Hosts — last {hours}h", content)))


# ── Silent Sources widget ─────────────────────────────────────────────────────
@router.get("/widgets/silent_sources", response_class=HTMLResponse, include_in_schema=False)
async def widget_silent_sources(hours: int = 24):
    hours = _hours(hours)
    st    = _storage()
    seen  = set()
    if st:
        try:
            for r in await st.count_by_host(hours=hours, limit=5000):
                if r.get("source_ip"):
                    seen.add(str(r["source_ip"]))
        except Exception as exc:
            _note_err(exc)
            seen = set()

    devices = await _sqlite_rows(
        "SELECT ip, name, site FROM devices WHERE allowed = 1 ORDER BY name"
    )
    silent = [d for d in devices if str(d["ip"]) not in seen]

    if silent:
        trs = "".join(
            f"<tr><td>{html.escape(str(d['name']))}</td><td>{html.escape(str(d['ip']))}</td>"
            f"<td>{html.escape(str(d.get('site') or ''))}</td></tr>"
            for d in silent[:50]
        )
        content = ("<table><thead><tr><th>Device</th><th>Address</th><th>Site</th></tr></thead>"
                   f"<tbody>{trs}</tbody></table>")
    elif devices:
        content = _empty("Every registered device is sending")
    else:
        content = _empty("No syslog sources registered")
    return HTMLResponse(_widget_page(
        "Silent Sources", _shell(f"Silent Sources — last {hours}h", content)))


# ── Collector Status widget ───────────────────────────────────────────────────
@router.get("/widgets/collector_status", response_class=HTMLResponse, include_in_schema=False)
async def widget_collector_status():
    st   = _storage()
    last = {}
    if st:
        try:
            for r in await st.collector_last_seen():
                key = r.get("collector") or r.get("collector_id") or r.get("name")
                if key is not None:
                    last[str(key)] = r.get("last_seen") or r.get("last_received")
        except Exception as exc:
            _note_err(exc)
            last = {}

    rows = await _sqlite_rows(
        "SELECT collector_name AS name, collector_ip AS hostname, site, enabled "
        "FROM collector_registry ORDER BY collector_name")

    if rows:
        trs = "".join(
            f"<tr><td>{html.escape(str(r.get('name') or ''))}</td>"
            f"<td>{html.escape(str(r.get('hostname') or ''))}</td>"
            f"<td>{html.escape(_fmt_ts(last.get(str(r.get('name'))) or ''))}</td></tr>"
            for r in rows
        )
        content = ("<table><thead><tr><th>Collector</th><th>Host</th><th>Last Event</th></tr></thead>"
                   f"<tbody>{trs}</tbody></table>")
    elif last:
        trs = "".join(
            f"<tr><td>{html.escape(str(k))}</td><td>—</td><td>{html.escape(_fmt_ts(v or ''))}</td></tr>"
            for k, v in sorted(last.items())
        )
        content = ("<table><thead><tr><th>Collector</th><th>Host</th><th>Last Event</th></tr></thead>"
                   f"<tbody>{trs}</tbody></table>")
    else:
        content = _empty("No collectors registered")
    return HTMLResponse(_widget_page("Collector Status", _shell("Collector Status", content)))
