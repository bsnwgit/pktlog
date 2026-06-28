"""Push backend files and restart pktlog."""
import paramiko, sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEY_PATH    = r'C:\Users\user\.ssh\corporate_infrastructure.pem'
LOCAL_ROOT  = r'C:\Users\user\My Drive\Documents\Claude\Projects\pktLog'
REMOTE_ROOT = '/mnt/software/pktlog'

key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('10.20.30.5', username='ec2-user', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

sftp = client.open_sftp()

# Files to push (relative to LOCAL_ROOT)
BACKEND_FILES = [
    'app/__init__.py',
    'app/main.py',
    'app/config.py',
    'app/database.py',
    'app/dependencies.py',
    'app/backup.py',
    'app/auth/__init__.py',
    'app/auth/router.py',
    'app/auth/models.py',
    'app/auth/utils.py',
    'app/alerts/__init__.py',
    'app/alerts/router.py',
    'app/alerts/models.py',
    'app/alerts/engine.py',
    'app/api/__init__.py',
    'app/api/settings.py',
    'app/api/users.py',
    'app/api/system.py',
    'app/api/logs.py',
    'app/logging_handler.py',
    'migrations/002_app_logs.sql',
    'app/ingest/__init__.py',
    'app/ingest/router.py',
    'app/models/__init__.py',
    'app/storage/__init__.py',
    'app/storage/base.py',
    'app/storage/duckdb.py',
    'app/storage/clickhouse_backend.py',
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

def run(label, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}] {out or ""} {("ERR:"+err[:200]) if err else ""}')

run('restart', 'sudo systemctl restart pktlog')
run('status',  'sleep 4 && systemctl is-active pktlog')
run('health',  'curl -s http://localhost:8768/api/health || echo "not ready"')

client.close()
print('Done')