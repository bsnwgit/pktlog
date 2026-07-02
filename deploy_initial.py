"""
Initial deployment of pktLog to O2.
Creates /mnt/software/pktlog, copies all backend files, sets up venv,
initializes the database, installs and starts the systemd service.

Run ONCE for fresh install. For subsequent updates use deploy_backend.py / deploy_fe.py.
"""
import paramiko, sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEY_PATH    = r'C:\Users\robert.barnett\.ssh\<PKT_SERVER_SSH_KEY>'
LOCAL_ROOT  = r'C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog'
REMOTE_ROOT = '/mnt/software/pktlog'

key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('<PKT_SERVER_IP>', username='<DEPLOY_USER>', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

sftp = client.open_sftp()

def run(label, cmd, timeout=60):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}]')
    if out: print(out)
    if err: print('  ERR:', err[:400])
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
            print(f'  PUT {remote_path.replace(REMOTE_ROOT, "")}')

# ── 1. Create directory structure ─────────────────────────────────────────────
print('\n[1/7] Creating directories...')
for d in [REMOTE_ROOT, f'{REMOTE_ROOT}/app', f'{REMOTE_ROOT}/frontend',
          f'{REMOTE_ROOT}/frontend/dist', '/mnt/software/logs']:
    ensure_remote_dir(d)

# ── 2. Copy backend files ─────────────────────────────────────────────────────
print('\n[2/7] Copying backend files...')
for subdir in ['app', 'migrations']:
    local = os.path.join(LOCAL_ROOT, subdir)
    if os.path.isdir(local):
        sftp_put_tree(local, f'{REMOTE_ROOT}/{subdir}')

for fname in ['requirements.txt', 'config.example.yaml', 'pktlog.service']:
    local = os.path.join(LOCAL_ROOT, fname)
    if os.path.exists(local):
        sftp.put(local, f'{REMOTE_ROOT}/{fname}')
        print(f'  PUT /{fname}')

# ── 3. Create config.yaml ─────────────────────────────────────────────────────
print('\n[3/7] Creating config.yaml...')
run('config check', f'test -f {REMOTE_ROOT}/config.yaml && echo exists || echo notfound')
config_exists = run('config exists?', f'test -f {REMOTE_ROOT}/config.yaml && echo yes || echo no')
if config_exists.strip() == 'yes':
    print('  config.yaml already exists — skipping')
else:
    secret = run('gen secret', 'openssl rand -hex 32').strip()
    run('create config', f"""
sed 's/CHANGE_ME_generate_with_openssl_rand_hex_32/{secret}/' \
    {REMOTE_ROOT}/config.example.yaml > {REMOTE_ROOT}/config.yaml
""")
    print('  config.yaml created with generated secret_key')

# ── 4. Python virtualenv + dependencies ───────────────────────────────────────
print('\n[4/7] Setting up Python virtualenv...')
venv = f'{REMOTE_ROOT}/venv'
run('venv check', f'test -d {venv}/bin && echo exists || echo notfound')
venv_exists = run('venv exists?', f'test -f {venv}/bin/python && echo yes || echo no')
if venv_exists.strip() == 'yes':
    print('  venv already exists — skipping creation')
else:
    run('create venv', f'python3 -m venv {venv}', timeout=30)
    print('  venv created')

print('  Installing requirements (this may take 2-3 minutes)...')
run('pip upgrade', f'{venv}/bin/pip install --quiet --upgrade pip', timeout=60)
run('pip install', f'{venv}/bin/pip install --quiet -r {REMOTE_ROOT}/requirements.txt 2>&1 | tail -5', timeout=300)
print('  Dependencies installed')

# ── 5. Initialize database ────────────────────────────────────────────────────
print('\n[5/7] Initializing database...')
init_script = f"""
import asyncio, sys, os
sys.path.insert(0, '{REMOTE_ROOT}')
os.environ['PKTLOG_CONFIG'] = '{REMOTE_ROOT}/config.yaml'
from app.database import init_db
import asyncio
asyncio.run(init_db())
print('Database initialized')
"""
run('init db', f'{venv}/bin/python3 -c "{init_script.strip()}"', timeout=30)

# ── 6. Install systemd service ────────────────────────────────────────────────
print('\n[6/7] Installing systemd service...')
run('copy service', f'sudo cp {REMOTE_ROOT}/pktlog.service /etc/systemd/system/pktlog.service')
run('daemon reload', 'sudo systemctl daemon-reload')
run('enable service', 'sudo systemctl enable pktlog')

# ── 7. Start service ──────────────────────────────────────────────────────────
print('\n[7/7] Starting pktlog service...')
run('start', 'sudo systemctl restart pktlog')
import time; time.sleep(5)
status = run('status', 'systemctl is-active pktlog')
run('health', 'curl -s http://localhost:8768/api/health 2>/dev/null || echo "(no /api/health yet)"')
run('log tail', f'tail -20 /mnt/software/logs/pktlog.log 2>/dev/null || echo "(no log yet)"')

sftp.close()
client.close()
print(f'\nDone — service is {status.strip()}')
print(f'URL: http://<PKT_SERVER_IP>:8768')
