"""
HTTP client for resonance's /embed/session endpoint.

One call, one job: hand resonance the app's key plus the identity of the user
who is asking, and get back a short-lived single-use code the browser can spend
on /embed?c=<code>. The key never leaves the server; resonance never sees the
app's own credentials.

TLS verification is on and not configurable. A browser-trusted certificate is a
documented prerequisite for resonance — embed.js is loaded by the browser, and
there is no verify=False for browsers. Verifying here too means a certificate
problem surfaces as a clear error in Test Connection rather than working
server-side and failing silently for every user with a blank frame.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import APP_SLUG
from .errors import ResonanceNotConfigured, ResonanceUnreachable, for_status

log = logging.getLogger("pktlog.resonance.client")

# Resonance normalises user.id to 64 chars and drops the whole user object if it
# is missing — so a long login must be truncated here, not silently discarded
# there. Roles are capped at 16 entries of 32 chars on their side; matching the
# caps locally keeps what we send equal to what gets recorded.
MAX_ID_LEN = 64
MAX_ROLE_LEN = 32
MAX_ROLES = 16

DEFAULT_TIMEOUT = 10.0


def build_user_id(username: str) -> str:
    """'pktlog-alice' — app and login together, so resonance's logs show both.

    The prefix comes from the vendored APP_SLUG constant rather than a setting:
    an admin must not be able to make their install report itself as a different
    pkt app in a shared audit trail.
    """
    return f"{APP_SLUG}-{username}"[:MAX_ID_LEN]


def _clean_roles(roles: list[str] | None) -> list[str]:
    if not roles:
        return []
    out = [str(r).strip()[:MAX_ROLE_LEN] for r in roles if str(r).strip()]
    return out[:MAX_ROLES]


class ResonanceClient:
    def __init__(self, base_url: str, key: str, *, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.key = (key or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.key)

    async def create_session(self, username: str, roles: list[str] | None = None) -> dict[str, Any]:
        """POST /embed/session. Returns resonance's 200 body verbatim:
        code, src, code_expires_in, expires_in, parts, cap.

        The full body is passed through rather than reduced to the code, because
        the Settings panel renders what the key actually grants — ask/mic/speak,
        the rate limits, the session TTL — from a real call instead of asking an
        admin to retype it from the resonance side.
        """
        if not self.configured:
            raise ResonanceNotConfigured()

        payload = {
            "key": self.key,
            "user": {"id": build_user_id(username), "roles": _clean_roles(roles)},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/embed/session", json=payload)
        except httpx.TimeoutException as exc:
            raise ResonanceUnreachable(f"timed out after {self.timeout}s") from exc
        except httpx.ConnectError as exc:
            # Certificate failures land here too, and are the likeliest cause on
            # a first install — say so rather than reporting a bare connect error.
            raise ResonanceUnreachable(
                f"{exc}",
                admin_message=(
                    "Could not reach the resonance server — check the address, "
                    "and that its certificate is trusted by this host."
                ),
            ) from exc
        except httpx.HTTPError as exc:
            raise ResonanceUnreachable(str(exc)) from exc

        if resp.status_code != 200:
            detail = ""
            try:
                detail = (resp.json() or {}).get("error", "")
            except Exception:
                detail = (resp.text or "")[:200]
            raise for_status(resp.status_code, detail)

        try:
            body = resp.json()
        except Exception as exc:
            raise ResonanceUnreachable("resonance returned a non-JSON 200") from exc

        if not body.get("code"):
            raise ResonanceUnreachable("resonance returned no code")

        return body
