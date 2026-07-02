"""Seed the initial admin user in pktlog.db on O2."""
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEY_PATH = r'C:\Users\robert.barnett\.ssh\<PKT_SERVER_SSH_KEY>'

key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('<PKT_SERVER_IP>', username='<DEPLOY_USER>', pkey=key, timeout=15, banner_timeout=15)
print('Connected')

cmd = r"""
cd /mnt/software/pktlog && source venv/bin/activate && python3 - <<'EOF'
import sqlite3, bcrypt
DB = '/mnt/software/pktlog/pktlog.db'
conn = sqlite3.connect(DB)
# Check if admin already exists
row = conn.execute("SELECT id, username FROM users WHERE username='admin'").fetchone()
if row:
    print(f'Admin user already exists (id={row[0]})')
else:
    pw = b'Admin1234!'
    h = bcrypt.hashpw(pw, bcrypt.gensalt()).decode()
    conn.execute(
        "INSERT INTO users (username, email, hashed_password, role, is_active) VALUES (?,?,?,?,?)",
        ('admin', 'admin@local', h, 'admin', 1)
    )
    conn.commit()
    print('Admin user created: username=admin  password=Admin1234!')
conn.close()
EOF
"""

_, stdout, stderr = client.exec_command(cmd, timeout=30)
print(stdout.read().decode('utf-8', errors='replace'))
err = stderr.read().decode('utf-8', errors='replace').strip()
if err:
    print('ERR:', err[:500])

client.close()
print('Done')
