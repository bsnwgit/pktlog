"""Build frontend on O2 and deploy to pktlog.
NOTE: Frontend build MUST run on O2 Linux — never on Windows.
This script syncs src files, triggers npm build remotely, then copies dist.
"""
import paramiko
import os
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

LOCAL_SRC   = r'C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\frontend\src'
LOCAL_FE    = r'C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\frontend'
REMOTE_SRC  = '/mnt/software/pktlog/frontend/src'
REMOTE_ROOT = '/mnt/software/pktlog'
KEY_PATH    = r'C:\Users\robert.barnett\.ssh\<PKT_SERVER_SSH_KEY>'

key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('<PKT_SERVER_IP>', username='<DEPLOY_USER>', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

sftp = client.open_sftp()

def sftp_put_dir(local_dir, remote_dir):
    try:
        sftp.stat(remote_dir)
    except FileNotFoundError:
        sftp.mkdir(remote_dir)
    for item in os.listdir(local_dir):
        local_path = os.path.join(local_dir, item)
        remote_path = remote_dir + '/' + item
        if os.path.isdir(local_path):
            sftp_put_dir(local_path, remote_path)
        else:
            sftp.put(local_path, remote_path)

# Sync non-src frontend files (package.json, vite.config.ts, etc.)
for fname in ['package.json', 'vite.config.ts', 'tsconfig.json',
              'tailwind.config.js', 'postcss.config.js', 'index.html']:
    local_path = os.path.join(LOCAL_FE, fname)
    if os.path.exists(local_path):
        sftp.put(local_path, f'{REMOTE_ROOT}/frontend/{fname}')
        print(f'  PUT frontend/{fname}')

print('Syncing frontend/src...')
sftp_put_dir(LOCAL_SRC, REMOTE_SRC)

LOCAL_PUBLIC  = r'C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\frontend\public'
REMOTE_PUBLIC = '/mnt/software/pktlog/frontend/public'
if os.path.isdir(LOCAL_PUBLIC):
    print('Syncing frontend/public...')
    sftp_put_dir(LOCAL_PUBLIC, REMOTE_PUBLIC)

sftp.close()
print('Sync done')

def run(cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f'$ {cmd[:70]}')
    if out: print(out)
    if err: print('ERR:', err[:300])

run('rm -rf /tmp/pktlog-fe && cp -r /mnt/software/pktlog/frontend /tmp/pktlog-fe')
run('export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && cd /tmp/pktlog-fe && npm install 2>&1 | tail -3')
run('export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && cd /tmp/pktlog-fe && npm run build 2>&1 | tail -10')
run('rm -rf /mnt/software/pktlog/frontend/dist && cp -r /tmp/pktlog-fe/dist /mnt/software/pktlog/frontend/dist')
run('sudo systemctl restart pktlog')
run('sleep 4 && systemctl is-active pktlog')
run('ls /mnt/software/pktlog/frontend/dist/assets/ | grep -E "Dashboard|Alerts|Settings"')

client.close()
print('Done')