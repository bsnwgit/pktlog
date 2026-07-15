"""
CRUD /api/users — admin user management.
"""
from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional

from app.auth.local import hash_password, verify_password
from app.database import get_db
from app.dependencies import AdminUser, CurrentUser

router = APIRouter()


class UserIn(BaseModel):
    username: str
    email: str
    password: Optional[str] = None
    role: str = "viewer"


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    created_at: str
    last_login: Optional[str]
    has_password: bool = True
    auth_provider: str = "local"


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


def _user_to_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    # created_at/last_login come from SQLite's datetime('now'), which is UTC
    # but has no timezone marker — browsers parse that as local time, not
    # UTC. Normalize to a proper UTC ISO string.
    for col in ("created_at", "last_login"):
        if d.get(col):
            d[col] = d[col].replace(" ", "T") + "Z"
    return d


class PasswordReset(BaseModel):
    new_password: str


# ── /me endpoints (must be before /{user_id} to avoid path collision) ──────────

@router.get("/me", response_model=UserOut)
async def get_me(user: CurrentUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT hashed_password, auth_provider FROM users WHERE id=?", (user["id"],)) as cur:
        row = await cur.fetchone()
    has_pw = bool(row and row["hashed_password"])
    provider = (row["auth_provider"] if row else None) or "local"
    return {**dict(user), "has_password": has_pw, "auth_provider": provider}


@router.patch("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    body: PasswordChange,
    user: CurrentUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Any logged-in user can change their own password after verifying the current one."""
    async with db.execute(
        "SELECT hashed_password FROM users WHERE id=?", (user["id"],)
    ) as cur:
        row = await cur.fetchone()
    if not row or not verify_password(body.current_password, row["hashed_password"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    await db.execute(
        "UPDATE users SET hashed_password=? WHERE id=?",
        (hash_password(body.new_password), user["id"]),
    )
    await db.commit()


# ── collection endpoints ───────────────────────────────────────────────────────

@router.get("/", response_model=list[UserOut])
async def list_users(_: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT id, username, email, role, is_active, created_at, last_login FROM users ORDER BY username"
    ) as cur:
        rows = await cur.fetchall()
    return [_user_to_dict(r) for r in rows]


@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserIn, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    hashed = hash_password(body.password) if body.password else None
    try:
        async with db.execute(
            "INSERT INTO users (username, email, hashed_password, role) VALUES (?,?,?,?) RETURNING *",
            (body.username, body.email, hashed, body.role),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        return _user_to_dict(row)
    except aiosqlite.IntegrityError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── single-user endpoints ──────────────────────────────────────────────────────

@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserIn,
    _: AdminUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    update_fields = "username=?, email=?, role=?"
    params = [body.username, body.email, body.role]
    if body.password:
        update_fields += ", hashed_password=?"
        params.append(hash_password(body.password))
    params.append(user_id)
    await db.execute(f"UPDATE users SET {update_fields} WHERE id=?", params)
    await db.commit()
    async with db.execute(
        "SELECT id, username, email, role, is_active, created_at, last_login FROM users WHERE id=?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_dict(row)


@router.patch("/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(user_id: int, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
    await db.commit()


@router.patch("/{user_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
async def activate_user(user_id: int, _: AdminUser, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user_id,))
    await db.commit()


@router.patch("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: int,
    body: PasswordReset,
    _: AdminUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin-only: set a user's password without knowing the current one."""
    if not body.new_password or len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    await db.execute(
        "UPDATE users SET hashed_password=? WHERE id=?",
        (hash_password(body.new_password), user_id),
    )
    await db.commit()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, caller: CurrentUser, db: aiosqlite.Connection = Depends(get_db)):
    if caller["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    if caller["id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
