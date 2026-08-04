"""
GET/PUT /api/user-api-keys — per-user external API keys (AbuseIPDB, etc.)
for the IP Info / Reputation Lookup feature.

Every authenticated user manages only their own keys, scoped by username —
there is no admin-wide view or override here.
"""
from __future__ import annotations

import json
from urllib.parse import quote

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import DB_PATH
from app.dependencies import CurrentUser
from app.crypto import decrypt_str, encrypt_str

# Public, harmless IP used to exercise each provider's lookup endpoint when
# testing a key — Google Public DNS, safe to query against any provider.
_TEST_IP = "8.8.8.8"

router = APIRouter()

# Providers this app knows how to use.
SUPPORTED_PROVIDERS: dict[str, str] = {
    "abuseipdb":      "AbuseIPDB",
    "ipqualityscore": "IPQualityScore",
    "ipinfo":         "ipinfo.io",
    "ipapi_is":       "ipapi.is",
    "mxtoolbox":      "MXToolbox",
}

# The ipinfo.io response sections a user can individually show/hide in the IP
# Lookup modal. Display preference only — ipinfo.io always returns whatever
# the account's plan unlocks; this just controls what renders.
IPINFO_FIELDS = ["geolocation", "asn", "company", "privacy", "abuse", "domains"]

# Same idea for ipapi.is — its response sections a user can individually
# show/hide. "detection" covers the is_vpn/is_proxy/is_tor/is_datacenter/
# is_abuser/is_mobile/is_satellite/is_crawler/is_bogon flags plus the vpn{}
# detail object.
IPAPI_IS_FIELDS = ["geolocation", "asn", "company", "detection", "abuse"]

# MXToolbox only auto-wires 3 of its 21 commands into the IP Lookup modal
# (see app/api/ip_info.py's _fetch_mxtoolbox) — the rest are domain/email
# record checks and active probes, reachable only via the generic
# /api/mxtoolbox/lookup endpoint, not this per-IP lookup. These 3 keys match
# the mxtoolbox result dict's own field names.
MXTOOLBOX_FIELDS = ["ptr", "asn", "blacklist"]


class ApiKeyOut(BaseModel):
    provider: str
    label:    str
    api_key:  str    # "" if not set
    updated_at: str | None = None
    enabled_fields: list[str] | None = None  # ipinfo/ipapi_is/mxtoolbox only; None = not customized (all shown)
    free_tier: bool = False  # ipapi_is only — use its keyless free tier instead of api_key
    enabled: bool = True  # ipinfo/ipapi_is/abuseipdb/mxtoolbox only — show this provider's section in the IP Lookup modal at all


class ApiKeyIn(BaseModel):
    api_key: str


class FieldsIn(BaseModel):
    enabled_fields: list[str]


class FreeTierIn(BaseModel):
    free_tier: bool


class EnabledIn(BaseModel):
    enabled: bool


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(user: CurrentUser):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT provider, api_key, updated_at, enabled_fields, free_tier, enabled FROM user_api_keys WHERE username = ?",
            (user["username"],),
        ) as cur:
            rows = {r["provider"]: r for r in await cur.fetchall()}

    return [
        ApiKeyOut(
            provider=provider,
            label=label,
            api_key=decrypt_str(rows[provider]["api_key"]) if provider in rows else "",
            updated_at=rows[provider]["updated_at"] if provider in rows else None,
            enabled_fields=json.loads(rows[provider]["enabled_fields"]) if provider in rows and rows[provider]["enabled_fields"] else None,
            free_tier=bool(rows[provider]["free_tier"]) if provider in rows else False,
            enabled=bool(rows[provider]["enabled"]) if provider in rows else True,
        )
        for provider, label in SUPPORTED_PROVIDERS.items()
    ]


@router.put("/{provider}/enabled", response_model=ApiKeyOut)
async def set_provider_enabled(provider: str, body: EnabledIn, user: CurrentUser):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO user_api_keys (username, provider, enabled, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT (username, provider)
               DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at""",
            (user["username"], provider, int(body.enabled)),
        )
        await db.commit()
        async with db.execute(
            "SELECT api_key, updated_at, enabled_fields, free_tier, enabled FROM user_api_keys WHERE username = ? AND provider = ?",
            (user["username"], provider),
        ) as cur:
            row = await cur.fetchone()

    return ApiKeyOut(
        provider=provider,
        label=SUPPORTED_PROVIDERS[provider],
        api_key=decrypt_str(row["api_key"]),
        updated_at=row["updated_at"],
        enabled_fields=json.loads(row["enabled_fields"]) if row["enabled_fields"] else None,
        free_tier=bool(row["free_tier"]),
        enabled=bool(row["enabled"]),
    )


@router.put("/ipapi_is/free-tier", response_model=ApiKeyOut)
async def set_ipapi_is_free_tier(body: FreeTierIn, user: CurrentUser):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO user_api_keys (username, provider, free_tier, updated_at)
               VALUES (?, 'ipapi_is', ?, datetime('now'))
               ON CONFLICT (username, provider)
               DO UPDATE SET free_tier = excluded.free_tier, updated_at = excluded.updated_at""",
            (user["username"], int(body.free_tier)),
        )
        await db.commit()
        async with db.execute(
            "SELECT api_key, updated_at, enabled_fields, free_tier, enabled FROM user_api_keys WHERE username = ? AND provider = 'ipapi_is'",
            (user["username"],),
        ) as cur:
            row = await cur.fetchone()

    return ApiKeyOut(
        provider="ipapi_is",
        label=SUPPORTED_PROVIDERS["ipapi_is"],
        api_key=decrypt_str(row["api_key"]),
        updated_at=row["updated_at"],
        enabled_fields=json.loads(row["enabled_fields"]) if row["enabled_fields"] else None,
        free_tier=bool(row["free_tier"]),
        enabled=bool(row["enabled"]),
    )


@router.put("/ipinfo/fields", response_model=ApiKeyOut)
async def set_ipinfo_fields(body: FieldsIn, user: CurrentUser):
    unknown = [f for f in body.enabled_fields if f not in IPINFO_FIELDS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown field(s): {', '.join(unknown)}")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO user_api_keys (username, provider, enabled_fields, updated_at)
               VALUES (?, 'ipinfo', ?, datetime('now'))
               ON CONFLICT (username, provider)
               DO UPDATE SET enabled_fields = excluded.enabled_fields, updated_at = excluded.updated_at""",
            (user["username"], json.dumps(body.enabled_fields)),
        )
        await db.commit()
        async with db.execute(
            "SELECT api_key, updated_at, enabled_fields, enabled FROM user_api_keys WHERE username = ? AND provider = 'ipinfo'",
            (user["username"],),
        ) as cur:
            row = await cur.fetchone()

    return ApiKeyOut(
        provider="ipinfo",
        label=SUPPORTED_PROVIDERS["ipinfo"],
        api_key=decrypt_str(row["api_key"]),
        updated_at=row["updated_at"],
        enabled_fields=json.loads(row["enabled_fields"]) if row["enabled_fields"] else None,
        enabled=bool(row["enabled"]),
    )


@router.put("/ipapi_is/fields", response_model=ApiKeyOut)
async def set_ipapi_is_fields(body: FieldsIn, user: CurrentUser):
    unknown = [f for f in body.enabled_fields if f not in IPAPI_IS_FIELDS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown field(s): {', '.join(unknown)}")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO user_api_keys (username, provider, enabled_fields, updated_at)
               VALUES (?, 'ipapi_is', ?, datetime('now'))
               ON CONFLICT (username, provider)
               DO UPDATE SET enabled_fields = excluded.enabled_fields, updated_at = excluded.updated_at""",
            (user["username"], json.dumps(body.enabled_fields)),
        )
        await db.commit()
        async with db.execute(
            "SELECT api_key, updated_at, enabled_fields, free_tier, enabled FROM user_api_keys WHERE username = ? AND provider = 'ipapi_is'",
            (user["username"],),
        ) as cur:
            row = await cur.fetchone()

    return ApiKeyOut(
        provider="ipapi_is",
        label=SUPPORTED_PROVIDERS["ipapi_is"],
        api_key=decrypt_str(row["api_key"]),
        updated_at=row["updated_at"],
        enabled_fields=json.loads(row["enabled_fields"]) if row["enabled_fields"] else None,
        free_tier=bool(row["free_tier"]),
        enabled=bool(row["enabled"]),
    )


@router.put("/mxtoolbox/fields", response_model=ApiKeyOut)
async def set_mxtoolbox_fields(body: FieldsIn, user: CurrentUser):
    unknown = [f for f in body.enabled_fields if f not in MXTOOLBOX_FIELDS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown field(s): {', '.join(unknown)}")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT INTO user_api_keys (username, provider, enabled_fields, updated_at)
               VALUES (?, 'mxtoolbox', ?, datetime('now'))
               ON CONFLICT (username, provider)
               DO UPDATE SET enabled_fields = excluded.enabled_fields, updated_at = excluded.updated_at""",
            (user["username"], json.dumps(body.enabled_fields)),
        )
        await db.commit()
        async with db.execute(
            "SELECT api_key, updated_at, enabled_fields, enabled FROM user_api_keys WHERE username = ? AND provider = 'mxtoolbox'",
            (user["username"],),
        ) as cur:
            row = await cur.fetchone()

    return ApiKeyOut(
        provider="mxtoolbox",
        label=SUPPORTED_PROVIDERS["mxtoolbox"],
        api_key=decrypt_str(row["api_key"]),
        updated_at=row["updated_at"],
        enabled_fields=json.loads(row["enabled_fields"]) if row["enabled_fields"] else None,
        enabled=bool(row["enabled"]),
    )


@router.put("/{provider}", response_model=ApiKeyOut)
async def set_api_key(provider: str, body: ApiKeyIn, user: CurrentUser):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    key = body.api_key.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if key:
            await db.execute(
                """INSERT INTO user_api_keys (username, provider, api_key, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT (username, provider)
                   DO UPDATE SET api_key = excluded.api_key, updated_at = excluded.updated_at""",
                (user["username"], provider, encrypt_str(key)),
            )
        else:
            # Empty key means "clear" the key — but keep the row (if it
            # exists) rather than deleting it outright, so any other stored
            # preference for this provider (enabled/enabled_fields/free_tier)
            # survives clearing/re-saving a blank key.
            await db.execute(
                """INSERT INTO user_api_keys (username, provider, api_key, updated_at)
                   VALUES (?, ?, '', datetime('now'))
                   ON CONFLICT (username, provider)
                   DO UPDATE SET api_key = '', updated_at = excluded.updated_at""",
                (user["username"], provider),
            )
        await db.commit()
        async with db.execute(
            "SELECT enabled_fields, free_tier, enabled FROM user_api_keys WHERE username = ? AND provider = ?",
            (user["username"], provider),
        ) as cur:
            row = await cur.fetchone()

    return ApiKeyOut(
        provider=provider,
        label=SUPPORTED_PROVIDERS[provider],
        api_key=key,
        enabled_fields=json.loads(row["enabled_fields"]) if row and row["enabled_fields"] else None,
        free_tier=bool(row["free_tier"]) if row else False,
        enabled=bool(row["enabled"]) if row else True,
    )


@router.post("/{provider}/test")
async def test_api_key(provider: str, body: ApiKeyIn, _: CurrentUser) -> dict:
    """Exercise the given key against the provider's real API with a harmless
    test IP. Tests whatever key is passed in — not necessarily saved yet —
    so a user can validate before committing."""
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    key = body.api_key.strip()
    if not key:
        return {"status": "skipped", "detail": "No API key entered"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if provider == "abuseipdb":
                resp = await client.get(
                    "https://api.abuseipdb.com/api/v2/check",
                    params={"ipAddress": _TEST_IP, "maxAgeInDays": 90},
                    headers={"Key": key, "Accept": "application/json"},
                )
                if resp.status_code == 200:
                    return {"status": "ok", "detail": "Key is valid"}
                return {"status": "failed", "detail": f"AbuseIPDB returned HTTP {resp.status_code}: {resp.text[:200]}"}

            elif provider == "ipinfo":
                resp = await client.get(f"https://ipinfo.io/{_TEST_IP}/json", params={"token": key})
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                if resp.status_code == 200 and "error" not in data:
                    return {"status": "ok", "detail": "Key is valid"}
                return {"status": "failed", "detail": data.get("error", {}).get("message") or f"ipinfo.io returned HTTP {resp.status_code}: {resp.text[:200]}"}

            elif provider == "ipqualityscore":
                resp = await client.get(f"https://ipqualityscore.com/api/json/ip/{quote(key, safe='')}/{_TEST_IP}")
                data = resp.json() if resp.status_code == 200 else {}
                if resp.status_code == 200 and data.get("success"):
                    return {"status": "ok", "detail": "Key is valid"}
                return {"status": "failed", "detail": data.get("message") or f"IPQualityScore returned HTTP {resp.status_code}: {resp.text[:200]}"}

            elif provider == "mxtoolbox":
                resp = await client.get(
                    "https://api.mxtoolbox.com/api/v1/Lookup/ptr/",
                    params={"argument": _TEST_IP},
                    headers={"Authorization": key, "Accept": "application/json"},
                )
                if resp.status_code == 200:
                    return {"status": "ok", "detail": "Key is valid"}
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                errors = data.get("Errors") if isinstance(data, dict) else None
                detail = errors[0].get("ErrorMessage") if isinstance(errors, list) and errors and isinstance(errors[0], dict) else None
                return {"status": "failed", "detail": detail or f"MXToolbox returned HTTP {resp.status_code}: {resp.text[:200]}"}

            elif provider == "ipapi_is":
                resp = await client.get("https://api.ipapi.is/", params={"q": _TEST_IP, "key": key})
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                if resp.status_code == 200 and not data.get("error"):
                    return {"status": "ok", "detail": "Key is valid"}
                return {"status": "failed", "detail": data.get("error") or f"ipapi.is returned HTTP {resp.status_code}: {resp.text[:200]}"}

    except httpx.RequestError as exc:
        return {"status": "failed", "detail": f"Request error: {exc}"}

    return {"status": "failed", "detail": "Unhandled provider"}
