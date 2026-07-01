"""
Project backup script — 2-rotation local backup.

Usage: python backup.py

Keeps two snapshots alongside the project folder:
  ../pktLog_backups/backup_1/  ← most recent
  ../pktLog_backups/backup_2/  ← previous

On each run:
  1. Drop backup_2 (if exists)
  2. Promote backup_1 → backup_2 (if exists)
  3. Copy current project → backup_1

To restore: copy files from backup_1/ back into the project folder,
then redeploy to pkt server if applicable.
"""

import shutil, sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from datetime import datetime, timezone

# ── Project path ──────────────────────────────────────────────────────────────
PROJECT_DIR = Path(r"C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog")
# ─────────────────────────────────────────────────────────────────────────────

BACKUP_BASE = PROJECT_DIR.parent / f"{PROJECT_DIR.name}_backups"
b1 = BACKUP_BASE / "backup_1"
b2 = BACKUP_BASE / "backup_2"

IGNORE = shutil.ignore_patterns(
    "node_modules", "__pycache__", ".git", "*.pyc", "*.pyo",
    "venv", ".venv", "*_backups", "*.log", ".DS_Store",
)

def fmt_size(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    mb = total / 1024 / 1024
    return f"{mb:.0f}M" if mb >= 1 else f"{total / 1024:.0f}K"

BACKUP_BASE.mkdir(parents=True, exist_ok=True)

def _force_remove(func, path, exc_info):
    """Error handler for rmtree — clears read-only flag then retries."""
    import os, stat
    os.chmod(path, stat.S_IWRITE)
    func(path)

if b2.exists():
    shutil.rmtree(b2, onexc=_force_remove)
    print("Dropped old backup_2")

if b1.exists():
    b1.rename(b2)
    print("Promoted backup_1 → backup_2")

shutil.copytree(PROJECT_DIR, b1, ignore=IGNORE)
ts = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S UTC %Y")
print(f"Created backup_1 at {ts}")
print(f"{fmt_size(b1)}\t{b1}")

print()
for d in sorted(BACKUP_BASE.iterdir()):
    if d.is_dir():
        mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"  {d.name}/  {fmt_size(d)}  ({mtime})")

print("\nBackup complete.")
