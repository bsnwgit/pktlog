"""Push duckdb.py fix and restart pktlog."""
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
sftp.put(
    os.path.join(LOCAL_ROOT, 'app/storage/duckdb.py'),
    f'{REMOTE_ROOT}/app/storage/duckdb.py'
)
print('PUT app/storage/duckdb.py')
sftp.close()

def run(label, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}] {out or "(no output)"} {("ERR:"+err[:300]) if err else ""}')

run('restart', 'sudo systemctl restart pktlog')
run('status',  'sleep 4 && systemctl is-active pktlog')
run('health',  'curl -s http://localhost:8768/api/health || echo "not ready"')
run('journal', 'journalctl -u pktlog -n 10 --no-pager')

client.close()
print('Done')
