"""
Minimal stand-in for resonance's /embed/session, per the documented contract.
Lets the pkt-app side be developed and tested without reaching a real server.

Behaviour is driven by the key so every branch is reachable:
  good.<secret>      -> 200 with a code
  disabled.<secret>  -> 403
  adminport.<secret> -> 404
  backoff.<secret>   -> 429
  anything else      -> 401
"""
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

SESSION_TTL = 28800   # 480 minutes, matching pktlog's own session timeout
CODE_TTL = 60


@app.post("/embed/session")
async def embed_session(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "not an object"}, status_code=400)

    key = (body.get("key") or "").strip()
    eid = key.split(".")[0] if "." in key else ""

    if eid == "disabled":
        return JSONResponse({"error": "this key is disabled"}, status_code=403)
    if eid == "adminport":
        return JSONResponse({"error": "not found"}, status_code=404)
    if eid == "backoff":
        return JSONResponse({"error": "too many failed attempts"}, status_code=429)
    if eid != "good":
        return JSONResponse({"error": "key not recognised"}, status_code=401)

    user = body.get("user") or {}
    if not user.get("id"):
        return JSONResponse({"error": "this key needs a person"}, status_code=400)

    code = secrets.token_urlsafe(16)
    return {
        "code": code,
        "src": f"/embed?c={code}",
        "code_expires_in": CODE_TTL,
        "expires_in": SESSION_TTL,
        "parts": ["visual", "transcript", "input", "mode", "talk", "audio", "text"],
        "cap": {"ask": True, "mic": False, "speak": True,
                "rate_per_min": 20, "rate_per_visitor": 10},
        "_echo_user": user,
    }
