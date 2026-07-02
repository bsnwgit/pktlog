import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
KEY_PATH = r'C:\Users\robert.barnett\.ssh\<PKT_SERVER_SSH_KEY>'
key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('<PKT_SERVER_IP>', username='<DEPLOY_USER>', pkey=key, timeout=15, banner_timeout=15)
print('Connected')
def run(label, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=20)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}] {out} {"ERR:"+err[:100] if err else ""}')
run('status', 'systemctl is-active pktlog')
run('dist', 'ls /mnt/software/pktlog/frontend/dist/assets/ 2>/dev/null | head -5 || echo "(no dist)"')
run('log', 'tail -8 /mnt/software/logs/pktlog.log 2>/dev/null || echo "(no log)"')
run('health', 'curl -s -o /dev/null -w "%{http_code}" http://localhost:8768/')
client.close()
print('Done')
