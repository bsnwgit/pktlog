"""Check current pktlog service state on the remote server.

Usage:
  PKTLOG_SSH_HOST=<host> PKTLOG_SSH_USER=<user> PKTLOG_SSH_KEY=<path> python3 check_server.py
or:
  python3 check_server.py --host <host> --user <user> --key <path> [--install-dir /opt/pktlog]
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
    parser.add_argument("--log-dir", default=os.environ.get("PKTLOG_LOG_DIR"),
                         help="Remote pktlog log directory (default: <install-dir>/logs)")
    args = parser.parse_args()
    if not args.log_dir:
        args.log_dir = f"{args.install_dir}/logs"
    missing = [name for name, val in (("--host/PKTLOG_SSH_HOST", args.host),
                                       ("--user/PKTLOG_SSH_USER", args.user),
                                       ("--key/PKTLOG_SSH_KEY", args.key)) if not val]
    if missing:
        parser.error(f"missing required value(s): {', '.join(missing)}")
    return args


def run(client, label, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=20)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}]')
    if out:
        print(out)
    if err:
        print('ERR:', err[:200])


def main():
    args = parse_args()

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

    run(client, 'pktlog dir', f'ls {args.install_dir} 2>/dev/null || echo "(not found)"')
    run(client, 'pktlog service', 'systemctl is-active pktlog 2>/dev/null || echo "(not installed)"')
    run(client, 'service file', 'ls /etc/systemd/system/pktlog.service 2>/dev/null || echo "(not found)"')
    run(client, 'logs dir', f'ls {args.log_dir}/ 2>/dev/null | head -5')
    run(client, 'python3', 'python3 --version')
    run(client, 'disk', f'df -h {args.install_dir} | tail -1')

    client.close()
    print('Done')


if __name__ == "__main__":
    main()
