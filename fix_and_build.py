"""Create stub assets dir, verify backend starts, then build frontend on O2."""
import paramiko, sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEY_PATH    = r'C:\Users\robert.barnett\.ssh\VyneCorpNetInfra.pem'
LOCAL_FE    = r'C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\frontend'
REMOTE_ROOT = '/mnt/software/pktlog'

key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('172.23.80.5', username='ec2-user', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

sftp = client.open_sftp()

def run(label, cmd, timeout=120):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}]')
    if out: print(out)
    if err: print('  ERR:', err[:400])
    return out

def sftp_put_dir(local_dir, remote_dir):
    try: sftp.stat(remote_dir)
    except FileNotFoundError: sftp.mkdir(remote_dir)
    for item in sorted(os.listdir(local_dir)):
        if item.startswith('.') or item in ('node_modules', '__pycache__', 'dist'):
            continue
        local_path = os.path.join(local_dir, item)
        remote_path = remote_dir + '/' + item
        if os.path.isdir(local_path):
            sftp_put_dir(local_path, remote_path)
        else:
            sftp.put(local_path, remote_path)

# Step 1: Also push app/models/flow.py and models __init__
print('\n[Step 1] Push models...')
sftp.put(
    r'C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\app\models\flow.py',
    f'{REMOTE_ROOT}/app/models/flow.py'
)
print('  PUT app/models/flow.py')

# Step 2: Sync frontend src
print('\n[Step 2] Syncing frontend files...')
for fname in ['package.json', 'vite.config.ts', 'tsconfig.json',
              'tailwind.config.js', 'postcss.config.js', 'index.html']:
    local = os.path.join(LOCAL_FE, fname)
    if os.path.exists(local):
        sftp.put(local, f'{REMOTE_ROOT}/frontend/{fname}')
        print(f'  PUT frontend/{fname}')

print('  Syncing src...')
sftp_put_dir(os.path.join(LOCAL_FE, 'src'), f'{REMOTE_ROOT}/frontend/src')

sftp.close()
print('  Sync done')

# Step 3: Build on O2
print('\n[Step 3] Building frontend on O2...')
NVM = 'export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"'
run('copy to tmp', f'rm -rf /tmp/pktlog-fe && cp -r {REMOTE_ROOT}/frontend /tmp/pktlog-fe')
run('npm install', f'{NVM} && cd /tmp/pktlog-fe && npm install 2>&1 | tail -3', timeout=120)
run('npm build',   f'{NVM} && cd /tmp/pktlog-fe && npm run build > /dev/null 2>&1 && echo "build ok" || echo "BUILD FAILED"', timeout=180)
run('copy dist',   f'rm -rf {REMOTE_ROOT}/frontend/dist && cp -r /tmp/pktlog-fe/dist {REMOTE_ROOT}/frontend/dist')
run('list dist',   f'ls {REMOTE_ROOT}/frontend/dist/assets/ | head -5')

# Step 4: Start service
print('\n[Step 4] Starting service...')
run('restart', 'sudo systemctl restart pktlog')
import time; time.sleep(6)
run('status',  'systemctl is-active pktlog')
run('log tail', f'tail -10 /mnt/software/logs/pktlog.log 2>/dev/null')
run('health',  'curl -s http://localhost:8768/ | head -c 200')

client.close()
print('\nDone')
