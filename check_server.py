"""Check current pktlog state on O2."""
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEY_PATH = r'C:\Users\user\.ssh\corporate_infrastructure.pem'
key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('10.20.30.5', username='ec2-user', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

def run(label, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=20)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}]')
    if out: print(out)
    if err and err: print('ERR:', err[:200])

run('pktlog dir', 'ls /mnt/software/pktlog 2>/dev/null || echo "(not found)"')
run('pktlog service', 'systemctl is-active pktlog 2>/dev/null || echo "(not installed)"')
run('service file', 'ls /etc/systemd/system/pktlog.service 2>/dev/null || echo "(not found)"')
run('logs dir', 'ls /mnt/software/logs/ 2>/dev/null | head -5')
run('python3', 'python3 --version')
run('disk', 'df -h /mnt/software | tail -1')

client.close()
print('Done')
