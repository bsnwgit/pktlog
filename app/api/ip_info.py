"""
GET /api/ip-info/{ip} — combined IP intelligence (ipinfo.io) + reputation
(AbuseIPDB) lookup for a single public IP, using the current user's own
stored API keys (see app/api/user_api_keys.py). Private/loopback/link-local
addresses are rejected — external providers have nothing useful to say
about them.
"""
from __future__ import annotations

import ipaddress

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import DB_PATH
from app.dependencies import CurrentUser

router = APIRouter()


class IpInfoResult(BaseModel):
    ip: str
    ipinfo: dict | None = None
    ipinfo_error: str | None = None
    abuseipdb: dict | None = None
    abuseipdb_error: str | None = None


async def _get_user_key(username: str, provider: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT api_key FROM user_api_keys WHERE username = ? AND provider = ?",
            (username, provider),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else ""


@router.get("/{ip}", response_model=IpInfoResult)
async def get_ip_info(ip: str, user: CurrentUser):
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid IP address: {ip}")

    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved or ip_obj.is_multicast:
        raise HTTPException(status_code=400, detail="IP info lookup is only available for public addresses")

    result = IpInfoResult(ip=ip)
    ipinfo_key = await _get_user_key(user["username"], "ipinfo")
    abuseipdb_key = await _get_user_key(user["username"], "abuseipdb")

    async with httpx.AsyncClient(timeout=10) as client:
        if ipinfo_key:
            try:
                resp = await client.get(f"https://ipinfo.io/{ip}/json", params={"token": ipinfo_key})
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                if resp.status_code == 200 and "error" not in data:
                    result.ipinfo = data
                else:
                    result.ipinfo_error = data.get("error", {}).get("message") or f"ipinfo.io returned HTTP {resp.status_code}"
            except httpx.RequestError as exc:
                result.ipinfo_error = f"Request error: {exc}"
        else:
            result.ipinfo_error = "No ipinfo.io key configured — add one in Settings → API Keys"

        if abuseipdb_key:
            try:
                resp = await client.get(
                    "https://api.abuseipdb.com/api/v2/check",
                    params={"ipAddress": ip, "maxAgeInDays": 90},
                    headers={"Key": abuseipdb_key, "Accept": "application/json"},
                )
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                if resp.status_code == 200:
                    result.abuseipdb = data.get("data")
                else:
                    errors = data.get("errors") or []
                    result.abuseipdb_error = (errors[0].get("detail") if errors else None) or f"AbuseIPDB returned HTTP {resp.status_code}"
            except httpx.RequestError as exc:
                result.abuseipdb_error = f"Request error: {exc}"
        else:
            result.abuseipdb_error = "No AbuseIPDB key configured — add one in Settings → API Keys"

    return result
