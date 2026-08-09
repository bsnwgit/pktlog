"""
System management endpoints — restart, health, cleanup, etc.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_settings
from app.dependencies import require_admin, CurrentUser
from app.storage.factory import get_storage
from app.version import get_version

log = logging.getLogger("pktlog.system")
router = APIRouter()


@router.get("/info")
async def system_info(user: CurrentUser) -> dict:
    """Version/about info shown on the Settings → System tab."""
    cfg = get_settings()
    return {
        "app_name": "pktLog",
        "version": get_version(),
        "install_dir": cfg.install_dir,
        "github": "https://github.com/bsnwgit/pktlog",
        "license": "PolyForm Noncommercial 1.0.0",
        "developer": "Robert Barnett",
        "contact": "inquiry@barsoftnetware.com",
    }


def _config_file_path() -> Path:
    """Locate the on-disk config.yaml — same candidates used elsewhere in this file."""
    cfg = get_settings()
    for candidate in [Path("config.yaml"), Path(cfg.install_dir) / "config.yaml"]:
        if candidate.exists():
            return candidate
    raise HTTPException(500, "config.yaml not found")


async def _delayed_restart(delay: float = 1.5) -> None:
    """Wait briefly, then signal systemd to restart this service."""
    await asyncio.sleep(delay)
    # Try systemd first; if sudo isn't configured fall back to killing the
    # master uvicorn process (ppid) so systemd restarts the whole thing.
    proc = subprocess.run(
        ["sudo", "systemctl", "restart", "pktlog"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        # Kill the master process; systemd Restart= will revive it.
        # os.getppid() targets the uvicorn master, not just this worker.
        os.kill(os.getppid(), signal.SIGTERM)


@router.post("/cleanup", dependencies=[Depends(require_admin)])
async def run_cleanup() -> dict:
    """
    Manually trigger data retention cleanup:
      - ClickHouse flows: MATERIALIZE TTL (async mutation, uses retention_days_raw)
      - ClickHouse hourly rollup: MATERIALIZE TTL (uses retention_days_hourly)
      - SQLite alert_events + notification_log: synchronous delete (uses alert_event_retention_days)

    Returns eligible row counts for ClickHouse (pre-deletion estimate) and
    exact deleted count for alert events.
    """
    cfg = get_settings()
    storage = get_storage()

    # ── Read retention settings from SQLite ───────────────────────────────────
    retention_raw     = 90
    retention_hourly  = 365
    retention_alerts  = 90
    async with aiosqlite.connect(cfg.db_path) as db:
        async with db.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('retention_days_raw','retention_days_hourly','alert_event_retention_days')"
        ) as cur:
            async for row in cur:
                try:
                    val = int(json.loads(row[1]))
                    if row[0] == 'retention_days_raw':     retention_raw    = val
                    if row[0] == 'retention_days_hourly':  retention_hourly = val
                    if row[0] == 'alert_event_retention_days': retention_alerts = val
                except (ValueError, TypeError):
                    pass

    result: dict = {
        "flows_eligible": 0,
        "hourly_eligible": 0,
        "alert_events_deleted": 0,
        "notification_log_deleted": 0,
        "status": "ok",
    }

    # ── ClickHouse: count eligible rows, then queue TTL materialization ───────
    db_name = cfg.clickhouse_database

    def _ch_cleanup():
        from app.storage.clickhouse import ClickHouseStorage
        if not isinstance(storage, ClickHouseStorage):
            return

        # Count rows beyond retention threshold (estimate for user feedback)
        rows_q = (
            f"SELECT count() FROM {db_name}.flows "
            f"WHERE timestamp < now() - INTERVAL {retention_raw} DAY"
        )
        r = storage._execute(rows_q)
        result["flows_eligible"] = int(r[0][0]) if r else 0

        hourly_q = (
            f"SELECT count() FROM {db_name}.flows_hourly "
            f"WHERE hour < now() - INTERVAL {retention_hourly} DAY"
        )
        r2 = storage._execute(hourly_q)
        result["hourly_eligible"] = int(r2[0][0]) if r2 else 0

        # Queue TTL materialization (async ClickHouse mutations)
        storage._execute(f"ALTER TABLE {db_name}.flows MATERIALIZE TTL")
        storage._execute(f"ALTER TABLE {db_name}.flows_hourly MATERIALIZE TTL")

    try:
        await asyncio.to_thread(_ch_cleanup)
        result["clickhouse_status"] = "queued"
    except Exception as e:
        log.warning(f"ClickHouse cleanup error: {e}")
        result["clickhouse_status"] = f"error: {e}"

    # ── SQLite: delete old alert events synchronously ────────────────────────
    try:
        async with aiosqlite.connect(cfg.db_path) as db:
            cur = await db.execute(
                "DELETE FROM notification_log WHERE event_id IN "
                "(SELECT id FROM alert_events WHERE fired_at < datetime('now', ?))",
                (f"-{retention_alerts} days",),
            )
            result["notification_log_deleted"] = cur.rowcount
            cur2 = await db.execute(
                "DELETE FROM alert_events WHERE fired_at < datetime('now', ?)",
                (f"-{retention_alerts} days",),
            )
            result["alert_events_deleted"] = cur2.rowcount
            await db.commit()
    except Exception as e:
        log.warning(f"Alert event cleanup error: {e}")
        result["alert_events_status"] = f"error: {e}"

    log.info(
        f"Manual cleanup: flows_eligible={result['flows_eligible']}, "
        f"hourly_eligible={result['hourly_eligible']}, "
        f"alert_events_deleted={result['alert_events_deleted']}"
    )
    return result


@router.get("/export", dependencies=[Depends(require_admin)])
async def export_bundle():
    """
    Download a full backup bundle as a .tar.gz archive.
    Includes: SQLite DB + config.yaml + ClickHouse flows (CSVWithNames, gzipped).
    Streams directly to avoid holding large data in memory.
    """
    cfg = get_settings()
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"pktlog-export-{date_str}.tar.gz"

    def _build_archive(out_path: Path) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # ── SQLite DB ────────────────────────────────────────────────────
            db = Path(cfg.db_path)
            if db.exists():
                shutil.copy2(str(db), str(tmp_path / "pktlog.db"))

            # ── config.yaml ──────────────────────────────────────────────────
            for candidate in [Path("config.yaml"), Path(cfg.install_dir) / "config.yaml"]:
                if candidate.exists():
                    shutil.copy2(str(candidate), str(tmp_path / "config.yaml"))
                    break

            # ── ClickHouse flows → flows.csv.gz ──────────────────────────────
            flows_gz = tmp_path / "flows.csv.gz"
            try:
                import gzip as _gzip
                with _gzip.open(str(flows_gz), "wb") as gz_f:
                    proc = subprocess.Popen(
                        [
                            "clickhouse-client",
                            "--host", "localhost",
                            "--port", "9000",
                            "--database", cfg.clickhouse_database,
                            "--query", "SELECT * FROM syslog_events FORMAT CSVWithNames",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    shutil.copyfileobj(proc.stdout, gz_f)
                    proc.wait(timeout=600)
                    if proc.returncode != 0:
                        err = proc.stderr.read().decode("utf-8", errors="replace")
                        log.warning(f"ClickHouse export error: {err}")
            except Exception as e:
                log.warning(f"ClickHouse export failed, skipping: {e}")
                if flows_gz.exists():
                    flows_gz.unlink()

            # ── RESTORE.md ───────────────────────────────────────────────────
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            ch_note = (
                "- flows.csv.gz — ClickHouse syslog_events table (CSVWithNames format, gzip compressed)"
                if flows_gz.exists() and flows_gz.stat().st_size > 100
                else "- flows.csv.gz — NOT included (ClickHouse export failed or table empty)"
            )
            instructions = f"""# pktLog Full Restore Bundle
Generated: {ts}

## Contents
- pktlog.db    — SQLite: users, settings, alert rules
- config.yaml  — Server startup config (ports, ClickHouse connection, JWT secret)
{ch_note}

## Restore procedure (fresh install)
1. Deploy pktLog to the new server per the README.
2. Stop the service:     sudo systemctl stop pktlog
3. Copy pktlog.db     →  {cfg.db_path}
4. Copy config.yaml   →  {cfg.install_dir}/config.yaml
5. Start the service:    sudo systemctl start pktlog

## Restore ClickHouse data (if flows.csv.gz is present)
On the new server, after ClickHouse is running and the pktlog database exists:

  gunzip -c flows.csv.gz | clickhouse-client \\
    --database pktlog \\
    --query "INSERT INTO syslog_events FORMAT CSVWithNames"

## Notes
- The JWT secret in config.yaml will invalidate existing browser sessions —
  users on the old server will need to log in again after restore.
- If you use the UI restore endpoint, the service will need a manual restart
  after restore for config.yaml changes to take effect.
"""
            (tmp_path / "RESTORE.md").write_text(instructions)

            # ── Bundle into tar.gz ────────────────────────────────────────────
            with tarfile.open(str(out_path), "w:gz") as tar:
                for f in tmp_path.iterdir():
                    tar.add(str(f), arcname=f.name)

    # Write to a named temp file so we can stream without holding bytes in RAM
    tmp_out = Path(tempfile.mktemp(suffix=".tar.gz"))
    try:
        await asyncio.to_thread(_build_archive, tmp_out)

        def _iterfile():
            try:
                with open(tmp_out, "rb") as f:
                    while chunk := f.read(65536):
                        yield chunk
            finally:
                tmp_out.unlink(missing_ok=True)

        return StreamingResponse(
            _iterfile(),
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception:
        tmp_out.unlink(missing_ok=True)
        raise


@router.post("/import", dependencies=[Depends(require_admin)])
def _restore_from_dir(src_dir: Path, cfg, files: Optional[set[str]]) -> dict:
    """
    Restore whatever of {pktlog.db, config.yaml, flows.csv.gz} is present in
    src_dir and selected by `files` (None means restore everything present — the
    original all-or-nothing behavior).
    """
    result: dict = {}

    def wanted(name: str) -> bool:
        return files is None or name in files

    # ── SQLite ────────────────────────────────────────────────────────────────
    db_src = src_dir / "pktlog.db"
    if wanted("pktlog.db"):
        if db_src.exists():
            shutil.copy2(str(db_src), cfg.db_path)
            result["pktlog.db"] = "restored"
        else:
            result["pktlog.db"] = "not found in backup"

    # ── config.yaml ───────────────────────────────────────────────────────────
    cfg_src = src_dir / "config.yaml"
    if wanted("config.yaml"):
        if cfg_src.exists():
            shutil.copy2(str(cfg_src), str(Path(cfg.install_dir) / "config.yaml"))
            result["config.yaml"] = "restored (restart required)"
        else:
            result["config.yaml"] = "not found in backup"

    # ── ClickHouse flows ──────────────────────────────────────────────────────
    flows_gz = src_dir / "flows.csv.gz"
    if wanted("flows.csv.gz"):
        if flows_gz.exists() and flows_gz.stat().st_size > 100:
            try:
                cmd = (
                    f"gunzip -c '{flows_gz}' | clickhouse-client "
                    f"--host localhost --database {cfg.clickhouse_database} "
                    f"--query 'INSERT INTO syslog_events FORMAT CSVWithNames'"
                )
                proc = subprocess.run(cmd, shell=True, capture_output=True, timeout=600, text=True)
                result["flows.csv.gz"] = "imported" if proc.returncode == 0 else f"error: {proc.stderr[:300]}"
            except Exception as e:
                result["flows.csv.gz"] = f"error: {e}"
        else:
            result["flows.csv.gz"] = "not present in backup"

    return result


def _parse_files_param(files: Optional[str]) -> Optional[set[str]]:
    if not files:
        return None
    return {f.strip() for f in files.split(",") if f.strip()}


def _safe_tar_extractall(tar: tarfile.TarFile, dest: Path) -> None:
    """
    Extract a tar archive while refusing any member whose resolved path would
    land outside `dest` (path traversal via '../', absolute paths, or symlink
    tricks — CVE-2007-4559-style). Raises ValueError on the first bad member.
    """
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        member_path = (dest / member.name).resolve()
        if member_path != dest_resolved and dest_resolved not in member_path.parents:
            raise ValueError(f"Unsafe path in archive member: {member.name!r}")
        # Also reject symlinks/hardlinks whose link target escapes dest.
        if member.issym() or member.islnk():
            link_target = (dest / member.name).parent / member.linkname
            link_target = link_target.resolve()
            if link_target != dest_resolved and dest_resolved not in link_target.parents:
                raise ValueError(f"Unsafe link target in archive member: {member.name!r}")
    tar.extractall(dest)


@router.post("/import", dependencies=[Depends(require_admin)])
async def import_bundle(file: UploadFile = File(...), files: Optional[str] = Form(None)):
    """
    Restore from a pktlog export bundle (.tar.gz).
    `files` is an optional comma-separated subset of {pktlog.db, config.yaml,
    flows.csv.gz} — omit to restore everything present in the bundle.
    Requires a service restart after restore for config changes to take effect.
    """
    cfg = get_settings()
    data = await file.read()
    wanted = _parse_files_param(files)

    def _do_restore(raw: bytes) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / "upload.tar.gz"
            archive_path.write_bytes(raw)

            try:
                with tarfile.open(str(archive_path), "r:gz") as tar:
                    _safe_tar_extractall(tar, tmp_path)
            except Exception as e:
                return {"error": f"Failed to extract archive: {e}"}

            return _restore_from_dir(tmp_path, cfg, wanted)

    result = await asyncio.to_thread(_do_restore, data)
    return result


@router.post("/backup", dependencies=[Depends(require_admin)])
async def run_backup_now() -> dict:
    """Trigger an immediate backup run regardless of schedule."""
    from app.backup import run_backup_sync
    cfg = get_settings()
    result = await asyncio.to_thread(run_backup_sync, cfg.db_path, cfg.clickhouse_database)
    return result


@router.get("/backup/list", dependencies=[Depends(require_admin)])
async def list_backups() -> list:
    """List existing backup snapshots on the server, newest first."""
    from app.backup import list_backups_sync
    cfg = get_settings()
    return await asyncio.to_thread(list_backups_sync, cfg.db_path)


@router.post("/backup/restore/{snapshot_name}", dependencies=[Depends(require_admin)])
async def restore_from_snapshot(snapshot_name: str, files: Optional[str] = None) -> dict:
    """
    Restore directly from an on-server backup snapshot — no download/upload round trip.
    `files` is an optional comma-separated subset of {pktlog.db, config.yaml,
    flows.csv.gz} — omit to restore everything present in the snapshot.
    """
    from app.backup import _read_backup_settings_sync

    if not re.fullmatch(r"backup_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}", snapshot_name):
        raise HTTPException(status_code=400, detail="Invalid snapshot name")

    cfg = get_settings()
    s = await asyncio.to_thread(_read_backup_settings_sync, cfg.db_path)
    backup_root = Path(s["backup_path"]).resolve()
    snap_dir = (backup_root / snapshot_name).resolve()
    if snap_dir.parent != backup_root or not snap_dir.is_dir():
        raise HTTPException(status_code=404, detail="Snapshot not found")

    wanted = _parse_files_param(files)
    result = await asyncio.to_thread(_restore_from_dir, snap_dir, cfg, wanted)
    return result


@router.post("/test-connection", dependencies=[Depends(require_admin)])
async def test_connection() -> dict:
    """
    Test the currently configured storage backend connection.
    Returns { ok, backend, message } — safe to call at any time, no side effects.
    """
    cfg = get_settings()

    # Read storage_backend from the settings table
    backend = "duckdb"
    async with aiosqlite.connect(cfg.db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE key='storage_backend'") as cur:
            row = await cur.fetchone()
            if row:
                try:
                    backend = json.loads(row[0])
                except Exception:
                    pass

    if backend != "clickhouse":
        return {"ok": True, "backend": backend, "message": "DuckDB is always available (embedded, no connection needed)"}

    # ── ClickHouse test ───────────────────────────────────────────────────────
    def _test() -> tuple[bool, str]:
        try:
            from clickhouse_driver import Client
            client = Client(
                host=cfg.clickhouse_host,
                port=cfg.clickhouse_port,
                database=cfg.clickhouse_database,
                user=cfg.clickhouse_user,
                password=cfg.clickhouse_password,
                connect_timeout=5,
                settings={"use_numpy": False},
            )
            version_rows = client.execute("SELECT version()")
            version = version_rows[0][0] if version_rows else "unknown"
            count_rows = client.execute("SELECT count() FROM syslog_events")
            event_count = int(count_rows[0][0]) if count_rows else 0
            client.disconnect()
            return True, f"Connected — ClickHouse {version} · {event_count:,} rows stored"
        except Exception as e:
            return False, str(e)

    try:
        ok, message = await asyncio.wait_for(asyncio.to_thread(_test), timeout=8.0)
    except asyncio.TimeoutError:
        ok, message = False, "Connection timed out (>8 s) — check host/port and firewall"

    return {"ok": ok, "backend": "clickhouse", "message": message}


# ── SSL certificate management ─────────────────────────────────────────────────

def _ssl_dir() -> Path:
    return Path(get_settings().ssl_dir)


def _cert_file() -> Path:
    return _ssl_dir() / "server.crt"


def _key_file() -> Path:
    return _ssl_dir() / "server.key"


def _cert_info() -> dict:
    """Read cert metadata via openssl CLI. Returns {} on failure."""
    try:
        proc = subprocess.run(
            ["openssl", "x509", "-in", str(_cert_file()), "-noout",
             "-enddate", "-subject", "-issuer"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr.strip()}
        info: dict = {}
        for line in proc.stdout.strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip().lower()
                if "notafter" in k or "enddate" in k:
                    info["expires"] = v.strip()
                elif k == "subject":
                    info["subject"] = v.strip()
                elif k == "issuer":
                    info["issuer"] = v.strip()
        # Parse expiry → days_until_expiry
        try:
            from datetime import datetime, timezone
            exp = datetime.strptime(info["expires"], "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
            info["days_until_expiry"] = (exp - datetime.now(timezone.utc)).days
            info["expires_iso"] = exp.isoformat()
        except Exception:
            pass
        return info
    except Exception as e:
        return {"error": str(e)}


@router.get("/ssl/status", dependencies=[Depends(require_admin)])
async def ssl_status() -> dict:
    """Return current SSL certificate status."""
    if not _cert_file().exists() or not _key_file().exists():
        return {"installed": False}
    info = await asyncio.to_thread(_cert_info)
    return {"installed": True, **info}


@router.post("/ssl/upload", dependencies=[Depends(require_admin)])
async def upload_ssl_cert(
    cert: UploadFile = File(...),
    key: UploadFile = File(...),
) -> dict:
    """Upload and install a PEM certificate + private key."""
    cert_data = await cert.read()
    key_data  = await key.read()

    if b"-----BEGIN CERTIFICATE-----" not in cert_data:
        raise HTTPException(400, "Invalid certificate — must be PEM format (-----BEGIN CERTIFICATE-----)")
    if b"PRIVATE KEY-----" not in key_data:
        raise HTTPException(400, "Invalid private key — must be PEM format (-----BEGIN ... PRIVATE KEY-----)")

    def _save():
        _ssl_dir().mkdir(parents=True, exist_ok=True)
        _cert_file().write_bytes(cert_data)
        _cert_file().chmod(0o644)
        _key_file().write_bytes(key_data)
        _key_file().chmod(0o600)

    await asyncio.to_thread(_save)
    log.info("SSL certificate uploaded and installed")
    info = await asyncio.to_thread(_cert_info)
    return {"installed": True, "status": "saved", **info}


@router.delete("/ssl/cert", dependencies=[Depends(require_admin)])
async def delete_ssl_cert() -> dict:
    """Remove the installed SSL certificate and key."""
    _cert_file().unlink(missing_ok=True)
    _key_file().unlink(missing_ok=True)
    log.info("SSL certificate removed")
    return {"installed": False, "status": "removed"}


@router.post("/ssl/upload-pfx", dependencies=[Depends(require_admin)])
async def upload_ssl_pfx(
    pfx: UploadFile = File(...),
    passphrase: str = Form(...),
) -> dict:
    """
    Accept a PKCS#12 (.pfx/.p12) bundle + passphrase.
    Extracts the cert and private key as unencrypted PEM files
    (server.crt / server.key) so Uvicorn can load them without interaction.
    """
    pfx_data = await pfx.read()

    def _extract() -> tuple[bool, str]:
        import tempfile
        _ssl_dir().mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mktemp(suffix=".pfx"))
        tmp.write_bytes(pfx_data)
        try:
            # Extract certificate chain
            cert_proc = subprocess.run(
                ["openssl", "pkcs12",
                 "-in", str(tmp),
                 "-clcerts", "-nokeys",
                 "-passin", f"pass:{passphrase}",
                 "-out", str(_cert_file())],
                capture_output=True, text=True, timeout=15,
            )
            if cert_proc.returncode != 0:
                err = cert_proc.stderr.strip()
                return False, f"Cert extraction failed: {err}"

            # Extract private key — -nodes strips the passphrase
            key_proc = subprocess.run(
                ["openssl", "pkcs12",
                 "-in", str(tmp),
                 "-nocerts", "-nodes",
                 "-passin", f"pass:{passphrase}",
                 "-out", str(_key_file())],
                capture_output=True, text=True, timeout=15,
            )
            if key_proc.returncode != 0:
                err = key_proc.stderr.strip()
                return False, f"Key extraction failed: {err}"

            _cert_file().chmod(0o644)
            _key_file().chmod(0o600)
            return True, "ok"
        finally:
            tmp.unlink(missing_ok=True)

    ok, msg = await asyncio.to_thread(_extract)
    if not ok:
        raise HTTPException(400, msg)

    log.info("SSL certificate installed from PFX")
    info = await asyncio.to_thread(_cert_info)
    return {"installed": True, "status": "saved", **info}


@router.post("/restart", dependencies=[Depends(require_admin)])
async def restart_service() -> dict:
    """
    Restart the pktLog service.  Response is sent immediately; the
    service process exits ~1.5 s later and systemd brings it back up.
    Requires admin role.
    """
    log.warning("Service restart requested via API")
    asyncio.create_task(_delayed_restart())
    return {"status": "restarting", "message": "Service will restart in ~2 seconds"}


class PortUpdate(BaseModel):
    port: int


@router.get("/port", dependencies=[Depends(require_admin)])
async def get_port() -> dict:
    """
    The port pktLog listens on. Lives in config.yaml (startup config, read
    before the DB connects) rather than the SQLite-backed settings table —
    see app/config.py. start.sh reads this same field to pick the bind port.
    """
    return {"port": get_settings().port}


@router.post("/port", dependencies=[Depends(require_admin)])
async def set_port(body: PortUpdate) -> dict:
    """
    Update the listen port in config.yaml. Takes effect on the next service
    restart — this only writes the file, it doesn't restart anything itself.
    """
    if not (1 <= body.port <= 65535):
        raise HTTPException(400, "Port must be between 1 and 65535")

    path = _config_file_path()
    text = path.read_text()
    new_line = f"port: {body.port}"
    if re.search(r"(?m)^port:\s*\d+", text):
        text = re.sub(r"(?m)^port:\s*\d+", new_line, text, count=1)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    path.write_text(text)

    log.warning(f"Listen port changed to {body.port} in config.yaml — restart required to take effect")
    return {"port": body.port, "message": "Saved — restart the service to apply"}
