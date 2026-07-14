"""
Initial deployment of pktLog to a remote server over SSH.

Creates the install directory, copies backend files, sets up the venv,
initializes the database, installs and starts the systemd service.

This mirrors what install.sh does when run locally on the server, but
pushes files from a local checkout over SFTP instead — useful when you
don't have a shell on the target host but do have SSH/SFTP access.
Note: unlike install.sh, this script does NOT install system apt packages
or ClickHouse — the target host must already have those set up.

Run ONCE for a fresh install. For subsequent updates use deploy_backend.py /
deploy_fe.py.

Usage:
  PKTLOG_SSH_HOST=<host> PKTLOG_SSH_USER=<user> PKTLOG_SSH_KEY=<path> \\
  PKTLOG_LOCAL_ROOT=/path/to/local/checkout python3 deploy_initial.py
or:
  python3 deploy_initial.py --host <host> --user <user> --key <path> \\
      --local-root /path/to/local/checkout [--install-dir /opt/pktlog] [--port 8768]
"""
import argparse
import os
import sys
import time
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
    parser.add_argument("--log-dir", default=os.environ.get("PKTLOG_LOG_DIR"),
                         help="Remote pktlog log directory (default: <install-dir>/logs)")
    parser.add_argument("--port", default=os.environ.get("PKTLOG_PORT", "8768"),
                         help="Port pktlog listens on (default: 8768)")
    args = parser.parse_args()
    if not args.log_dir:
        args.log_dir = f"{args.install_dir}/logs"
    missing = [name for name, val in (("--host/PKTLOG_SSH_HOST", args.host),
                                       ("--user/PKTLOG_SSH_USER", args.user),
                                       ("--key/PKTLOG_SSH_KEY", args.key)) if not val]
    if missing:
        parser.error(f"missing required value(s): {', '.join(missing)}")
    return args


def main():
    args = parse_args()
    remote_root = args.install_dir

    key = paramiko.RSAKey.from_private_key_file(args.key)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, username=args.user, pkey=key, timeout=15, banner_timeout=15)
    print('Connected')

    sftp = client.open_sftp()

    def run(label, cmd, timeout=60):
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode('utf-8', errors='replace').strip()
        err = stderr.read().decode('utf-8', errors='replace').strip()
        print(f'[{label}]')
        if out:
            print(out)
        if err:
            print('  ERR:', err[:400])
        return out

    def ensure_remote_dir(path):
        run(f'mkdir {path}', f'mkdir -p {path}')

    def sftp_put_tree(local_dir, remote_dir):
        ensure_remote_dir(remote_dir)
        for item in sorted(os.listdir(local_dir)):
            if item.startswith('.') or item == '__pycache__' or item.endswith('.pyc'):
                continue
            local_path = os.path.join(local_dir, item)
            remote_path = remote_dir + '/' + item
            if os.path.isdir(local_path):
                sftp_put_tree(local_path, remote_path)
            else:
                sftp.put(local_path, remote_path)
                print(f'  PUT {remote_path.replace(remote_root, "")}')

    # ── 1. Create directory structure ─────────────────────────────────────────
    print('\n[1/7] Creating directories...')
    for d in [remote_root, f'{remote_root}/app', f'{remote_root}/frontend',
              f'{remote_root}/frontend/dist', args.log_dir]:
        ensure_remote_dir(d)

    # ── 2. Copy backend files ───────────────────────────────────────────────────
    print('\n[2/7] Copying backend files...')
    for subdir in ['app', 'migrations']:
        local = os.path.join(args.local_root, subdir)
        if os.path.isdir(local):
            sftp_put_tree(local, f'{remote_root}/{subdir}')

    for fname in ['requirements.txt', 'config.example.yaml', 'pktlog.service']:
        local = os.path.join(args.local_root, fname)
        if os.path.exists(local):
            sftp.put(local, f'{remote_root}/{fname}')
            print(f'  PUT /{fname}')

    # ── 3. Create config.yaml ───────────────────────────────────────────────────
    print('\n[3/7] Creating config.yaml...')
    config_exists = run('config exists?', f'test -f {remote_root}/config.yaml && echo yes || echo no')
    if config_exists.strip() == 'yes':
        print('  config.yaml already exists — skipping')
    else:
        secret = run('gen secret', 'openssl rand -hex 32').strip()
        run('create config', f"""
sed 's/CHANGE_ME_generate_with_openssl_rand_hex_32/{secret}/' \
    {remote_root}/config.example.yaml > {remote_root}/config.yaml
""")
        print('  config.yaml created with generated secret_key')

    # ── 4. Python virtualenv + dependencies ─────────────────────────────────────
    print('\n[4/7] Setting up Python virtualenv...')
    venv = f'{remote_root}/venv'
    venv_exists = run('venv exists?', f'test -f {venv}/bin/python && echo yes || echo no')
    if venv_exists.strip() == 'yes':
        print('  venv already exists — skipping creation')
    else:
        run('create venv', f'python3 -m venv {venv}', timeout=30)
        print('  venv created')

    print('  Installing requirements (this may take 2-3 minutes)...')
    run('pip upgrade', f'{venv}/bin/pip install --quiet --upgrade pip', timeout=60)
    run('pip install', f'{venv}/bin/pip install --quiet -r {remote_root}/requirements.txt 2>&1 | tail -5', timeout=300)
    print('  Dependencies installed')

    # ── 5. Initialize database ──────────────────────────────────────────────────
    print('\n[5/7] Initializing database...')
    init_script = f"""
import asyncio, sys, os
sys.path.insert(0, '{remote_root}')
os.environ['PKTLOG_CONFIG'] = '{remote_root}/config.yaml'
from app.database import init_db
import asyncio
asyncio.run(init_db())
print('Database initialized')
"""
    run('init db', f'{venv}/bin/python3 -c "{init_script.strip()}"', timeout=30)

    # ── 6. Install systemd service ──────────────────────────────────────────────
    print('\n[6/7] Installing systemd service...')
    run('copy service', f'sudo cp {remote_root}/pktlog.service /etc/systemd/system/pktlog.service')
    run('daemon reload', 'sudo systemctl daemon-reload')
    run('enable service', 'sudo systemctl enable pktlog')

    # ── 7. Start service ────────────────────────────────────────────────────────
    print('\n[7/7] Starting pktlog service...')
    run('start', 'sudo systemctl restart pktlog')
    time.sleep(5)
    status = run('status', 'systemctl is-active pktlog')
    run('health', f'curl -s http://localhost:{args.port}/api/health 2>/dev/null || echo "(no /api/health yet)"')
    run('log tail', f'tail -20 {args.log_dir}/pktlog.log 2>/dev/null || echo "(no log yet)"')

    sftp.close()
    client.close()
    print(f'\nDone — service is {status.strip()}')
    print(f'URL: http://{args.host}:{args.port}')


if __name__ == "__main__":
    main()
