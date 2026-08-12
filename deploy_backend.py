"""Push backend files and restart pktlog.

Usage:
  PKTLOG_SSH_HOST=<host> PKTLOG_SSH_USER=<user> PKTLOG_SSH_KEY=<path> \\
  PKTLOG_LOCAL_ROOT=/path/to/local/checkout python3 deploy_backend.py
or:
  python3 deploy_backend.py --host <host> --user <user> --key <path> \\
      --local-root /path/to/local/checkout [--install-dir /opt/pktlog] [--port 8768]
"""
import argparse
import os
import sys
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("PKTLOG_SSH_HOST"),
                         help="SSH host/IP of the pktlog server")
    parser.add_argument("--user", default=os.environ.get("PKTLOG_SSH_USER"),
                         help="SSH username")
    parser.add_argument("--key", default=os.environ.get("PKTLOG_SSH_KEY"),
                         help="Path to SSH private key")
    parser.add_argument("--local-root",
                         default=os.environ.get("PKTLOG_LOCAL_ROOT", str(Path(__file__).resolve().parent)),
                         help="Local project root to push from (default: this script's directory)")
    parser.add_argument("--install-dir", default=os.environ.get("PKTLOG_INSTALL_DIR", "/opt/pktlog"),
                         help="Remote pktlog install directory (default: /opt/pktlog)")
    parser.add_argument("--port", default=os.environ.get("PKTLOG_PORT", "8768"),
                         help="Port pktlog listens on, for the post-deploy health check (default: 8768)")
    args = parser.parse_args()
    missing = [name for name, val in (("--host/PKTLOG_SSH_HOST", args.host),
                                       ("--user/PKTLOG_SSH_USER", args.user),
                                       ("--key/PKTLOG_SSH_KEY", args.key)) if not val]
    if missing:
        parser.error(f"missing required value(s): {', '.join(missing)}")
    return args


def run(client, label, cmd, timeout=30):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}] {out or ""} {("ERR:" + err[:200]) if err else ""}')


# Files to push (relative to --local-root)
BACKEND_FILES = [
    # Core app
    'app/main.py',
    'app/config.py',
    'app/database.py',
    'app/dependencies.py',
    'app/backup.py',
    'app/logging_handler.py',
    # Auth
    'app/auth/__init__.py',
    # Alerts
    'app/alerts/__init__.py',
    'app/alerts/engine.py',
    # API
    'app/api/settings.py',
    'app/api/users.py',
    'app/api/system.py',
    'app/api/logs.py',
    'app/api/pktlog.py',
    'app/api/syslog.py',
    'app/api/collectors.py',
    # Models
    'app/models/__init__.py',
    'app/models/syslog.py',
    # Storage
    'app/storage/__init__.py',
    'app/storage/base.py',
    'app/storage/factory.py',
    'app/storage/clickhouse.py',
    'app/storage/duckdb.py',
    # Ingest
    'app/ingest/__init__.py',
    'app/ingest/parser.py',
    'app/ingest/normalizer.py',
    'app/ingest/writer.py',
    'app/ingest/listener.py',
    # Migrations
    'migrations/001_initial.sql',
    'migrations/002_app_logs.sql',
    'migrations/003_collector_registry.sql',
    'migrations/004_sampler_dismissals.sql',
]


def main():
    args = parse_args()
    remote_root = args.install_dir

    key = paramiko.RSAKey.from_private_key_file(args.key)
    client = paramiko.SSHClient()
    # Verify the host key rather than trusting whatever is presented first.
    # AutoAddPolicy made the initial connection — the one that establishes
    # trust — unauthenticated, so anything in between could impersonate the
    # target and capture the SSH credentials. Connect once by hand to record
    # the key, or set PKT_SSH_TRUST_NEW_HOSTS=1 to accept a new one.
    client.load_system_host_keys()
    for _known in (os.environ.get("PKT_SSH_KNOWN_HOSTS"),
                   os.path.expanduser("~/.ssh/known_hosts")):
        if _known and os.path.exists(_known):
            try:
                client.load_host_keys(_known)
            except OSError:
                pass
    # RejectPolicy unconditionally. An earlier version of this fix kept an
    # AutoAddPolicy escape hatch behind an environment variable, which is
    # exactly the blind first-contact trust the fix exists to remove — it just
    # moved it behind a flag. Point PKT_SSH_KNOWN_HOSTS at a file instead: a
    # host key can be recorded deliberately, which is auditable, where
    # "accept whatever answers this time" is not.
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(args.host, username=args.user, pkey=key, timeout=15, banner_timeout=15)
    print('Connected')

    # Ensure remote directories exist before SFTPing
    run(client, 'mkdir-ingest', f'mkdir -p {remote_root}/app/ingest')
    run(client, 'mkdir-models', f'mkdir -p {remote_root}/app/models')
    run(client, 'mkdir-storage', f'mkdir -p {remote_root}/app/storage')
    run(client, 'mkdir-migrations', f'mkdir -p {remote_root}/migrations')

    sftp = client.open_sftp()
    for rel in BACKEND_FILES:
        local = os.path.join(args.local_root, rel)
        remote = f'{remote_root}/{rel}'
        if os.path.exists(local):
            sftp.put(local, remote)
            print(f'  PUT {rel}')
        else:
            print(f'  SKIP {rel} (not found locally)')
    sftp.close()

    run(client, 'restart', 'sudo systemctl restart pktlog')
    run(client, 'status', 'sleep 4 && systemctl is-active pktlog')
    run(client, 'health', f'curl -sk https://localhost:{args.port}/api/health || echo "not ready"')

    client.close()
    print('Done')


if __name__ == "__main__":
    main()
