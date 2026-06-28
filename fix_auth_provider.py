"""Add auth_provider column to users table on O2."""
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEY_PATH = r'C:\Users\robert.barnett\.ssh\VyneCorpNetInfra.pem'

key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('172.23.80.5', username='ec2-user', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

cmd = r"""
cd /mnt/software/pktlog && source venv/bin/activate && python3 - <<'EOF'
import sqlite3
DB = '/mnt/software/pktlog/pktlog.db'
conn = sqlite3.connect(DB)

# Check if column already exists
cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
print('Current columns:', cols)

if 'auth_provider' not in cols:
    conn.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'local'")
    conn.commit()
    print('Added auth_provider column')
else:
    print('auth_provider column already exists')

conn.close()
EOF
"""

_, stdout, stderr = client.exec_command(cmd, timeout=30)
print(stdout.read().decode('utf-8', errors='replace'))
err = stderr.read().decode('utf-8', errors='replace').strip()
if err:
    print('ERR:', err[:500])

def run(label, cmd2):
    _, o, e = client.exec_command(cmd2, timeout=20)
    out = o.read().decode('utf-8', errors='replace').strip()
    er = e.read().decode('utf-8', errors='replace').strip()
    print(f'[{label}] {out or "(no output)"} {("ERR:"+er[:200]) if er else ""}')

run('restart', 'sudo systemctl restart pktlog')
run('status', 'sleep 3 && systemctl is-active pktlog')

client.close()
print('Done')
