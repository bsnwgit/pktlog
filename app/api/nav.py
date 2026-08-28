"""
pktLog — Navigation manifest for pktHub's APPS sidebar.

Manifest: GET /api/nav/manifest  → this app's own left-nav, in display order

pktHub mirrors these entries under pktLog in its own sidebar and opens each
one as a chromeless embed of the real page (`/proxy/<app_id><path>?chromeless=1`),
so the hub's menu is this app's menu rather than a re-implementation of it.

Keep in step with NAV in frontend/src/components/Layout.tsx — that const is
what this app renders for a direct visit, this manifest is what the hub
renders. Same menu, two consumers: a page added to one belongs in the other.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import require_suite_token

# Fetched unauthenticated by pktHub's health poller, and it discloses this
# app's page structure — so it carries the same X-Suite-Token gate as the
# widget endpoints in app/api/widgets.py.
router = APIRouter(dependencies=[Depends(require_suite_token)])

# ── Manifest ──────────────────────────────────────────────────────────────────
# `path` is relative to this app's root. `icon` is the same glyph the app's own
# sidebar draws, so the hub renders a visually identical row.
NAV_MANIFEST = [
    {"path": "/",         "label": "Dashboard", "icon": "◑", "admin_only": False},
    {"path": "/explorer", "label": "Explorer",  "icon": "⊞", "admin_only": False},
    {"path": "/alerts",   "label": "Alerts",    "icon": "△", "admin_only": False, "divider_before": True},
    {"path": "/logs",     "label": "Logs",      "icon": "≡", "admin_only": False},
    {"path": "/approval", "label": "Approval",  "icon": "✓", "admin_only": True,  "divider_before": True},
    {"path": "/settings", "label": "Settings",  "icon": "⚙", "admin_only": False, "divider_before": True},
]


@router.get("/nav/manifest")
async def nav_manifest():
    return NAV_MANIFEST
