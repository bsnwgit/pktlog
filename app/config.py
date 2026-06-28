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
        default=_yaml_cfg.get("db_path", "/mnt/software/pktlog/pktlog.db")
    )

    # ── ClickHouse (startup connection — overridable at runtime via settings) ──
    clickhouse_host: str = Field(default=_yaml_cfg.get("clickhouse_host", "localhost"))
    clickhouse_port: int = Field(default=_yaml_cfg.get("clickhouse_port", 9000))
    clickhouse_database: str = Field(default=_yaml_cfg.get("clickhouse_database", "pktlog"))
    clickhouse_user: str = Field(default=_yaml_cfg.get("clickhouse_user", "default"))
    clickhouse_password: str = Field(default=_yaml_cfg.get("clickhouse_password", ""))

    # ── DuckDB (alternate backend) ─────────────────────────────────────────────
    duckdb_path: str = Field(
        default=_yaml_cfg.get("duckdb_path", "/mnt/software/pktlog/flows.duckdb")
    )

    # ── JWT ───────────────────────────────────────────────────────────────────
    secret_key: str = Field(
        default=_yaml_cfg.get("secret_key", "CHANGE_ME_IN_PRODUCTION_secret_key_32chars")
    )
    algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # ── CORS ──────────────────────────────────────────────────────────────────
    # In production, set this to your actual origin (e.g. http://10.20.30.5:8768)
    cors_origins: list[str] = Field(
        default=_yaml_cfg.get("cors_origins", ["*"])
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(default=_yaml_cfg.get("log_level", "info"))
    log_file: str = Field(
        default=_yaml_cfg.get("log_file", "/mnt/software/logs/pktlog.log")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
