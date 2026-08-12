"""Build frontend on the remote server and deploy it to pktlog.

NOTE: Frontend build MUST run on the remote Linux host — never on Windows
(Windows node_modules lacks the Linux rollup native binary).
This script syncs src files, triggers npm build remotely, then copies dist.

Usage:
  PKTLOG_SSH_HOST=<host> PKTLOG_SSH_USER=<user> PKTLOG_SSH_KEY=<path> \\
  PKTLOG_LOCAL_ROOT=/path/to/local/checkout python3 deploy_fe.py
or:
  python3 deploy_fe.py --host <host> --user <user> --key <path> \\
      --local-root /path/to/local/checkout [--install-dir /opt/pktlog]
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
    args = parser.parse_args()
    missing = [name for name, val in (("--host/PKTLOG_SSH_HOST", args.host),
                                       ("--user/PKTLOG_SSH_USER", args.user),
                                       ("--key/PKTLOG_SSH_KEY", args.key)) if not val]
    if missing:
        parser.error(f"missing required value(s): {', '.join(missing)}")
    return args


def sftp_put_dir(sftp, local_dir, remote_dir):
    try:
        sftp.stat(remote_dir)
    except FileNotFoundError:
        sftp.mkdir(remote_dir)
    for item in os.listdir(local_dir):
        local_path = os.path.join(local_dir, item)
        remote_path = remote_dir + '/' + item
        if os.path.isdir(local_path):
            sftp_put_dir(sftp, local_path, remote_path)
        else:
            sftp.put(local_path, remote_path)


def main():
    args = parse_args()
    local_fe = os.path.join(args.local_root, 'frontend')
    local_src = os.path.join(local_fe, 'src')
    local_public = os.path.join(local_fe, 'public')
    remote_root = args.install_dir
    remote_src = f'{remote_root}/frontend/src'
    remote_public = f'{remote_root}/frontend/public'

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

    sftp = client.open_sftp()

    # Sync non-src frontend files (package.json, vite.config.ts, etc.)
    for fname in ['package.json', 'vite.config.ts', 'tsconfig.json',
                  'tailwind.config.js', 'postcss.config.js', 'index.html']:
        local_path = os.path.join(local_fe, fname)
        if os.path.exists(local_path):
            sftp.put(local_path, f'{remote_root}/frontend/{fname}')
            print(f'  PUT frontend/{fname}')

    print('Syncing frontend/src...')
    sftp_put_dir(sftp, local_src, remote_src)

    if os.path.isdir(local_public):
        print('Syncing frontend/public...')
        sftp_put_dir(sftp, local_public, remote_public)

    sftp.close()
    print('Sync done')

    def run(cmd):
        _, stdout, stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        print(f'$ {cmd[:70]}')
        if out:
            print(out)
        if err:
            print('ERR:', err[:300])

    run(f'rm -rf /tmp/pktlog-fe && cp -r {remote_root}/frontend /tmp/pktlog-fe')
    run('export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && cd /tmp/pktlog-fe && npm install 2>&1 | tail -3')
    run('export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && cd /tmp/pktlog-fe && npm run build 2>&1 | tail -10')
    run(f'rm -rf {remote_root}/frontend/dist && cp -r /tmp/pktlog-fe/dist {remote_root}/frontend/dist')
    run('sudo systemctl restart pktlog')
    run('sleep 4 && systemctl is-active pktlog')
    run(f'ls {remote_root}/frontend/dist/assets/ | grep -E "Dashboard|Alerts|Settings"')

    client.close()
    print('Done')


if __name__ == "__main__":
    main()
