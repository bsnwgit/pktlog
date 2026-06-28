"""Push app/models/flow.py fix and restart."""
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEY_PATH = r'C:\Users\user\.ssh\corporate_infrastructure.pem'
key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('10.20.30.5', username='ec2-user', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

sftp = client.open_sftp()
sftp.put(
    r'C:\Users\user\My Drive\Documents\Claude\Projects\pktLog\app\models\flow.py',
    '/mnt/software/pktlog/app/models/flow.py'
)
sftp.close()
print('PUT app/models/flow.py')

def run(label, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}] {out} {"ERR:"+err[:200] if err else ""}')

run('restart', 'sudo systemctl restart pktlog')
import time; time.sleep(5)
run('status', 'systemctl is-active pktlog')
run('log tail', 'tail -15 /mnt/software/logs/pktlog.log 2>/dev/null | grep -v "^$"')

client.close()
print('Done')
