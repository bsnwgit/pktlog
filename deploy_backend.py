"""Push backend files and restart pktlog."""
import paramiko, sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEY_PATH    = r'C:\Users\user\.ssh\<PKT_SERVER_SSH_KEY>'
LOCAL_ROOT  = r'C:\Users\user\My Drive\Documents\Claude\Projects\pktLog'
REMOTE_ROOT = '/mnt/software/pktlog'

key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('<PKT_SERVER_IP>', username='<DEPLOY_USER>', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

# Ensure remote directories exist before SFTPing
def run(label, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}] {out or ""} {("ERR:"+err[:200]) if err else ""}')

run('mkdir-ingest',  f'mkdir -p {REMOTE_ROOT}/app/ingest')
run('mkdir-models',  f'mkdir -p {REMOTE_ROOT}/app/models')
run('mkdir-storage', f'mkdir -p {REMOTE_ROOT}/app/storage')
run('mkdir-migrations', f'mkdir -p {REMOTE_ROOT}/migrations')

sftp = client.open_sftp()

# Files to push (relative to LOCAL_ROOT)
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
]

for rel in BACKEND_FILES:
    local = os.path.join(LOCAL_ROOT, rel)
    remote = f'{REMOTE_ROOT}/{rel}'
    if os.path.exists(local):
        sftp.put(local, remote)
        print(f'  PUT {rel}')
    else:
        print(f'  SKIP {rel} (not found locally)')

sftp.close()

run('restart', 'sudo systemctl restart pktlog')
run('status',  'sleep 4 && systemctl is-active pktlog')
run('health',  'curl -sk https://localhost:8768/api/health || echo "not ready"')

client.close()
print('Done')
