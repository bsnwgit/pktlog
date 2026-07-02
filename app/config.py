"""
pktLog configuration.

Priority order (highest → lowest):
  1. Environment variables  (PKTLOG_*)
  2. config.yaml in CWD or /mnt/software/pktlog/
  3. Defaults defined here

Runtime settings (storage backend, retention days, etc.) are stored in SQLite
and loaded via SettingsStore; those are NOT in this file.
This file only covers startup/infrastructure settings that must be known
before the database is connected.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_yaml() -> dict:
    """Try known config file locations and return parsed YAML, or {}."""
    candidates = [
        Path("config.yaml"),
        Path("/app/data/config.yaml"),
        Path("/mnt/software/pktlog/config.yaml"),
        Path.home() / ".pktlog" / "config.yaml",
    ]
    env_path = os.environ.get("PKTLOG_CONFIG")
    if env_path:
        candidates.insert(0, Path(env_path))

    for path in candidates:
        if path.exists():
            with path.open() as f:
                return yaml.safe_load(f) or {}
    return {}


_yaml_cfg = _load_yaml()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PKTLOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = Field(default=_yaml_cfg.get("host", "0.0.0.0"))
    port: int = Field(default=_yaml_cfg.get("port", 8080))
    workers: int = Field(default=_yaml_cfg.get("workers", 2))
    debug: bool = Field(default=_yaml_cfg.get("debug", False))

    # ── App database (SQLite sidecar) ──────────────────────────────────────────
    db_path: str = Field(
        default=_yaml_cfg.get("db_path", "/app/data/pktlog.db")
    )

    # ── ClickHouse (startup connection — overridable at runtime via settings) ──
    clickhouse_host: str = Field(default=_yaml_cfg.get("clickhouse_host", "localhost"))
    clickhouse_port: int = Field(default=_yaml_cfg.get("clickhouse_port", 9000))
    clickhouse_database: str = Field(default=_yaml_cfg.get("clickhouse_database", "pktlog"))
    clickhouse_user: str = Field(default=_yaml_cfg.get("clickhouse_user", "default"))
    clickhouse_password: str = Field(default=_yaml_cfg.get("clickhouse_password", ""))

    # ── Syslog ingest ─────────────────────────────────────────────────────────
    syslog_port: int = Field(default=_yaml_cfg.get("syslog_port", 514))

    # ── DuckDB (alternate backend) ─────────────────────────────────────────────
    duckdb_path: str = Field(
        default=_yaml_cfg.get("duckdb_path", "/app/data/pktlog_data.duckdb")
    )

    # ── JWT ───────────────────────────────────────────────────────────────────
    secret_key: str = Field(
        default=_yaml_cfg.get("secret_key", "CHANGE_ME_IN_PRODUCTION_secret_key_32chars")
    )
    algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # ── CORS ──────────────────────────────────────────────────────────────────
    # In production, set this to your actual origin (e.g. http://<PKT_SERVER_IP>:8768)
    cors_origins: list[str] = Field(
        default=_yaml_cfg.get("cors_origins", ["*"])
    )

    # ── pktSuite integration ─────────────────────────────────────────────────
    # Token must match the suite_token registered in pktHub App Registry.
    # Leave blank to disable suite token auth.
    suite_token: str = Field(default=_yaml_cfg.get("suite_token", ""))

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(default=_yaml_cfg.get("log_level", "info"))
    log_file: str = Field(
        default=_yaml_cfg.get("log_file", "/app/data/logs/pktlog.log")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# suite_token_from_sqlite_patch — reads token from SQLite so /api/suite/register
# takes effect immediately without service restart.
_patched_get_settings = get_settings  # noqa: save original if it exists

def get_settings() -> Settings:  # type: ignore[misc]
    s = Settings()
    try:
        import sqlite3 as _sq, json as _j
        _db_path = s.db_path
        _conn = _sq.connect(_db_path)
        _row = _conn.execute("SELECT value FROM settings WHERE key='suite_token'").fetchone()
        _conn.close()
        if _row and _row[0]:
            _val = _row[0]
            _tok = _j.loads(_val) if _val.startswith('"') else _val
            if _tok:
                s = s.model_copy(update={'suite_token': _tok})
    except Exception:
        pass
    return s
