"""
GET/PUT /api/user-api-keys — per-user external API keys (AbuseIPDB, etc.)
for the IP Info / Reputation Lookup feature.

Every authenticated user manages only their own keys, scoped by username —
there is no admin-wide view or override here.
"""
from __future__ import annotations

from urllib.parse import quote

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import DB_PATH
from app.dependencies import CurrentUser

# Public, harmless IP used to exercise each provider's lookup endpoint when
# testing a key — Google Public DNS, safe to query against any provider.
_TEST_IP = "8.8.8.8"

router = APIRouter()

# Providers this app knows how to use.
SUPPORTED_PROVIDERS: dict[str, str] = {
    "abuseipdb":      "AbuseIPDB",
    "ipinfo":         "ipinfo.io",
    "ipqualityscore": "IPQualityScore",
}


class ApiKeyOut(BaseModel):
    provider: str
    label:    str
    api_key:  str    # "" if not set
    updated_at: str | None = None


class ApiKeyIn(BaseModel):
    api_key: str


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(user: CurrentUser):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT provider, api_key, updated_at FROM user_api_keys WHERE username = ?",
            (user["username"],),
        ) as cur:
            rows = {r["provider"]: r for r in await cur.fetchall()}

    return [
        ApiKeyOut(
            provider=provider,
            label=label,
            api_key=rows[provider]["api_key"] if provider in rows else "",
            updated_at=rows[provider]["updated_at"] if provider in rows else None,
        )
        for provider, label in SUPPORTED_PROVIDERS.items()
    ]


@router.put("/{provider}", response_model=ApiKeyOut)
async def set_api_key(provider: str, body: ApiKeyIn, user: CurrentUser):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    key = body.api_key.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        if key:
            await db.execute(
                """INSERT INTO user_api_keys (username, provider, api_key, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT (username, provider)
                   DO UPDATE SET api_key = excluded.api_key, updated_at = excluded.updated_at""",
                (user["username"], provider, key),
            )
        else:
            # Empty key means "clear" — delete the row instead of storing "".
            await db.execute(
                "DELETE FROM user_api_keys WHERE username = ? AND provider = ?",
                (user["username"], provider),
            )
        await db.commit()

    return ApiKeyOut(provider=provider, label=SUPPORTED_PROVIDERS[provider], api_key=key)


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

    except httpx.RequestError as exc:
        return {"status": "failed", "detail": f"Request error: {exc}"}

    return {"status": "failed", "detail": "Unhandled provider"}
