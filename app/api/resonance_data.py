"""
app/api/resonance_data.py — the data half of the resonance contract.

app/api/resonance.py mounts the panel. This module is what the panel is
allowed to *read* once it is mounted, and it exists because the embed contract
has three parts and mounting only satisfies one of them:

  1. an OpenAPI document at a stable same-origin path      -> /api/resonance/openapi.json
  2. a grant file naming what may be called                -> /.well-known/resonance.json
  3. endpoints that behave: bounded, JSON, stable fields   -> /api/resonance/data/*

Why a separate surface rather than granting against /api/syslog/* directly.
The operations named in a grant have to carry a stable operationId, prose a
stranger can choose between, enums for every fixed vocabulary, a declared
response schema, and a bounded page with a total. pktlog's own endpoints were
written for a SPA that already knows all of that: several return a bare array,
one caps at 500 rows with no way to ask for fewer, and their parameters are
typed but not described. Retrofitting the contract onto them would change
response shapes the frontend already consumes. These wrap the same storage
calls and the same tables instead, so there is no second implementation of any
query — only a second, narrower doorway with the labels the model needs.

Authentication is the app's existing session, not a new one. The panel's calls
are ordinary same-origin fetches from our own page, so they carry the refresh
cookie exactly as /api/resonance/code does, and they are admitted by the same
helpers that admit /code — see resonance_session_user below. Nothing here
issues, accepts or understands a credential of resonance's, and the panel can
therefore only ever read what the signed-in person could already read.

Read-only by design. Nothing in this module writes. The grant format marks a
state-changing operation `writes: true` and resonance will not run one without
spoken confirmation, so adding one later is a one-line change to GRANTED plus
the operation itself — but no such operation is exposed today.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import ResponseValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.database import get_db
from app.models.syslog import FACILITY_NAMES, SEVERITY_NAMES
from app.storage.factory import get_storage

# Deliberately the same helpers /api/resonance/code uses, imported rather than
# reimplemented: the two surfaces must never disagree about who counts as
# signed in, which origin counts as ours, or whether the feature is on.
from app.api.resonance import (
    LEVEL_RANK, _allowed_roles, _get, _same_origin, _user_for_code, role_level,
)
from app.dependencies import require_admin, require_analyst

log = logging.getLogger("pktlog.api.resonance_data")

router = APIRouter(tags=["resonance-data"])

DATA_PREFIX = "/api/resonance/data"
SPEC_PATH = "/api/resonance/openapi.json"
GRANT_PATH = "/.well-known/resonance.json"


# ── What the assistant is allowed to call ────────────────────────────────────
#
# The one list. The grant file is generated from it, the published spec is
# filtered to it, and startup checks it against the routes that actually exist.
# An operationId that is not here is invisible to the assistant even though it
# is a perfectly ordinary route of this app.


@dataclass(frozen=True)
class Grant:
    op: str
    # Set on ANY operation that changes state, whatever its HTTP verb.
    # Resonance reads the values back to the person before running one.
    writes: bool = False


GRANTED: tuple[Grant, ...] = (
    Grant("searchSyslog"),
    Grant("getSyslogSummary"),
    Grant("getSyslogTimeline"),
    Grant("listCollectors"),
    Grant("listPendingCollectors"),
    Grant("listAlertEvents"),
    Grant("listAlertRules"),
    Grant("searchApplicationLog"),
    # Everything below changes state. Deliberately no delete of anything, and
    # no authoring of configuration: an assistant may act on what is already
    # there — acknowledge, enable, admit — and never invent or destroy it.
    Grant("ackAlertEvent", writes=True),
    Grant("ackAllAlertEvents", writes=True),
    Grant("toggleAlertRule", writes=True),
    Grant("approveCollector", writes=True),
    Grant("ignoreCollector", writes=True),
)


# ── Vocabulary ────────────────────────────────────────────────────────────────
#
# These are the enums the requirement is really about: without them a model
# asks for severity "high" and facility "firewall", gets a 422, and reports the
# app as broken. Both sets are RFC 5424's, which is why they can be fixed here
# at all — the install-specific vocabulary (collector names, orgs, log groups,
# sites) cannot be, and is published through listCollectors instead.

SeverityName = Literal[
    "emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"
]
FacilityName = Literal[
    "kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news",
    "uucp", "cron", "authpriv", "ftp", "ntp", "audit", "alert", "clock",
    "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7",
]
AlertSeverity = Literal["info", "warning", "critical"]
AlertRuleType = Literal[
    "data_gap", "new_host", "threshold", "rate_spike",
    "top_talker", "ingest_rate_low", "clickhouse_size",
]
AppLogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
MatchMode = Literal["contains", "prefix"]

_SEVERITY_CODES = {name: code for code, name in SEVERITY_NAMES.items()}
_FACILITY_CODES = {name: code for code, name in FACILITY_NAMES.items()}

# Spelled into the parameter descriptions so the ordering is never a guess.
_SEVERITY_ORDER = ", ".join(f"{code}={name}" for code, name in sorted(SEVERITY_NAMES.items()))


# ── Errors ────────────────────────────────────────────────────────────────────


class ResonanceDataError(HTTPException):
    """Rendered as {"error": "..."} — the message reaches the person verbatim."""


class ErrorResponse(BaseModel):
    error: str = Field(description="What went wrong, phrased for the person to act on.")


def register_error_handler(app) -> None:
    """Give this surface the {"error": ...} body the grant contract specifies.

    Scoped to ResonanceDataError so the rest of the app keeps FastAPI's
    {"detail": ...}, which its own frontend already reads.
    """

    @app.exception_handler(ResonanceDataError)
    async def _render(_request: Request, exc: ResonanceDataError):  # noqa: ANN202
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(ResponseValidationError)
    async def _schema_drifted(request: Request, exc: ResponseValidationError):  # noqa: ANN202
        """Report a declared schema that no longer matches what the store returns.

        This fires after the route body has already succeeded, so the module's
        own try/except cannot see it, and it is logged by uvicorn rather than by
        anything the SQLite handler is attached to. The result was a 500 with a
        generic message in the panel and not one line anywhere on the server —
        which is how one wrong field type (pid, declared int, stored as a
        string) cost a round trip to diagnose. Now it names the fields.

        Only this surface is rewritten; every other response_model in the app
        keeps FastAPI's existing behaviour.
        """
        if not request.url.path.startswith("/api/resonance/"):
            raise exc
        fields = sorted({".".join(str(p) for p in err.get("loc", ())[-2:])
                         for err in exc.errors()})[:8]
        log.error(
            "resonance response schema no longer matches the data on %s: %s — "
            "run scripts/resonance_schema_check.py",
            request.url.path, ", ".join(fields) or "unknown field",
        )
        return JSONResponse(
            {"error": "pktLog produced a result it could not describe. This is a fault in pktLog, "
                      "not in the question — it has been logged."},
            status_code=500,
        )


_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "No signed-in session on this request."},
    403: {"model": ErrorResponse, "description": "Signed in, but not permitted to use the assistant."},
    404: {"model": ErrorResponse, "description": "The assistant is switched off on this install."},
    504: {"model": ErrorResponse, "description": "The log store did not answer in time; ask something narrower."},
}


# ── Session ───────────────────────────────────────────────────────────────────


async def resonance_session_user(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
) -> dict:
    """Admit a call the panel made from our own page, on this app's own session.

    Same four gates as /api/resonance/code, in the same order and for the same
    reasons: the request must present as same-origin before any cookie is
    honoured, it must carry a session we recognise, the feature must be on, and
    the person's role must be one an admin listed. The last two mean this whole
    surface is inert on an install that never enabled the panel — a route that
    exists but answers 404 until someone turns the feature on deliberately.
    """
    if not _same_origin(request):
        raise ResonanceDataError(status_code=403, detail="Cross-site request refused.")

    user = await _user_for_code(request, db)
    if not user:
        raise ResonanceDataError(status_code=401, detail="Not signed in to pktLog.")

    if not bool(await _get(db, "resonance_enabled", False)):
        raise ResonanceDataError(status_code=404, detail="The assistant is not enabled on this install.")

    if user["role"] not in await _allowed_roles(db):
        raise ResonanceDataError(
            status_code=403, detail="Your role is not permitted to use the assistant."
        )

    # Audit trail, and the only way to answer "did the assistant actually ask us
    # anything". uvicorn runs with access_log=False and a successful read is
    # otherwise silent, so without this the difference between "the panel never
    # called" and "the panel called and got what it wanted" is invisible from
    # the server — which is exactly the question asked when an answer looks
    # wrong. One line per call, at INFO, so it lands in the Logs page too.
    route = request.scope.get("route")
    log.info(
        "resonance call: %s (%s) -> %s",
        user.get("username"), user.get("role"),
        getattr(route, "operation_id", None) or request.url.path,
    )
    return user


async def resonance_write_user(
    request: Request, db: aiosqlite.Connection = Depends(get_db)
) -> dict:
    """As above, and the role must be set to "write" rather than "read".

    Two gates have to agree before anything changes, and they answer different
    questions. This one is the admin's: has this role been trusted to let the
    assistant act at all. The second, inside each operation, is pktLog's own:
    may this person do this thing anyway. A role set to "write" never gains a
    right its holder does not already have in the interface — it only decides
    whether the assistant may exercise the rights they do have.
    """
    user = await resonance_session_user(request, db)
    if LEVEL_RANK.get(await role_level(db, user["role"]), 0) < LEVEL_RANK["write"]:
        raise ResonanceDataError(
            status_code=403,
            detail=("The assistant is set to read-only for your role, so it cannot make "
                    "that change. An administrator sets this under Settings → Resonance."),
        )
    return user


async def _apply_app_rule(user: dict, rule, what: str) -> None:
    """Apply pktLog's own role rule for the endpoint this operation mirrors.

    The rule itself is imported rather than restated, so a change to who may do
    something in the interface reaches the assistant in the same commit instead
    of leaving two role models to drift apart.
    """
    try:
        await rule(user)
    except HTTPException as exc:
        raise ResonanceDataError(
            status_code=exc.status_code,
            detail=f"Your pktLog role does not permit you to {what}.",
        ) from exc


SessionUser = Depends(resonance_session_user)
WriteUser = Depends(resonance_write_user)


# ── Response schemas ──────────────────────────────────────────────────────────
#
# Declared because resonance validates a result against the schema before the
# model is allowed to read it, so a shape it cannot describe is a shape it
# refuses. `extra="allow"` throughout: these describe rows that grow columns
# over time, and a new column should reach the answer rather than be stripped
# out of it by a schema written before it existed.


class SyslogRecord(BaseModel):
    """One ingested syslog event."""

    model_config = ConfigDict(extra="allow")

    timestamp: Optional[str] = Field(None, description="When the device says the event happened (ISO 8601, UTC).")
    received_at: Optional[str] = Field(None, description="When this install received it (ISO 8601, UTC).")
    source_ip: Optional[str] = Field(None, description="Address of the device that emitted the line.")
    source_name: Optional[str] = Field(None, description="Friendly name for the source device, when known.")
    dest_ip: Optional[str] = Field(None, description="Destination address parsed out of the message body, when it carries one.")
    facility: Optional[int] = Field(None, description="RFC 5424 facility code, 0-23.")
    facility_name: Optional[str] = Field(None, description="Facility as a name, e.g. auth, daemon, local4.")
    severity: Optional[int] = Field(None, description="RFC 5424 severity code, 0 (most severe) to 7 (least).")
    severity_name: Optional[str] = Field(None, description="Severity as a name, e.g. error, warning, info.")
    program: Optional[str] = Field(None, description="Program or tag the device reported, e.g. sshd, kernel.")
    # A string, not a number, in both storage backends (ClickHouse String,
    # DuckDB VARCHAR) and empty rather than null when the line carried none.
    # Declared as int here originally, from the ingest dataclass rather than
    # from what the store actually returns, which made every search fail
    # response validation on the first record.
    pid: Optional[str] = Field(
        None, description="Process id exactly as the device reported it. Empty when the line carried none."
    )
    message: Optional[str] = Field(None, description="The message text, with the syslog header removed.")
    raw: Optional[str] = Field(None, description="The line exactly as it arrived.")
    collector_ip: Optional[str] = Field(None, description="Address of the collector that forwarded this event to pktLog.")
    collector_name: Optional[str] = Field(None, description="Name that collector is registered under.")
    org: Optional[str] = Field(None, description="Organisation the collector is filed under.")
    log_group: Optional[str] = Field(None, description="Log group the collector is filed under.")
    site: Optional[str] = Field(None, description="Site the collector is filed under.")


class SyslogSearchResult(BaseModel):
    """A page of matching events, plus how many matched in total."""

    model_config = ConfigDict(extra="allow")

    total: int = Field(description="How many events matched the filters, ignoring limit and offset.")
    limit: int = Field(description="How many were asked for.")
    offset: int = Field(description="How many were skipped before this page.")
    returned: int = Field(description="How many are in this response. Below `limit` when the page was trimmed to fit.")
    truncated_for_size: bool = Field(
        description="True when entries that matched were dropped from this page to keep the "
                    "response inside the size a conversation can carry. Ask something narrower, "
                    "or page with offset — nothing was lost from `total`."
    )
    records: list[SyslogRecord] = Field(description="The matching events, most recent first.")


class SeverityCount(BaseModel):
    model_config = ConfigDict(extra="allow")
    severity: Optional[int] = Field(None, description="RFC 5424 severity code.")
    severity_name: Optional[str] = Field(None, description="Severity as a name.")
    count: Optional[int] = Field(None, description="Events at this severity in the window.")


class HostCount(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_ip: Optional[str] = None
    source_name: Optional[str] = None
    log_group: Optional[str] = None
    count: Optional[int] = Field(None, description="Events from this host in the window.")


class ProgramCount(BaseModel):
    model_config = ConfigDict(extra="allow")
    program: Optional[str] = None
    count: Optional[int] = Field(None, description="Events from this program in the window.")


class CollectorLastSeen(BaseModel):
    model_config = ConfigDict(extra="allow")
    collector_ip: Optional[str] = None
    collector_name: Optional[str] = None
    last_seen: Optional[str] = Field(
        None, description="When this collector last delivered an event, or null if it never has."
    )


class SyslogSummary(BaseModel):
    """Everything the dashboard shows, for one lookback window."""

    model_config = ConfigDict(extra="allow")

    hours: int = Field(description="The window these figures cover, in hours back from now.")
    count_by_severity: list[SeverityCount] = Field(description="Event counts grouped by severity.")
    top_hosts: list[HostCount] = Field(description="Busiest source devices in the window.")
    top_programs: list[ProgramCount] = Field(description="Busiest programs in the window.")
    collector_last_seen: list[CollectorLastSeen] = Field(
        description="Enabled collectors and when each last delivered anything, soonest-silent "
                    "concerns first. A null last_seen means it is registered but has never been "
                    "heard from. At most collector_limit of them."
    )
    collector_total: int = Field(
        description="How many enabled collectors there are in total. Larger than the length of "
                    "collector_last_seen when collector_limit cut the list short."
    )


class TimelineBucket(BaseModel):
    model_config = ConfigDict(extra="allow")
    bucket: Optional[str] = Field(None, description="Start of the interval (ISO 8601).")
    count: Optional[int] = Field(None, description="Events in the interval.")


class SyslogTimeline(BaseModel):
    """Event volume over time, already bucketed."""

    model_config = ConfigDict(extra="allow")

    hours: int = Field(description="Window covered, in hours back from now.")
    bucket_minutes: int = Field(description="Width of each interval, in minutes.")
    points: int = Field(description="How many intervals are in buckets.")
    buckets: list[TimelineBucket] = Field(description="Intervals in ascending time order.")


class Collector(BaseModel):
    """A registered collector, and the hierarchy its events are filed under."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    collector_ip: Optional[str] = Field(None, description="Address pktLog receives this collector's events from.")
    collector_name: Optional[str] = Field(None, description="Name this collector is registered under.")
    org: Optional[str] = Field(None, description="Organisation value stamped onto its events.")
    log_group: Optional[str] = Field(None, description="Log group value stamped onto its events.")
    site: Optional[str] = Field(None, description="Site value stamped onto its events.")
    notes: Optional[str] = None
    enabled: Optional[bool] = Field(None, description="Whether events from this collector are accepted.")
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CollectorList(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = Field(description="How many collectors matched, ignoring limit and offset.")
    limit: int
    offset: int
    returned: int = Field(description="How many are in this response. Below `limit` when the page was trimmed to fit.")
    truncated_for_size: bool = Field(
        description="True when entries that matched were dropped from this page to keep the "
                    "response inside the size a conversation can carry. Ask something narrower, "
                    "or page with offset — nothing was lost from `total`."
    )
    collectors: list[Collector]


class AlertEvent(BaseModel):
    """One firing of an alert rule."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    rule_id: Optional[int] = None
    rule_name: Optional[str] = Field(None, description="Name of the rule that fired.")
    severity: Optional[str] = Field(None, description="info, warning or critical.")
    message: Optional[str] = Field(None, description="What the rule said when it fired.")
    details: Optional[dict] = Field(None, description="Rule-specific context, shape varies by rule type.")
    fired_at: Optional[str] = Field(None, description="When it fired (ISO 8601, UTC).")
    acked_at: Optional[str] = Field(None, description="When someone acknowledged it, or null if nobody has.")
    acked_by: Optional[int] = Field(None, description="Id of the user who acknowledged it.")
    resolved_at: Optional[str] = Field(None, description="When the condition cleared, or null if it has not.")
    auto_resolved: Optional[bool] = Field(None, description="Whether it cleared on its own rather than by hand.")


class AlertEventList(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = Field(description="How many events matched, ignoring limit and offset.")
    limit: int
    offset: int
    returned: int = Field(description="How many are in this response. Below `limit` when the page was trimmed to fit.")
    truncated_for_size: bool = Field(
        description="True when entries that matched were dropped from this page to keep the "
                    "response inside the size a conversation can carry. Ask something narrower, "
                    "or page with offset — nothing was lost from `total`."
    )
    events: list[AlertEvent] = Field(description="Matching events, most recently fired first.")


class AlertRule(BaseModel):
    """A configured alert rule."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    rule_type: Optional[str] = Field(None, description="Which condition the rule watches for.")
    conditions: Optional[dict] = Field(None, description="Parameters for that condition, shape varies by rule type.")
    time_window_min: Optional[int] = Field(None, description="Window the condition is evaluated over, in minutes.")
    severity: Optional[str] = Field(None, description="Severity events from this rule are raised at.")
    channels: Optional[list[str]] = Field(None, description="Where notifications are sent.")
    cooldown_min: Optional[int] = Field(None, description="Minimum gap between firings, in minutes.")
    last_fired: Optional[str] = Field(None, description="When this rule last fired, or null if it never has.")
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AlertRuleList(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = Field(description="How many rules matched, ignoring limit and offset.")
    limit: int
    offset: int
    returned: int = Field(description="How many are in this response. Below `limit` when the page was trimmed to fit.")
    truncated_for_size: bool = Field(
        description="True when entries that matched were dropped from this page to keep the "
                    "response inside the size a conversation can carry. Ask something narrower, "
                    "or page with offset — nothing was lost from `total`."
    )
    rules: list[AlertRule]


class AppLogRecord(BaseModel):
    """One line from pktLog's own application log."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    ts: Optional[str] = Field(None, description="When it was logged (ISO 8601).")
    level: Optional[str] = Field(None, description="DEBUG, INFO, WARNING, ERROR or CRITICAL.")
    level_no: Optional[int] = Field(None, description="Numeric level; higher is more severe.")
    logger: Optional[str] = Field(None, description="Which part of pktLog logged it, e.g. pktlog.ingest.listener.")
    message: Optional[str] = None
    exc_info: Optional[str] = Field(
        None,
        description="Traceback, when the line carried one. Long ones keep the tail — where the "
                    "exception itself is — behind a leading truncation marker.",
    )


class AppLogResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = Field(description="How many lines matched, ignoring limit and offset.")
    limit: int
    offset: int
    returned: int = Field(description="How many are in this response. Below `limit` when the page was trimmed to fit.")
    truncated_for_size: bool = Field(
        description="True when entries that matched were dropped from this page to keep the "
                    "response inside the size a conversation can carry. Ask something narrower, "
                    "or page with offset — nothing was lost from `total`."
    )
    records: list[AppLogRecord] = Field(description="Matching lines, most recent first.")


# ── Operations ────────────────────────────────────────────────────────────────
#
# Every summary and description here is written for a reader who has never seen
# pktLog, because that is literally what chooses between them: a model picks an
# operation from these sentences and nothing else. "Search logs" would leave it
# guessing between syslog events and the app's own diagnostics, which are two
# entirely different questions asked with almost the same words.

# One page is capped well below what the SPA allows. The panel's results are
# read back to a person in a conversation, so a hundred rows is already past
# the point of being an answer, and a model handed a thousand narrows nothing.
# Measured against a live store at ~537 bytes per syslog event: 25 rows is about
# 13 KB and lands inside the budget without trimming, which is where a default
# belongs. The maxima are deliberately above what always fits — _fit() reports
# the cut, and a caller that wants density should be able to ask for it.
_SEARCH_DEFAULT, _SEARCH_MAX = 25, 100
_LIST_DEFAULT, _LIST_MAX = 50, 200

# hours=720 with bucket_minutes=1 is 43,200 intervals. Bounded here rather than
# left to the caller, because an unbounded chart is the one way a read-only
# endpoint can still take an install down. The ceiling is set by bytes, not
# taste: measured against a live store, an interval costs about 52 bytes, so
# 300 of them sit inside the result budget below with room to spare.
_TIMELINE_MAX_POINTS = 300

# Resonance truncates a result over 20 KB and tells the model it did. That turns
# a clean page into JSON that stops mid-record, so the cut is made here instead,
# where it can leave the envelope intact and say what happened in a field the
# model can act on. 18 KB leaves headroom for transport framing.
#
# This is not theoretical: a page of 50 syslog events measured 26.8 KB against a
# live store — every default search would have been mangled on arrival.
_RESULT_BUDGET_BYTES = 18_000

# Resonance gives up on a call after 20 seconds and tells the person the
# application did not answer. Answering at 15 with something they can act on
# beats going quiet at 20.
_CALL_TIMEOUT_SECONDS = 15

# Enough of a traceback to identify the failure without letting one line eat a page.
_TRACEBACK_TAIL_CHARS = 1200


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def _fit(payload: dict, items_key: str) -> dict:
    """Trim a page to the byte budget, and record that it had to.

    Always keeps at least one item: an empty page for one oversized record is a
    worse answer than an oversized one, and the caller can still see `total`.
    """
    items = list(payload.get(items_key) or [])
    # Price the envelope with the two fields this adds, so adding them cannot
    # push a result that just fitted back over the line.
    envelope = dict(payload)
    envelope[items_key] = []
    envelope["returned"] = len(items)
    envelope["truncated_for_size"] = True
    budget = _RESULT_BUDGET_BYTES - _encoded_size(envelope)

    kept: list = []
    used = 0
    for item in items:
        size = _encoded_size(item) + 1   # + the separating comma
        if kept and used + size > budget:
            break
        kept.append(item)
        used += size

    payload[items_key] = kept
    payload["returned"] = len(kept)
    payload["truncated_for_size"] = len(kept) < len(items)
    return payload


async def _in_time(awaitable, what: str):
    """Bound a store query so a slow one is answered rather than abandoned."""
    try:
        return await asyncio.wait_for(awaitable, _CALL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise ResonanceDataError(
            status_code=504,
            detail=(
                f"The log store took longer than {_CALL_TIMEOUT_SECONDS} seconds to {what}. "
                "Narrow the time range, or filter by collector, program or severity."
            ),
        ) from exc


@router.get(
    f"{DATA_PREFIX}/syslog/search",
    operation_id="searchSyslog",
    summary="Search collected syslog events",
    description=(
        "Search the syslog events pktLog has collected from network devices, newest first. "
        "This is the log data itself — the individual lines devices sent — not pktLog's own "
        "diagnostics, which are in searchApplicationLog. Every filter is optional and they "
        "combine with AND; with no filters at all it returns the most recent events from "
        "everything. Returns at most `limit` events plus the total number that matched, so a "
        "search matching forty thousand lines answers with a page and says forty thousand. A page "
        "is also trimmed to fit the size a conversation can carry, and sets truncated_for_size "
        "when it was — narrow the filters rather than raising `limit` when that happens. "
        "Collector names, organisations, log groups and sites differ on every install — read "
        "the real values from listCollectors rather than guessing at them."
    ),
    response_model=SyslogSearchResult,
    responses={**_ERRORS, 400: {"model": ErrorResponse, "description": "The query could not be run."}},
)
async def search_syslog(
    _user: dict = SessionUser,
    start: Optional[datetime] = Query(
        None, description="Only events at or after this time. ISO 8601, e.g. 2026-08-23T09:00:00Z."
    ),
    end: Optional[datetime] = Query(
        None, description="Only events at or before this time. ISO 8601."
    ),
    source_ip: Optional[str] = Query(None, description="Address of the device that emitted the event."),
    dest_ip: Optional[str] = Query(
        None, description="Destination address parsed from the message body, where one is present."
    ),
    collector_ip: Optional[str] = Query(None, description="Address of the collector that forwarded the events."),
    collector_name: Optional[str] = Query(
        None, description="Name of the collector. Valid names come from listCollectors."
    ),
    org: Optional[str] = Query(
        None, description="Organisation the collector is filed under. Valid values come from listCollectors."
    ),
    log_group: Optional[str] = Query(
        None, description="Log group the collector is filed under. Valid values come from listCollectors."
    ),
    site: Optional[str] = Query(
        None, description="Site the collector is filed under. Valid values come from listCollectors."
    ),
    severity_at_least: Optional[SeverityName] = Query(
        None,
        description=(
            "Only events this severe or worse. Syslog counts down, so 'error' returns errors, "
            f"criticals, alerts and emergencies, but no warnings. Codes are {_SEVERITY_ORDER}."
        ),
    ),
    facility: Optional[FacilityName] = Query(
        None, description="Only events from this RFC 5424 facility."
    ),
    program: Optional[str] = Query(
        None, description="Program or tag the device reported, e.g. sshd, kernel, dhcpd."
    ),
    q: Optional[str] = Query(
        None, description="Free text to find inside the message body. Case-insensitive, matches anywhere."
    ),
    match_mode: MatchMode = Query(
        "contains",
        description=(
            "How the address, name and program filters match: 'contains' finds the term anywhere "
            "in the value, 'prefix' anchors it to the start. Both ignore case. Does not affect `q`, "
            "which always matches anywhere."
        ),
    ),
    limit: int = Query(
        _SEARCH_DEFAULT, ge=1, le=_SEARCH_MAX,
        description=f"How many events to return. Default {_SEARCH_DEFAULT}, maximum {_SEARCH_MAX}.",
    ),
    offset: int = Query(0, ge=0, description="How many events to skip, for paging through a large result."),
):
    try:
        result = await _in_time(get_storage().search(
            start=start,
            end=end,
            source_ip=source_ip,
            dest_ip=dest_ip,
            collector_ip=collector_ip,
            collector_name=collector_name,
            org=org,
            log_group=log_group,
            site=site,
            severity_max=_SEVERITY_CODES[severity_at_least] if severity_at_least else None,
            facility=_FACILITY_CODES[facility] if facility else None,
            program=program,
            search=q,
            match_mode=match_mode,
            limit=limit,
            offset=offset,
        ), "run that search")
    except ResonanceDataError:
        raise
    except Exception as exc:
        # The storage backend's own message is not fit to read aloud and can
        # name internals; log it, answer with something the person can act on.
        log.warning("resonance syslog search failed: %s", exc)
        raise ResonanceDataError(
            status_code=400, detail="The log store could not run that search. Try a narrower time range."
        ) from exc

    return _fit(result, "records")


@router.get(
    f"{DATA_PREFIX}/syslog/summary",
    operation_id="getSyslogSummary",
    summary="Summarise syslog activity over a recent window",
    description=(
        "Answer 'what is going on right now' in one call: how many events arrived at each "
        "severity, which devices and which programs produced the most, and when each registered "
        "collector was last heard from. Covers the last `hours` hours. Use this before searching — "
        "it shows which sources and programs actually exist on this install, and a collector with "
        "a null last_seen is registered but silent, which is usually the thing being asked about. "
        "The host and program lists are the top 20 of each; the collector list is capped by "
        "`collector_limit` and reports the full count alongside it."
    ),
    response_model=SyslogSummary,
    responses={**_ERRORS, 400: {"model": ErrorResponse, "description": "The summary could not be built."}},
)
async def get_syslog_summary(
    _user: dict = SessionUser,
    hours: int = Query(
        24, ge=1, le=720,
        description="How far back to look, in hours. Default 24, maximum 720 (30 days).",
    ),
    collector_limit: int = Query(
        _LIST_DEFAULT, ge=1, le=_LIST_MAX,
        description=(
            f"How many collectors to include in collector_last_seen. Default {_LIST_DEFAULT}, "
            f"maximum {_LIST_MAX}. collector_total always reports the real number."
        ),
    ),
    db: aiosqlite.Connection = Depends(get_db),
):
    from app.api.syslog import build_summary

    try:
        summary = await _in_time(build_summary(db, hours), "build that summary")
    except ResonanceDataError:
        raise
    except Exception as exc:
        log.warning("resonance syslog summary failed: %s", exc)
        raise ResonanceDataError(
            status_code=400, detail="The log store could not build that summary."
        ) from exc

    # The registry is the one part of this answer that grows without limit, and
    # an install with hundreds of collectors would otherwise put the whole list
    # into a conversation. Silent ones sort first: a collector that has never
    # been heard from is the reason someone asked, so it must survive the cut.
    collectors = list(summary.get("collector_last_seen") or [])
    collectors.sort(key=lambda c: (c.get("last_seen") is not None, c.get("last_seen") or ""))
    summary["collector_total"] = len(collectors)
    summary["collector_last_seen"] = collectors[:collector_limit]
    return summary


@router.get(
    f"{DATA_PREFIX}/syslog/timeline",
    operation_id="getSyslogTimeline",
    summary="Count syslog events over time, in fixed intervals",
    description=(
        "Event volume bucketed into equal intervals, oldest first — the shape behind 'did traffic "
        "spike', 'when did it stop', 'is it still arriving'. Choose `bucket_minutes` so the window "
        "produces no more intervals than `point_limit`; a wider window needs wider buckets, and "
        "asking for more is refused rather than truncated so the answer is never quietly wrong."
    ),
    response_model=SyslogTimeline,
    responses={**_ERRORS, 400: {"model": ErrorResponse, "description": "Too many intervals, or the query failed."}},
)
async def get_syslog_timeline(
    _user: dict = SessionUser,
    hours: int = Query(
        24, ge=1, le=720,
        description="How far back to look, in hours. Default 24, maximum 720 (30 days).",
    ),
    bucket_minutes: int = Query(
        60, ge=1, le=1440,
        description="Width of each interval, in minutes. Default 60, maximum 1440 (one day).",
    ),
    log_group: Optional[str] = Query(
        None, description="Restrict to one log group. Valid values come from listCollectors."
    ),
    point_limit: int = Query(
        200, ge=1, le=_TIMELINE_MAX_POINTS,
        description=(
            f"Most intervals the answer may contain. Default 200, maximum {_TIMELINE_MAX_POINTS}. "
            "A combination of hours and bucket_minutes that would exceed it is refused, with the "
            "bucket_minutes that would work."
        ),
    ),
):
    # Declared as a parameter rather than enforced silently: the ceiling is part
    # of what the caller needs to know to pick bucket_minutes, and a bound that
    # only exists in the server is a bound the model cannot plan around.
    points = -(-(hours * 60) // bucket_minutes)
    if points > point_limit:
        needed = -(-(hours * 60) // point_limit)
        raise ResonanceDataError(
            status_code=400,
            detail=(
                f"That is {points} intervals, over the limit of {point_limit}. "
                f"Use bucket_minutes of at least {needed} for a {hours} hour window, or ask for fewer hours."
            ),
        )

    try:
        buckets = await _in_time(
            get_storage().timeseries(
                hours=hours, bucket_minutes=bucket_minutes, log_group=log_group
            ),
            "build that timeline",
        )
    except ResonanceDataError:
        raise
    except Exception as exc:
        log.warning("resonance syslog timeline failed: %s", exc)
        raise ResonanceDataError(
            status_code=400, detail="The log store could not build that timeline."
        ) from exc

    return {
        "hours": hours,
        "bucket_minutes": bucket_minutes,
        "points": len(buckets),
        "buckets": buckets,
    }


@router.get(
    f"{DATA_PREFIX}/collectors",
    operation_id="listCollectors",
    summary="List the registered collectors and the names events are filed under",
    description=(
        "The registry of collectors this install accepts syslog from, with the organisation, "
        "log group and site each one stamps onto its events. Read this before filtering any "
        "search by name: these values are chosen per install and cannot be guessed — one site "
        "calls a collector 'fw-edge' and another calls the same thing 'perimeter-1'. Returns at "
        "most `limit` collectors plus the total number registered."
    ),
    response_model=CollectorList,
    responses=_ERRORS,
)
async def list_collectors(
    _user: dict = SessionUser,
    enabled_only: bool = Query(
        False, description="Only collectors currently accepting events. Default false, which lists all of them."
    ),
    limit: int = Query(
        _LIST_DEFAULT, ge=1, le=_LIST_MAX,
        description=f"How many to return. Default {_LIST_DEFAULT}, maximum {_LIST_MAX}.",
    ),
    offset: int = Query(0, ge=0, description="How many to skip, for paging."),
    db: aiosqlite.Connection = Depends(get_db),
):
    where = "WHERE enabled = 1" if enabled_only else ""

    async with db.execute(f"SELECT COUNT(*) FROM collector_registry {where}") as cur:
        total = (await cur.fetchone())[0]

    async with db.execute(
        f"""SELECT id, collector_ip, collector_name, org, log_group, site, notes, enabled,
                   created_at, updated_at
            FROM collector_registry
            {where}
            ORDER BY collector_ip
            LIMIT ? OFFSET ?""",
        (limit, offset),
    ) as cur:
        rows = await cur.fetchall()

    collectors = []
    for r in rows:
        d = dict(r)
        d["enabled"] = bool(d["enabled"])
        collectors.append(d)

    return _fit({"total": total, "limit": limit, "offset": offset, "collectors": collectors},
                "collectors")


@router.get(
    f"{DATA_PREFIX}/alerts/events",
    operation_id="listAlertEvents",
    summary="List alerts that have fired",
    description=(
        "Individual firings of pktLog's alert rules — a collector going silent, an unknown device "
        "appearing, a rate crossing a threshold — newest first. This is what to read for 'what is "
        "wrong' or 'what happened overnight'. An event with a null acked_at is one nobody has "
        "looked at yet; a null resolved_at means the condition has not cleared. Returns at most "
        "`limit` events plus the total number that matched."
    ),
    response_model=AlertEventList,
    responses=_ERRORS,
)
async def list_alert_events(
    _user: dict = SessionUser,
    unacked_only: bool = Query(False, description="Only events nobody has acknowledged yet."),
    unresolved_only: bool = Query(False, description="Only events whose condition has not cleared."),
    severity: Optional[AlertSeverity] = Query(None, description="Only events raised at this severity."),
    since: Optional[str] = Query(
        None, description="Only events fired at or after this time. ISO 8601."
    ),
    until: Optional[str] = Query(
        None, description="Only events fired at or before this time. ISO 8601."
    ),
    limit: int = Query(
        _SEARCH_DEFAULT, ge=1, le=_SEARCH_MAX,
        description=f"How many to return. Default {_SEARCH_DEFAULT}, maximum {_SEARCH_MAX}.",
    ),
    offset: int = Query(0, ge=0, description="How many to skip, for paging."),
    db: aiosqlite.Connection = Depends(get_db),
):
    from app.api.alerts import _event_to_dict

    clauses: list[str] = []
    params: list = []
    if unacked_only:
        clauses.append("e.acked_at IS NULL")
    if unresolved_only:
        clauses.append("e.resolved_at IS NULL")
    if severity:
        clauses.append("e.severity = ?")
        params.append(severity)
    if since:
        # fired_at is written by SQLite's datetime('now') — space separated, no
        # 'Z' — so both sides go through datetime() to compare like for like.
        clauses.append("e.fired_at >= datetime(?)")
        params.append(since)
    if until:
        clauses.append("e.fired_at <= datetime(?)")
        params.append(until)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    async with db.execute(
        f"SELECT COUNT(*) FROM alert_events e JOIN alert_rules r ON r.id = e.rule_id {where}",
        params,
    ) as cur:
        total = (await cur.fetchone())[0]

    async with db.execute(
        f"""SELECT e.*, r.name AS rule_name
            FROM alert_events e
            JOIN alert_rules r ON r.id = e.rule_id
            {where}
            ORDER BY e.fired_at DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ) as cur:
        rows = await cur.fetchall()

    events = []
    for r in rows:
        d = _event_to_dict(r)
        d["auto_resolved"] = bool(d.get("auto_resolved"))
        events.append(d)

    return _fit({"total": total, "limit": limit, "offset": offset, "events": events}, "events")


@router.get(
    f"{DATA_PREFIX}/alerts/rules",
    operation_id="listAlertRules",
    summary="List the configured alert rules",
    description=(
        "The rules that decide when pktLog raises an alert, whether each is switched on, and when "
        "it last fired. Read this to answer 'is anything watching for that' or to explain why an "
        "alert appeared. These are the rules themselves — the alerts they produced are in "
        "listAlertEvents. Returns at most `limit` rules plus the total configured."
    ),
    response_model=AlertRuleList,
    responses=_ERRORS,
)
async def list_alert_rules(
    _user: dict = SessionUser,
    enabled_only: bool = Query(False, description="Only rules that are switched on."),
    rule_type: Optional[AlertRuleType] = Query(None, description="Only rules watching for this condition."),
    limit: int = Query(
        _LIST_DEFAULT, ge=1, le=_LIST_MAX,
        description=f"How many to return. Default {_LIST_DEFAULT}, maximum {_LIST_MAX}.",
    ),
    offset: int = Query(0, ge=0, description="How many to skip, for paging."),
    db: aiosqlite.Connection = Depends(get_db),
):
    from app.api.alerts import _rule_to_dict

    clauses: list[str] = []
    params: list = []
    if enabled_only:
        clauses.append("enabled = 1")
    if rule_type:
        clauses.append("rule_type = ?")
        params.append(rule_type)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    async with db.execute(f"SELECT COUNT(*) FROM alert_rules {where}", params) as cur:
        total = (await cur.fetchone())[0]

    async with db.execute(
        f"SELECT * FROM alert_rules {where} ORDER BY name LIMIT ? OFFSET ?",
        params + [limit, offset],
    ) as cur:
        rows = await cur.fetchall()

    return _fit(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "rules": [_rule_to_dict(r) for r in rows],
        },
        "rules",
    )


@router.get(
    f"{DATA_PREFIX}/application-log",
    operation_id="searchApplicationLog",
    summary="Search pktLog's own diagnostic log",
    description=(
        "pktLog's internal log — what the ingest listener, alert engine, storage backend and "
        "schedulers recorded about their own running, newest first. This is where the answer to "
        "'why did collection stop' or 'what went wrong at 03:00' lives. It is NOT the syslog data "
        "collected from network devices; that is searchSyslog. Returns at most `limit` lines plus "
        "the total that matched, trimmed further if the page would be too large to carry."
    ),
    response_model=AppLogResult,
    responses=_ERRORS,
)
async def search_application_log(
    _user: dict = SessionUser,
    level: Optional[AppLogLevel] = Query(
        None,
        description=(
            "Only lines at this level or more severe. 'WARNING' returns warnings, errors and "
            "criticals. Omit for everything captured."
        ),
    ),
    logger: Optional[str] = Query(
        None,
        description=(
            "Only lines from this part of pktLog, matched as a prefix — 'pktlog.ingest' covers "
            "every logger beneath it."
        ),
    ),
    q: Optional[str] = Query(None, description="Free text to find inside the message. Matches anywhere."),
    since: Optional[str] = Query(None, description="Only lines after this time. ISO 8601."),
    until: Optional[str] = Query(None, description="Only lines at or before this time. ISO 8601."),
    limit: int = Query(
        _SEARCH_DEFAULT, ge=1, le=_SEARCH_MAX,
        description=f"How many lines to return. Default {_SEARCH_DEFAULT}, maximum {_SEARCH_MAX}.",
    ),
    offset: int = Query(0, ge=0, description="How many lines to skip, for paging."),
    db: aiosqlite.Connection = Depends(get_db),
):
    from app.api.logs import _LEVEL_NOS

    clauses: list[str] = []
    params: list = []
    if level:
        clauses.append("level_no >= ?")
        params.append(_LEVEL_NOS[level])
    if logger:
        clauses.append("logger LIKE ?")
        params.append(f"{logger}%")
    if q:
        clauses.append("message LIKE ?")
        params.append(f"%{q}%")
    if since:
        clauses.append("ts > ?")
        params.append(since)
    if until:
        clauses.append("ts <= ?")
        params.append(until)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    async with db.execute(f"SELECT COUNT(*) FROM app_logs {where}", params) as cur:
        total = (await cur.fetchone())[0]

    async with db.execute(
        f"""SELECT id, ts, level, level_no, logger, message, exc_info
            FROM app_logs
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ) as cur:
        rows = await cur.fetchall()

    records = []
    for row in rows:
        record = dict(row)
        trace = record.get("exc_info")
        if trace and len(trace) > _TRACEBACK_TAIL_CHARS:
            # Keep the tail: a Python traceback puts the exception last, and one
            # 8 KB traceback would otherwise consume a whole page's budget.
            record["exc_info"] = ("… earlier frames omitted …\n"
                                  + trace[-_TRACEBACK_TAIL_CHARS:])
        records.append(record)

    return _fit({"total": total, "limit": limit, "offset": offset, "records": records}, "records")


# ── The two documents ─────────────────────────────────────────────────────────
#
# Neither carries data — only names — so both are readable without a login, in
# the same way this app already publishes its own /openapi.json. Publishing them
# grants nothing on its own: an operation is reachable only because it is in
# GRANTED, and reachable only to a signed-in person whose role an admin listed.


def _declared_operation_ids(app) -> set[str]:
    """operationIds actually registered on the app.

    Walks the route table rather than calling app.openapi(), which would build
    and cache the schema at import time — before the SPA catch-all is mounted.

    The walk recurses because the table is not reliably flat: FastAPI 0.141
    keeps an included router as a single wrapper object holding its own routes,
    where earlier versions spliced them straight in. pktlog installs pin only a
    lower bound on fastapi, so both layouts are live in the field and a walker
    that understood one of them would have reported every operation missing on
    the other.
    """
    found: set[str] = set()
    seen: set[int] = set()

    def walk(routes) -> None:
        for route in routes or []:
            if id(route) in seen:
                continue
            seen.add(id(route))
            op = getattr(route, "operation_id", None)
            if op:
                found.add(op)
            nested = getattr(route, "routes", None)
            if nested is None:
                inner = getattr(route, "original_router", None)
                nested = getattr(inner, "routes", None) if inner is not None else None
            if nested:
                walk(nested)

    walk(getattr(app, "routes", []))
    return found


def validate_grants(app) -> list[str]:
    """Fail loudly at startup when a grant names an operation that is not there.

    A grant for a route that has been renamed is the quiet failure mode of this
    whole arrangement: the panel asks for it, gets a 404, and reports the app as
    having no such capability rather than as misconfigured. Returns the missing
    names so a caller can act on them; logs them either way.
    """
    declared = _declared_operation_ids(app)
    missing = [g.op for g in GRANTED if g.op not in declared]
    if missing:
        log.error(
            "resonance grant names %d operation(s) this app does not declare: %s — "
            "they are being withheld from /.well-known/resonance.json",
            len(missing), ", ".join(missing),
        )
    return missing


async def writes_are_enabled(db: aiosqlite.Connection) -> bool:
    """True when at least one role has been trusted with more than reading.

    The grant is one document for the whole origin and is served without a
    login, so it cannot vary per person — but it can tell the truth about the
    install. Where no role is set to "write", the write operations are withheld
    from it entirely rather than advertised and refused on every attempt.
    """
    for role in ("admin", "analyst", "viewer"):
        if LEVEL_RANK.get(await role_level(db, role), 0) >= LEVEL_RANK["write"]:
            return True
    return False


def build_grant(app, allow_writes: bool) -> dict:
    """The grant document, generated from GRANTED so the two cannot disagree."""
    declared = _declared_operation_ids(app)
    allow: list[dict] = []
    for g in GRANTED:
        if g.op not in declared:
            continue
        if g.writes and not allow_writes:
            continue
        entry: dict[str, Any] = {"op": g.op}
        if g.writes:
            entry["writes"] = True
        allow.append(entry)
    return {"resonance": 1, "spec": SPEC_PATH, "allow": allow}


def _referenced_schemas(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            out.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            _referenced_schemas(value, out)
    elif isinstance(node, list):
        for value in node:
            _referenced_schemas(value, out)


def build_spec(app, allow_writes: bool) -> dict:
    """This app's own OpenAPI, narrowed to the granted operations.

    Generated from the live routes rather than written by hand, so a parameter
    that changes shape changes here too — the failure a hand-kept spec always
    ends in is the assistant confidently sending a field that stopped existing.
    Narrowed rather than published whole because everything an operation's prose
    has to compete with is another operation's prose: eighty of them, most of
    which the grant forbids, is eighty chances to pick the wrong one.
    """
    full = app.openapi()
    granted = {g.op for g in GRANTED if allow_writes or not g.writes}

    paths: dict[str, dict] = {}
    for path, item in (full.get("paths") or {}).items():
        # Deep-copied because app.openapi() hands back the app's own cached
        # schema object: editing an operation in place here would edit the
        # document this app publishes at /openapi.json as well.
        kept = {
            method: copy.deepcopy(operation)
            for method, operation in item.items()
            if isinstance(operation, dict) and operation.get("operationId") in granted
        }
        if kept:
            for operation in kept.values():
                # Nothing is presented on these calls but the person's own
                # session cookie, which the browser attaches by itself.
                operation.pop("security", None)
            paths[path] = kept

    wanted: set[str] = set()
    _referenced_schemas(paths, wanted)
    all_schemas = (full.get("components") or {}).get("schemas") or {}
    resolved: dict[str, Any] = {}
    while wanted:
        name = wanted.pop()
        if name in resolved or name not in all_schemas:
            continue
        resolved[name] = copy.deepcopy(all_schemas[name])
        nested: set[str] = set()
        _referenced_schemas(all_schemas[name], nested)
        wanted |= nested - resolved.keys()

    spec: dict[str, Any] = {
        "openapi": full.get("openapi", "3.1.0"),
        "info": {
            "title": "pktLog — assistant data surface",
            "version": full.get("info", {}).get("version", "0.1.0"),
            "description": (
                "The read operations pktLog publishes for an embedded assistant. Every call is "
                "made by pktLog's own page, same-origin, on the session of the person already "
                "signed in, so nothing here can reach data that person could not already open "
                "in the interface."
            ),
        },
        "paths": paths,
    }
    if resolved:
        spec["components"] = {"schemas": resolved}
    return spec


# Two possible documents — with writes and without — so the setting can change
# without a restart while the expensive part is still built once each.
_spec_cache: dict[bool, Any] = {}


@router.get(GRANT_PATH, include_in_schema=False)
async def resonance_grant(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """What this install permits the assistant to call. Names only, no data.

    Public by contract: it has to be readable before anyone signs in, and it
    carries nothing but operation names. Whether the write operations appear
    depends on the levels an admin set, so an install that has trusted nobody
    with writes publishes a grant that cannot be read as offering them.
    """
    grant = build_grant(request.app, await writes_are_enabled(db))
    log.info("resonance grant fetched: %d operation(s), %d writing",
             len(grant["allow"]), sum(1 for a in grant["allow"] if a.get("writes")))
    return JSONResponse(
        grant,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get(SPEC_PATH, include_in_schema=False)
async def resonance_spec(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """The OpenAPI document for the granted operations."""
    allow_writes = await writes_are_enabled(db)
    if allow_writes not in _spec_cache:
        _spec_cache[allow_writes] = build_spec(request.app, allow_writes)
    log.info("resonance spec fetched (writes %s)", "included" if allow_writes else "withheld")
    return JSONResponse(
        _spec_cache[allow_writes],
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


# ── Pending collectors ────────────────────────────────────────────────────────


class PendingCollector(BaseModel):
    """A sender pktLog is dropping because it is not in the registry."""

    model_config = ConfigDict(extra="allow")

    collector_ip: Optional[str] = Field(None, description="Address the unregistered traffic is arriving from.")
    first_seen: Optional[str] = Field(None, description="When it was first heard from.")
    last_seen: Optional[str] = Field(None, description="When it was last heard from.")
    message_count: Optional[int] = Field(None, description="How many messages have been dropped from it.")
    sample_message: Optional[str] = Field(None, description="One of its messages, to help identify the device.")
    ignored: Optional[bool] = Field(None, description="True when someone chose to hide it rather than admit it.")


class PendingCollectorList(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = Field(description="How many senders are waiting, ignoring limit and offset.")
    limit: int
    offset: int
    returned: int = Field(description="How many are in this response.")
    truncated_for_size: bool = Field(description="True when entries were dropped to keep the response carryable.")
    pending: list[PendingCollector]


@router.get(
    f"{DATA_PREFIX}/collectors/pending",
    operation_id="listPendingCollectors",
    summary="List senders waiting to be admitted",
    description=(
        "Devices that have sent syslog to this install but are not in the collector registry, "
        "so their messages are being dropped. This is the queue behind 'why am I not seeing "
        "anything from that new switch'. Administrators only, as in the interface. Each entry "
        "carries a sample message to help identify the device before admitting it with "
        "approveCollector or hiding it with ignoreCollector."
    ),
    response_model=PendingCollectorList,
    responses=_ERRORS,
)
async def list_pending_collectors(
    user: dict = SessionUser,
    include_ignored: bool = Query(
        False, description="Also list senders someone chose to hide. Default false."
    ),
    limit: int = Query(
        _LIST_DEFAULT, ge=1, le=_LIST_MAX,
        description=f"How many to return. Default {_LIST_DEFAULT}, maximum {_LIST_MAX}.",
    ),
    offset: int = Query(0, ge=0, description="How many to skip, for paging."),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _apply_app_rule(user, require_admin, "see the approval queue")

    where = "" if include_ignored else "WHERE ignored = 0"
    async with db.execute(f"SELECT COUNT(*) FROM pending_collectors {where}") as cur:
        total = (await cur.fetchone())[0]

    async with db.execute(
        f"""SELECT collector_ip, first_seen, last_seen, message_count, sample_message, ignored
            FROM pending_collectors
            {where}
            ORDER BY last_seen DESC
            LIMIT ? OFFSET ?""",
        (limit, offset),
    ) as cur:
        rows = await cur.fetchall()

    pending_rows = []
    for row in rows:
        entry = dict(row)
        entry["ignored"] = bool(entry.get("ignored"))
        pending_rows.append(entry)

    return _fit({"total": total, "limit": limit, "offset": offset, "pending": pending_rows},
                "pending")


# ── Operations that change something ──────────────────────────────────────────
#
# Every one of these is marked `writes: true` in the grant, so resonance stops
# and reads the actual values back to the person before it runs one. That
# confirmation is theirs to enforce and cannot be relied on here, which is why
# both gates above still apply on the request itself.
#
# What is deliberately absent is as much of the design as what is present: no
# delete of a rule, a collector or a pending sender; no clearing of logs; no
# creating or editing of configuration. An assistant can act on what an
# administrator already put there, and cannot author or destroy it.


class AckResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: int = Field(description="The alert event this refers to.")
    acknowledged: bool = Field(description="True if this call acknowledged it.")
    already_acknowledged: bool = Field(
        description="True when someone had already acknowledged it, in which case nothing changed."
    )
    acked_at: Optional[str] = Field(None, description="When it was acknowledged (ISO 8601, UTC).")
    message: str = Field(description="What happened, phrased to be read back to the person.")


class AckAllResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    acknowledged: int = Field(description="How many outstanding alerts this call acknowledged.")
    message: str = Field(description="What happened, phrased to be read back to the person.")


class ToggleRuleResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int = Field(description="The rule that was switched.")
    name: Optional[str] = Field(None, description="Its name, for reading back.")
    enabled: bool = Field(description="Whether the rule is now on.")
    message: str = Field(description="What happened, phrased to be read back to the person.")


class ApproveResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    collector_ip: str
    collector_name: Optional[str] = None
    registered: bool = Field(description="True when the sender is now in the registry.")
    message: str = Field(description="What happened, phrased to be read back to the person.")


class IgnoreResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    collector_ip: str
    ignored: bool = Field(description="True when the sender is now hidden from the queue.")
    message: str = Field(description="What happened, phrased to be read back to the person.")


@router.post(
    f"{DATA_PREFIX}/alerts/events/{{event_id}}/ack",
    operation_id="ackAlertEvent",
    summary="Acknowledge one alert",
    description=(
        "Mark a single fired alert as seen, recording who did it and when. This changes state. "
        "It does not resolve the alert or fix the condition behind it — the underlying problem "
        "is untouched and the rule will fire again if it recurs. Acknowledging something already "
        "acknowledged changes nothing and says so. Available to analysts and administrators, as "
        "in the interface."
    ),
    response_model=AckResult,
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "No alert event with that id."}},
)
async def ack_alert_event(
    event_id: int = Path(
        description="Id of the alert event to acknowledge, as returned by listAlertEvents."
    ),
    user: dict = WriteUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    await _apply_app_rule(user, require_analyst, "acknowledge alerts")

    async with db.execute(
        "SELECT e.acked_at, r.name FROM alert_events e "
        "JOIN alert_rules r ON r.id = e.rule_id WHERE e.id = ?",
        (event_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise ResonanceDataError(status_code=404, detail=f"There is no alert event {event_id}.")

    if row["acked_at"]:
        when = str(row["acked_at"]).replace(" ", "T") + "Z"
        return {
            "event_id": event_id, "acknowledged": False, "already_acknowledged": True,
            "acked_at": when,
            "message": f"Alert {event_id} ({row['name']}) was already acknowledged at {when}.",
        }

    await db.execute(
        "UPDATE alert_events SET acked_at = datetime('now'), acked_by = ? "
        "WHERE id = ? AND acked_at IS NULL",
        (user.get("id"), event_id),
    )
    await db.commit()

    async with db.execute("SELECT acked_at FROM alert_events WHERE id = ?", (event_id,)) as cur:
        acked = (await cur.fetchone())["acked_at"]
    when = str(acked).replace(" ", "T") + "Z" if acked else None
    log.info("resonance: %s acknowledged alert event %s", user.get("username"), event_id)
    return {
        "event_id": event_id, "acknowledged": True, "already_acknowledged": False,
        "acked_at": when,
        "message": f"Acknowledged alert {event_id} ({row['name']}). The condition behind it is unchanged.",
    }


@router.post(
    f"{DATA_PREFIX}/alerts/events/ack-all",
    operation_id="ackAllAlertEvents",
    summary="Acknowledge every outstanding alert",
    description=(
        "Mark every alert nobody has acknowledged yet as seen, in one go. This changes state, "
        "and it is indiscriminate: it covers alerts the person has not read, including any that "
        "fire between their asking and it running. It resolves nothing — every underlying "
        "condition is untouched. Count the outstanding ones with listAlertEvents and "
        "unacked_only=true before offering this. Analysts and administrators, as in the interface."
    ),
    response_model=AckAllResult,
    responses=_ERRORS,
)
async def ack_all_alert_events(
    user: dict = WriteUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    await _apply_app_rule(user, require_analyst, "acknowledge alerts")

    async with db.execute("SELECT COUNT(*) FROM alert_events WHERE acked_at IS NULL") as cur:
        outstanding = (await cur.fetchone())[0]

    if not outstanding:
        return {"acknowledged": 0, "message": "There were no unacknowledged alerts."}

    await db.execute(
        "UPDATE alert_events SET acked_at = datetime('now'), acked_by = ? WHERE acked_at IS NULL",
        (user.get("id"),),
    )
    await db.commit()
    log.info("resonance: %s acknowledged all %d outstanding alerts",
             user.get("username"), outstanding)
    return {
        "acknowledged": outstanding,
        "message": (f"Acknowledged {outstanding} outstanding alert"
                    f"{'s' if outstanding != 1 else ''}. No conditions were resolved."),
    }


@router.post(
    f"{DATA_PREFIX}/alerts/rules/{{rule_id}}/toggle",
    operation_id="toggleAlertRule",
    summary="Switch an alert rule on or off",
    description=(
        "Flip an existing alert rule between enabled and disabled. This changes state, and it "
        "changes it for everyone: a disabled rule stops watching entirely, so nothing it would "
        "have caught is caught until it is switched back. Read the rule with listAlertRules "
        "first so the person is told which one, and which way, before it runs. It cannot create, "
        "edit or delete a rule. Administrators only, as in the interface."
    ),
    response_model=ToggleRuleResult,
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "No alert rule with that id."}},
)
async def toggle_alert_rule(
    rule_id: int = Path(
        description="Id of the alert rule to switch, as returned by listAlertRules."
    ),
    user: dict = WriteUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    await _apply_app_rule(user, require_admin, "change alert rules")

    async with db.execute(
        "SELECT enabled, name FROM alert_rules WHERE id = ?", (rule_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise ResonanceDataError(status_code=404, detail=f"There is no alert rule {rule_id}.")

    new_enabled = 0 if row["enabled"] else 1
    await db.execute(
        "UPDATE alert_rules SET enabled = ?, updated_at = datetime('now') WHERE id = ?",
        (new_enabled, rule_id),
    )
    await db.commit()
    log.info("resonance: %s %s alert rule %s (%s)", user.get("username"),
             "enabled" if new_enabled else "disabled", rule_id, row["name"])
    state = "on" if new_enabled else "off"
    return {
        "id": rule_id, "name": row["name"], "enabled": bool(new_enabled),
        "message": (f"Alert rule {rule_id} ({row['name']}) is now switched {state}."
                    + ("" if new_enabled else " It will not fire until it is switched back.")),
    }


@router.post(
    f"{DATA_PREFIX}/collectors/pending/{{collector_ip:path}}/approve",
    operation_id="approveCollector",
    summary="Admit a waiting sender into the collector registry",
    description=(
        "Register a device that has been sending syslog but was not in the registry, so its "
        "messages start being stored instead of dropped, and clear it from the approval queue. "
        "This changes state and it changes what is retained: everything that device sends from "
        "now on is kept. Read it from listPendingCollectors first — the sample message is how "
        "the person confirms the device is theirs. Administrators only, as in the interface."
    ),
    response_model=ApproveResult,
    responses={
        **_ERRORS,
        404: {"model": ErrorResponse, "description": "No sender waiting at that address."},
        409: {"model": ErrorResponse, "description": "That address is already registered."},
    },
)
async def approve_collector(
    collector_ip: str = Path(
        description="Address of the waiting sender, exactly as listPendingCollectors reports it."
    ),
    user: dict = WriteUser,
    collector_name: str = Query(
        ..., min_length=1, max_length=120,
        description="Name to register the device under. Chosen by the person, not guessed — it "
                    "is stamped onto every event the device sends from now on.",
    ),
    org: str = Query("", max_length=120, description="Organisation to file its events under. Optional."),
    log_group: str = Query("", max_length=120, description="Log group to file its events under. Optional."),
    site: str = Query("", max_length=120, description="Site to file its events under. Optional."),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _apply_app_rule(user, require_admin, "admit collectors")

    from app.ingest import pending as pending_module

    async with db.execute(
        "SELECT 1 FROM pending_collectors WHERE collector_ip = ?", (collector_ip,)
    ) as cur:
        waiting = await cur.fetchone() is not None
    if not waiting:
        raise ResonanceDataError(
            status_code=404,
            detail=f"No sender is waiting at {collector_ip}. listPendingCollectors shows the queue.",
        )

    try:
        await db.execute(
            """INSERT INTO collector_registry
               (collector_ip, collector_name, org, log_group, site, notes, enabled)
               VALUES (?, ?, ?, ?, ?, '', 1)""",
            (collector_ip, collector_name, org, log_group, site),
        )
        await db.commit()
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise ResonanceDataError(
                status_code=409, detail=f"{collector_ip} is already in the collector registry."
            ) from exc
        log.warning("resonance approve of %s failed: %s", collector_ip, exc)
        raise ResonanceDataError(
            status_code=400, detail=f"{collector_ip} could not be registered."
        ) from exc

    # The registry row is what admits the data; clearing the queue only tidies
    # up, so it happens second and never on its own.
    await pending_module.clear(db, collector_ip)
    log.info("resonance: %s admitted collector %s as %r",
             user.get("username"), collector_ip, collector_name)
    return {
        "collector_ip": collector_ip, "collector_name": collector_name, "registered": True,
        "message": (f"{collector_ip} is now registered as {collector_name!r}. Its messages will "
                    "be stored from now on; anything it sent before this is already gone."),
    }


@router.post(
    f"{DATA_PREFIX}/collectors/pending/{{collector_ip:path}}/ignore",
    operation_id="ignoreCollector",
    summary="Hide a waiting sender from the approval queue",
    description=(
        "Take a sender off the approval queue without registering it. Its messages go on being "
        "dropped and its counters go on rising — this only records that someone looked and chose "
        "not to admit it, so a noisy unwanted device stops burying real ones. This changes state. "
        "It is not a block and it is reversible from the Approval page. Administrators only, as "
        "in the interface."
    ),
    response_model=IgnoreResult,
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "No sender waiting at that address."}},
)
async def ignore_collector(
    collector_ip: str = Path(
        description="Address of the waiting sender, exactly as listPendingCollectors reports it."
    ),
    user: dict = WriteUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    await _apply_app_rule(user, require_admin, "change the approval queue")

    cursor = await db.execute(
        "UPDATE pending_collectors SET ignored = 1 WHERE collector_ip = ?", (collector_ip,)
    )
    await db.commit()
    if cursor.rowcount == 0:
        raise ResonanceDataError(
            status_code=404,
            detail=f"No sender is waiting at {collector_ip}. listPendingCollectors shows the queue.",
        )
    log.info("resonance: %s hid pending collector %s", user.get("username"), collector_ip)
    return {
        "collector_ip": collector_ip, "ignored": True,
        "message": (f"{collector_ip} is hidden from the approval queue. It is not blocked — its "
                    "messages are still being dropped, and it can be put back from the Approval page."),
    }
