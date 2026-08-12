"""Seed (or reset) the initial admin user in pktlog.db on the remote server.

Usage:
  PKTLOG_SSH_HOST=<host> PKTLOG_SSH_USER=<user> PKTLOG_SSH_KEY=<path> \\
  PKTLOG_ADMIN_PASSWORD=<password> python3 seed_admin.py
or:
  python3 seed_admin.py --host <host> --user <user> --key <path> \\
      --password <password> [--install-dir /opt/pktlog] [--username admin]

The password is never printed back — pass it in and keep track of it yourself.
"""
import argparse
import os
import sys

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
    parser.add_argument("--install-dir", default=os.environ.get("PKTLOG_INSTALL_DIR", "/opt/pktlog"),
                         help="Remote pktlog install directory (default: /opt/pktlog)")
    parser.add_argument("--username", default=os.environ.get("PKTLOG_ADMIN_USERNAME", "admin"),
                         help="Admin username to create/reset (default: admin)")
    parser.add_argument("--password", default=os.environ.get("PKTLOG_ADMIN_PASSWORD"),
                         help="Admin password to set (required)")
    args = parser.parse_args()
    missing = [name for name, val in (("--host/PKTLOG_SSH_HOST", args.host),
                                       ("--user/PKTLOG_SSH_USER", args.user),
                                       ("--key/PKTLOG_SSH_KEY", args.key),
                                       ("--password/PKTLOG_ADMIN_PASSWORD", args.password)) if not val]
    if missing:
        parser.error(f"missing required value(s): {', '.join(missing)}")
    return args


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
    _known = os.path.expanduser("~/.ssh/known_hosts")
    if os.path.exists(_known):
        try:
            client.load_host_keys(_known)
        except OSError:
            pass
    if os.environ.get("PKT_SSH_TRUST_NEW_HOSTS") == "1":
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(args.host, username=args.user, pkey=key, timeout=15, banner_timeout=15)
    print('Connected')

    # Password/username are passed to the remote Python process via env vars
    # (not interpolated into the script text) so they never appear in logs.
    cmd = f"""
cd {remote_root} && source venv/bin/activate && \
PKTLOG_SEED_USERNAME={args.username!r} PKTLOG_SEED_PASSWORD={args.password!r} \
DB_PATH={remote_root}/pktlog.db python3 - <<'EOF'
import os, sqlite3, bcrypt
DB = os.environ["DB_PATH"]
username = os.environ["PKTLOG_SEED_USERNAME"]
password = os.environ["PKTLOG_SEED_PASSWORD"].encode()
conn = sqlite3.connect(DB)
row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
h = bcrypt.hashpw(password, bcrypt.gensalt()).decode()
if row:
    conn.execute("UPDATE users SET hashed_password=?, is_active=1 WHERE username=?", (h, username))
    conn.commit()
    print(f'Admin user updated (id={{row[0]}})')
else:
    conn.execute(
        "INSERT INTO users (username, email, hashed_password, role, is_active) VALUES (?,?,?,?,?)",
        (username, f'{{username}}@local', h, 'admin', 1)
    )
    conn.commit()
    print(f'Admin user created: username={{username}}')
conn.close()
EOF
"""

    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    print(stdout.read().decode('utf-8', errors='replace'))
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if err:
        print('ERR:', err[:500])

    client.close()
    print('Done')


if __name__ == "__main__":
    main()
