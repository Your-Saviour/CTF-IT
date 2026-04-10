import os
import hashlib
import sqlite3
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

DB_PATH = '/opt/inventory/inventory.db'
UPLOAD_DIR = '/opt/inventory/uploads'
ALLOWED_EXTENSIONS = {'txt', 'csv', 'log', 'json'}

STYLE = '''
<style>
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 0; }
  .container { max-width: 900px; margin: 40px auto; padding: 20px; }
  h1 { color: #00d4ff; }
  table { width: 100%; border-collapse: collapse; margin-top: 20px; }
  th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #333; }
  th { background: #16213e; color: #00d4ff; }
  tr:hover { background: #16213e; }
  .btn { display: inline-block; padding: 8px 20px; background: #00d4ff; color: #1a1a2e;
         text-decoration: none; border-radius: 4px; border: none; cursor: pointer; font-size: 14px; }
  .btn:hover { background: #00b8d9; }
  input[type=text], input[type=password] { padding: 8px; width: 250px; border: 1px solid #333;
         border-radius: 4px; background: #16213e; color: #e0e0e0; }
  .warning { background: #ff4444; color: white; padding: 12px; text-align: center;
             margin-bottom: 20px; border-radius: 4px; }
  .error { color: #ff6b6b; margin: 10px 0; }
  .success { color: #51cf66; margin: 10px 0; }
  .nav { background: #16213e; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; }
  .nav a { color: #00d4ff; text-decoration: none; margin-left: 16px; }
  .status-online { color: #51cf66; }
  .status-offline { color: #ff6b6b; }
</style>
'''

LOGIN_PAGE = STYLE + '''
<div class="container">
  <h1>Inventory Dashboard</h1>
  {% if warning %}
  <div class="warning">Default credentials detected - please change the admin password immediately!</div>
  {% endif %}
  <h3>Login</h3>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <form method="post" action="/login">
    <p><input type="text" name="username" placeholder="Username" required></p>
    <p><input type="password" name="password" placeholder="Password" required></p>
    <p><button class="btn" type="submit">Login</button></p>
  </form>
</div>
'''

DASHBOARD_PAGE = STYLE + '''
<div class="nav">
  <span>Inventory Dashboard</span>
  <span>
    <a href="/upload">Upload</a>
    <a href="/logout">Logout ({{ user }})</a>
  </span>
</div>
<div class="container">
  {% if warning %}
  <div class="warning">Default credentials detected - please change the admin password immediately!</div>
  {% endif %}
  <h1>Server Inventory</h1>
  <table>
    <tr><th>Hostname</th><th>IP Address</th><th>OS</th><th>Status</th><th>Last Seen</th></tr>
    {% for h in hosts %}
    <tr>
      <td>{{ h['hostname'] }}</td>
      <td>{{ h['ip_address'] }}</td>
      <td>{{ h['os_type'] }}</td>
      <td class="status-{{ h['status'] }}">{{ h['status'] }}</td>
      <td>{{ h['last_seen'] }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
'''

UPLOAD_PAGE = STYLE + '''
<div class="nav">
  <span>Inventory Dashboard</span>
  <span>
    <a href="/dashboard">Dashboard</a>
    <a href="/logout">Logout ({{ user }})</a>
  </span>
</div>
<div class="container">
  <h1>Upload File</h1>
  {% if message %}<p class="{{ 'success' if 'success' in message else 'error' }}">{{ message }}</p>{% endif %}
  <form method="post" enctype="multipart/form-data">
    <p><input type="file" name="file" style="color:#e0e0e0;"></p>
    <p><button class="btn" type="submit">Upload</button></p>
  </form>
  <p style="color:#888;">Allowed types: txt, csv, log, json</p>
</div>
'''


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def check_default_creds():
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT password FROM users WHERE username = 'admin'"
        ).fetchone()
        conn.close()
        if row and row['password'] == hash_password('admin'):
            return True
    except Exception:
        pass
    return False


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/')
def index():
    default_warning = check_default_creds()
    return render_template_string(LOGIN_PAGE, error=None, warning=default_warning)


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        conn = get_db()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ? AND password = ?',
            (username, hash_password(password))
        ).fetchone()
        conn.close()
        if user:
            session['user'] = username
            return redirect(url_for('dashboard'))
        error = 'Invalid credentials'
    default_warning = check_default_creds()
    return render_template_string(LOGIN_PAGE, error=error, warning=default_warning)


@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    hosts = conn.execute('SELECT * FROM hosts').fetchall()
    conn.close()
    default_warning = check_default_creds()
    return render_template_string(
        DASHBOARD_PAGE, hosts=hosts, user=session['user'], warning=default_warning
    )


@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    message = None
    if request.method == 'POST':
        f = request.files.get('file')
        if f and f.filename:
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            if ext in ALLOWED_EXTENSIONS:
                f.save(os.path.join(UPLOAD_DIR, f.filename))
                message = 'File uploaded successfully'
            else:
                message = 'File type not allowed'
        else:
            message = 'No file selected'
    return render_template_string(UPLOAD_PAGE, message=message, user=session['user'])


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))
