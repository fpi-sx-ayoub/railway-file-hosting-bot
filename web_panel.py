# ════════════════════════════════════════════════════════════════
#  لوحة تحكم الويب - Web Control Panel
#  تشغّل بجانب البوت وتوفّر API + واجهة HTML
# ════════════════════════════════════════════════════════════════
import os
import json
import sqlite3
import asyncio
import threading
import subprocess
import time
import signal
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, send_file

# ── الإعدادات ──────────────────────────────────────────────────
DATA_DIR = "data"
DB_PATH = os.path.join(DATA_DIR, "bot_data.db")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "fpi-sx-team-secret-key-2026")
OWNER_ID = int(os.getenv("OWNER_ID", "8695276303"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@MTX_SX_TEAM_BOT")

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ── حالة البوت ────────────────────────────────────────────────
class BotState:
    process = None
    started_at = None
    status = "stopped"   # stopped | running | starting | stopping
    lock = threading.Lock()
    last_log = []

bot_state = BotState()

# ── أدوات قاعدة البيانات ──────────────────────────────────────
def db_conn():
    return sqlite3.connect(DB_PATH, timeout=30)

def db_query(sql, params=(), one=False):
    try:
        with db_conn() as c:
            cur = c.execute(sql, params)
            rows = cur.fetchall()
            if one:
                return rows[0] if rows else None
            return rows
    except Exception as e:
        print(f"[DB Error] {e}")
        return None if one else []

def get_stats():
    """جلب الإحصائيات الشاملة"""
    def q(sql, default=0):
        try:
            with db_conn() as c:
                r = c.execute(sql).fetchone()
                return r[0] if r and r[0] is not None else default
        except Exception:
            return default
    return {
        "users_total":    q("SELECT COUNT(*) FROM users"),
        "users_active":   q("SELECT COUNT(*) FROM users WHERE is_banned=0"),
        "users_banned":   q("SELECT COUNT(*) FROM users WHERE is_banned=1"),
        "users_admins":   q("SELECT COUNT(*) FROM users WHERE is_admin=1"),
        "files_total":    q("SELECT COUNT(*) FROM files"),
        "files_pending":  q("SELECT COUNT(*) FROM files WHERE status='pending'"),
        "files_approved": q("SELECT COUNT(*) FROM files WHERE status='approved'"),
        "files_rejected": q("SELECT COUNT(*) FROM files WHERE status='rejected'"),
        "files_running":  q("SELECT COUNT(*) FROM files WHERE is_running=1"),
        "channels":       q("SELECT COUNT(*) FROM channels"),
    }

def get_users(limit=100, offset=0):
    rows = db_query(
        "SELECT user_id,username,first_name,last_name,joined_at,is_banned,is_admin "
        "FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ) or []
    out = []
    for r in rows:
        out.append({
            "user_id": r[0],
            "username": r[1] or "",
            "first_name": r[2] or "",
            "last_name": r[3] or "",
            "joined_at": r[4] or "",
            "is_banned": bool(r[5]),
            "is_admin": bool(r[6]),
        })
    return out

def get_files(limit=100):
    rows = db_query(
        "SELECT id,user_id,filename,size,file_type,status,is_running,created_at "
        "FROM files ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ) or []
    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "user_id": r[1],
            "filename": r[2],
            "size": r[3],
            "file_type": r[4],
            "status": r[5],
            "is_running": bool(r[6]),
            "created_at": r[7],
        })
    return out

def get_admins():
    rows = db_query(
        "SELECT u.user_id, u.username, u.first_name FROM users u WHERE u.is_admin=1"
    ) or []
    return [{"user_id": r[0], "username": r[1] or "", "first_name": r[2] or ""} for r in rows]

def get_active_user_ids():
    rows = db_query("SELECT user_id FROM users WHERE is_banned=0") or []
    return [r[0] for r in rows]

# ── إدارة عملية البوت ────────────────────────────────────────
def start_bot_process():
    with bot_state.lock:
        if bot_state.process and bot_state.process.poll() is None:
            return False, "البوت يعمل بالفعل"
        bot_state.status = "starting"
        try:
            log_path = os.path.join(LOGS_DIR, "bot_runtime.log")
            log_file = open(log_path, "a", buffering=1)
            log_file.write(f"\n\n===== Bot started at {datetime.utcnow().isoformat()} =====\n")
            env = os.environ.copy()
            env["WEB_PANEL_MODE"] = "1"
            bot_state.process = subprocess.Popen(
                ["python", "-u", "bot.py"],
                stdout=log_file, stderr=subprocess.STDOUT,
                env=env, preexec_fn=os.setsid
            )
            bot_state.started_at = datetime.utcnow().isoformat()
            bot_state.status = "running"
            return True, "تم تشغيل البوت بنجاح"
        except Exception as e:
            bot_state.status = "stopped"
            return False, f"فشل التشغيل: {e}"

def stop_bot_process():
    with bot_state.lock:
        if not bot_state.process or bot_state.process.poll() is not None:
            bot_state.status = "stopped"
            return False, "البوت متوقف بالفعل"
        bot_state.status = "stopping"
        try:
            os.killpg(os.getpgid(bot_state.process.pid), signal.SIGTERM)
            try:
                bot_state.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(bot_state.process.pid), signal.SIGKILL)
            bot_state.process = None
            bot_state.status = "stopped"
            return True, "تم إيقاف البوت"
        except Exception as e:
            bot_state.status = "stopped"
            return False, f"خطأ: {e}"

def bot_is_running():
    if bot_state.process and bot_state.process.poll() is None:
        return True
    if bot_state.status == "running":
        bot_state.status = "stopped"
    return False

# ── إرسال إذاعة عبر Telegram API مباشرة ──────────────────────
def send_broadcast(message_text):
    import requests
    if not BOT_TOKEN:
        return {"success": 0, "failed": 0, "error": "BOT_TOKEN غير محدد"}
    user_ids = get_active_user_ids()
    success = 0
    failed = 0
    text = f"📢 <b>رسالة من الإدارة</b>\n\n{message_text}\n\n👑 FpI sX tEaM"
    for uid in user_ids:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": uid, "text": text, "parse_mode": "HTML"},
                timeout=10
            )
            if r.status_code == 200 and r.json().get("ok"):
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        time.sleep(0.05)
    return {"success": success, "failed": failed, "total": len(user_ids)}

# ════════════════════════════════════════════════════════════════
#  مصادقة بسيطة
# ════════════════════════════════════════════════════════════════
def require_login(fn):
    from functools import wraps
    @wraps(fn)
    def wrap(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrap

# ════════════════════════════════════════════════════════════════
#  HTML Templates
# ════════════════════════════════════════════════════════════════
LOGIN_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>تسجيل الدخول - لوحة التحكم</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:20px}
.box{background:#fff;border-radius:20px;padding:40px;width:100%;max-width:400px;
box-shadow:0 25px 60px rgba(0,0,0,.3)}
h1{text-align:center;color:#333;margin-bottom:10px;font-size:28px}
.sub{text-align:center;color:#888;margin-bottom:30px;font-size:14px}
.field{margin-bottom:20px}
label{display:block;margin-bottom:8px;color:#555;font-weight:600}
input{width:100%;padding:14px;border:2px solid #e1e1e1;border-radius:10px;
font-size:15px;transition:border .3s}
input:focus{outline:0;border-color:#667eea}
button{width:100%;padding:14px;background:linear-gradient(135deg,#667eea,#764ba2);
color:#fff;border:0;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;
transition:transform .2s}
button:hover{transform:translateY(-2px)}
.err{background:#fee;color:#c33;padding:12px;border-radius:8px;margin-bottom:15px;text-align:center;font-size:14px}
.logo{text-align:center;font-size:50px;margin-bottom:10px}
</style>
</head>
<body>
<div class="box">
<div class="logo">🤖</div>
<h1>لوحة التحكم</h1>
<p class="sub">FpI sX tEaM - Bot Control Panel</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<form method="POST">
<div class="field">
<label>🔐 كلمة المرور</label>
<input type="password" name="password" required autofocus placeholder="أدخل كلمة المرور">
</div>
<button type="submit">دخول 🚀</button>
</form>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>لوحة تحكم البوت - FpI sX tEaM</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}
body{background:#0f0f1e;color:#e0e0e0;min-height:100vh}
.topbar{background:linear-gradient(135deg,#667eea,#764ba2);padding:15px 25px;
display:flex;justify-content:space-between;align-items:center;box-shadow:0 4px 20px rgba(0,0,0,.4)}
.topbar h1{font-size:20px;color:#fff}
.topbar .user{display:flex;gap:15px;align-items:center}
.btn{padding:8px 16px;border-radius:8px;border:0;cursor:pointer;font-weight:600;
font-size:14px;transition:all .2s}
.btn-danger{background:#e74c3c;color:#fff}
.btn-success{background:#27ae60;color:#fff}
.btn-primary{background:#3498db;color:#fff}
.btn-warning{background:#f39c12;color:#fff}
.btn:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.3)}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.container{max-width:1400px;margin:0 auto;padding:25px}
.status-card{background:#1a1a2e;border-radius:15px;padding:25px;margin-bottom:25px;
border:2px solid #2a2a4a;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:15px}
.status-info h2{font-size:22px;margin-bottom:8px}
.status-info p{color:#888;font-size:14px}
.status-badge{display:inline-block;padding:6px 14px;border-radius:20px;font-weight:700;font-size:13px}
.s-running{background:#27ae60;color:#fff}
.s-stopped{background:#e74c3c;color:#fff}
.s-starting,.s-stopping{background:#f39c12;color:#fff}
.controls{display:flex;gap:10px;flex-wrap:wrap}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:15px;margin-bottom:25px}
.stat{background:#1a1a2e;border-radius:12px;padding:20px;border:1px solid #2a2a4a;
transition:transform .2s;cursor:default}
.stat:hover{transform:translateY(-3px);border-color:#667eea}
.stat .num{font-size:32px;font-weight:800;color:#667eea;margin-bottom:5px}
.stat .lbl{color:#888;font-size:13px}
.stat .icn{font-size:24px;margin-bottom:8px;display:block}
.tabs{display:flex;gap:5px;margin-bottom:20px;border-bottom:2px solid #2a2a4a;flex-wrap:wrap}
.tab{padding:12px 24px;cursor:pointer;color:#888;font-weight:600;border-bottom:3px solid transparent;
transition:all .2s}
.tab:hover{color:#fff}
.tab.active{color:#667eea;border-bottom-color:#667eea}
.panel{background:#1a1a2e;border-radius:12px;padding:25px;border:1px solid #2a2a4a;display:none}
.panel.active{display:block}
.panel h3{margin-bottom:18px;font-size:18px;display:flex;align-items:center;gap:10px}
textarea,input[type=text],input[type=number]{width:100%;padding:12px;background:#0f0f1e;
border:2px solid #2a2a4a;border-radius:8px;color:#e0e0e0;font-size:14px;font-family:inherit}
textarea{min-height:120px;resize:vertical}
textarea:focus,input:focus{outline:0;border-color:#667eea}
.field{margin-bottom:15px}
.field label{display:block;margin-bottom:8px;color:#aaa;font-weight:600;font-size:14px}
table{width:100%;border-collapse:collapse;margin-top:10px}
th,td{padding:12px;text-align:right;border-bottom:1px solid #2a2a4a;font-size:13px}
th{background:#0f0f1e;color:#667eea;font-weight:700}
tr:hover{background:#0f0f1e}
.badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700;display:inline-block}
.b-admin{background:#9b59b6;color:#fff}
.b-banned{background:#e74c3c;color:#fff}
.b-active{background:#27ae60;color:#fff}
.b-pending{background:#f39c12;color:#fff}
.b-approved{background:#27ae60;color:#fff}
.b-rejected{background:#e74c3c;color:#fff}
.alert{padding:12px;border-radius:8px;margin-bottom:15px;font-size:14px}
.alert-success{background:rgba(39,174,96,.2);color:#2ecc71;border:1px solid #27ae60}
.alert-error{background:rgba(231,76,60,.2);color:#e74c3c;border:1px solid #e74c3c}
.alert-info{background:rgba(52,152,219,.2);color:#3498db;border:1px solid #3498db}
.scroll{max-height:500px;overflow-y:auto}
.refresh-btn{background:transparent;color:#888;font-size:13px;padding:5px 10px}
.refresh-btn:hover{color:#fff}
.footer{text-align:center;padding:20px;color:#666;font-size:13px;margin-top:30px}
.uptime{color:#27ae60;font-weight:700}
.actions{display:flex;gap:5px;flex-wrap:wrap}
.icon-btn{padding:5px 10px;font-size:12px;border-radius:6px;border:0;cursor:pointer;color:#fff}
@media (max-width:768px){
  .topbar h1{font-size:16px}
  .stats-grid{grid-template-columns:repeat(2,1fr)}
  .tabs{overflow-x:auto;flex-wrap:nowrap}
}
</style>
</head>
<body>
<div class="topbar">
  <h1>🤖 لوحة تحكم البوت — FpI sX tEaM</h1>
  <div class="user">
    <span style="font-size:13px">👋 مرحباً، المدير</span>
    <a href="/logout"><button class="btn btn-danger">خروج</button></a>
  </div>
</div>

<div class="container">

  <!-- حالة البوت -->
  <div class="status-card">
    <div class="status-info">
      <h2>⚡ حالة البوت
        <span id="bot-status-badge" class="status-badge s-stopped">جارٍ التحميل...</span>
      </h2>
      <p>البوت: <b>{{ bot_username }}</b> | المالك: <b>{{ owner_id }}</b></p>
      <p id="uptime-text" style="margin-top:6px;font-size:13px"></p>
    </div>
    <div class="controls">
      <button class="btn btn-success" id="btn-start" onclick="startBot()">▶️ تشغيل البوت</button>
      <button class="btn btn-danger" id="btn-stop" onclick="stopBot()">⏹️ إيقاف البوت</button>
      <button class="btn btn-warning" onclick="restartBot()">🔄 إعادة تشغيل</button>
      <button class="btn btn-primary refresh-btn" onclick="refreshAll()">🔃 تحديث</button>
    </div>
  </div>

  <!-- الإحصائيات -->
  <div class="stats-grid" id="stats">
    <div class="stat"><span class="icn">👥</span><div class="num" id="s-users-total">-</div><div class="lbl">إجمالي المستخدمين</div></div>
    <div class="stat"><span class="icn">✅</span><div class="num" id="s-users-active">-</div><div class="lbl">المستخدمون النشطون</div></div>
    <div class="stat"><span class="icn">🚫</span><div class="num" id="s-users-banned">-</div><div class="lbl">المحظورون</div></div>
    <div class="stat"><span class="icn">👑</span><div class="num" id="s-users-admins">-</div><div class="lbl">المشرفون</div></div>
    <div class="stat"><span class="icn">📁</span><div class="num" id="s-files-total">-</div><div class="lbl">إجمالي الملفات</div></div>
    <div class="stat"><span class="icn">⏳</span><div class="num" id="s-files-pending">-</div><div class="lbl">بانتظار الموافقة</div></div>
    <div class="stat"><span class="icn">🟢</span><div class="num" id="s-files-running">-</div><div class="lbl">قيد التشغيل</div></div>
    <div class="stat"><span class="icn">📢</span><div class="num" id="s-channels">-</div><div class="lbl">القنوات المُلزمة</div></div>
  </div>

  <!-- التبويبات -->
  <div class="tabs">
    <div class="tab active" data-tab="broadcast">📢 إذاعة</div>
    <div class="tab" data-tab="users">👥 المستخدمون</div>
    <div class="tab" data-tab="files">📁 الملفات</div>
    <div class="tab" data-tab="admins">👑 المشرفون</div>
    <div class="tab" data-tab="logs">📜 السجلات</div>
  </div>

  <!-- إذاعة -->
  <div class="panel active" id="p-broadcast">
    <h3>📢 إرسال إذاعة لجميع المستخدمين</h3>
    <div id="broadcast-result"></div>
    <div class="field">
      <label>نص الرسالة (يدعم HTML)</label>
      <textarea id="broadcast-msg" placeholder="اكتب رسالتك هنا..."></textarea>
    </div>
    <button class="btn btn-primary" onclick="sendBroadcast()" id="bc-btn">📤 إرسال الإذاعة</button>
    <p style="color:#888;margin-top:12px;font-size:13px">ستُرسل الرسالة لجميع المستخدمين النشطين عبر Telegram API مباشرة.</p>
  </div>

  <!-- المستخدمون -->
  <div class="panel" id="p-users">
    <h3>👥 قائمة المستخدمين <button class="btn refresh-btn" onclick="loadUsers()">🔃</button></h3>
    <div class="scroll">
      <table>
        <thead><tr><th>ID</th><th>المستخدم</th><th>الاسم</th><th>التاريخ</th><th>الحالة</th><th>إجراء</th></tr></thead>
        <tbody id="users-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- الملفات -->
  <div class="panel" id="p-files">
    <h3>📁 الملفات الأخيرة <button class="btn refresh-btn" onclick="loadFiles()">🔃</button></h3>
    <div class="scroll">
      <table>
        <thead><tr><th>ID</th><th>المستخدم</th><th>الاسم</th><th>النوع</th><th>الحجم</th><th>الحالة</th><th>تشغيل</th></tr></thead>
        <tbody id="files-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- المشرفون -->
  <div class="panel" id="p-admins">
    <h3>👑 المشرفون</h3>
    <div class="scroll">
      <table>
        <thead><tr><th>ID</th><th>المستخدم</th><th>الاسم</th></tr></thead>
        <tbody id="admins-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- السجلات -->
  <div class="panel" id="p-logs">
    <h3>📜 سجلات تشغيل البوت <button class="btn refresh-btn" onclick="loadLogs()">🔃</button></h3>
    <pre id="logs-content" style="background:#0f0f1e;padding:15px;border-radius:8px;
         max-height:500px;overflow:auto;font-size:12px;color:#0f0;line-height:1.5"></pre>
  </div>

  <div class="footer">
    🛡️ FpI sX tEaM Bot Panel © 2026 | Render Hosting
  </div>
</div>

<script>
// ── التبويبات ──
document.querySelectorAll('.tab').forEach(t=>{
  t.onclick=()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('p-'+t.dataset.tab).classList.add('active');
    if(t.dataset.tab==='users')loadUsers();
    if(t.dataset.tab==='files')loadFiles();
    if(t.dataset.tab==='admins')loadAdmins();
    if(t.dataset.tab==='logs')loadLogs();
  };
});

// ── API ──
async function api(url,opts={}){
  const r=await fetch(url,{credentials:'same-origin',...opts});
  return await r.json();
}

// ── الحالة + الإحصائيات ──
async function refreshStatus(){
  const s=await api('/api/status');
  const b=document.getElementById('bot-status-badge');
  b.textContent={running:'🟢 يعمل',stopped:'🔴 متوقف',starting:'🟡 جارٍ التشغيل',stopping:'🟡 جارٍ الإيقاف'}[s.status]||s.status;
  b.className='status-badge s-'+s.status;
  document.getElementById('btn-start').disabled=s.status==='running'||s.status==='starting';
  document.getElementById('btn-stop').disabled=s.status==='stopped';
  document.getElementById('uptime-text').innerHTML=s.uptime?
    '⏱️ <span class="uptime">يعمل منذ: '+s.uptime+'</span>':'⏸️ البوت متوقف';
}
async function refreshStats(){
  const s=await api('/api/stats');
  for(const k of ['users_total','users_active','users_banned','users_admins',
                  'files_total','files_pending','files_running','channels']){
    const el=document.getElementById('s-'+k.replace(/_/g,'-'));
    if(el)el.textContent=s[k]||0;
  }
}
async function refreshAll(){await refreshStatus();await refreshStats();}

// ── تشغيل / إيقاف ──
async function startBot(){
  const r=await api('/api/bot/start',{method:'POST'});
  alert(r.message||'تم');refreshStatus();
}
async function stopBot(){
  if(!confirm('هل تريد إيقاف البوت؟'))return;
  const r=await api('/api/bot/stop',{method:'POST'});
  alert(r.message||'تم');refreshStatus();
}
async function restartBot(){
  if(!confirm('إعادة تشغيل البوت؟'))return;
  await api('/api/bot/stop',{method:'POST'});
  setTimeout(async()=>{await api('/api/bot/start',{method:'POST'});refreshStatus();},2000);
}

// ── إذاعة ──
async function sendBroadcast(){
  const msg=document.getElementById('broadcast-msg').value.trim();
  if(!msg){alert('اكتب الرسالة أولاً');return;}
  if(!confirm('إرسال الرسالة لجميع المستخدمين؟'))return;
  const btn=document.getElementById('bc-btn');
  btn.disabled=true;btn.textContent='⏳ جارٍ الإرسال...';
  const res=document.getElementById('broadcast-result');
  res.innerHTML='<div class="alert alert-info">جارٍ الإرسال، يرجى الانتظار...</div>';
  try{
    const r=await api('/api/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:msg})});
    if(r.ok){
      res.innerHTML='<div class="alert alert-success">✅ تم الإرسال<br>'+
        'نجح: <b>'+r.result.success+'</b> | فشل: <b>'+r.result.failed+'</b> | إجمالي: <b>'+r.result.total+'</b></div>';
      document.getElementById('broadcast-msg').value='';
    }else{
      res.innerHTML='<div class="alert alert-error">❌ '+(r.error||'فشل')+'</div>';
    }
  }catch(e){res.innerHTML='<div class="alert alert-error">❌ '+e+'</div>';}
  btn.disabled=false;btn.textContent='📤 إرسال الإذاعة';
}

// ── المستخدمون ──
async function loadUsers(){
  const u=await api('/api/users');
  const tb=document.getElementById('users-tbody');
  tb.innerHTML=u.map(x=>{
    const badges=[];
    if(x.is_admin)badges.push('<span class="badge b-admin">👑 مشرف</span>');
    if(x.is_banned)badges.push('<span class="badge b-banned">🚫 محظور</span>');
    else badges.push('<span class="badge b-active">✅ نشط</span>');
    const act=x.is_banned?
      '<button class="icon-btn btn-success" onclick="userAction('+x.user_id+',\\'unban\\')">فك حظر</button>':
      '<button class="icon-btn btn-danger" onclick="userAction('+x.user_id+',\\'ban\\')">حظر</button>';
    return '<tr><td>'+x.user_id+'</td><td>@'+(x.username||'-')+'</td><td>'+(x.first_name||'')+'</td>'+
           '<td style="font-size:11px">'+(x.joined_at||'').substr(0,16)+'</td>'+
           '<td>'+badges.join(' ')+'</td><td>'+act+'</td></tr>';
  }).join('')||'<tr><td colspan="6" style="text-align:center;color:#888">لا يوجد مستخدمون</td></tr>';
}
async function userAction(uid,act){
  const r=await api('/api/users/'+uid+'/'+act,{method:'POST'});
  alert(r.message||'تم');loadUsers();refreshStats();
}

// ── الملفات ──
async function loadFiles(){
  const f=await api('/api/files');
  const tb=document.getElementById('files-tbody');
  tb.innerHTML=f.map(x=>{
    const sb={pending:'b-pending',approved:'b-approved',rejected:'b-rejected'}[x.status]||'';
    return '<tr><td>'+x.id+'</td><td>'+x.user_id+'</td><td>'+x.filename+'</td>'+
           '<td>'+(x.file_type||'').toUpperCase()+'</td><td>'+Math.round((x.size||0)/1024)+' KB</td>'+
           '<td><span class="badge '+sb+'">'+x.status+'</span></td>'+
           '<td>'+(x.is_running?'🟢':'🔴')+'</td></tr>';
  }).join('')||'<tr><td colspan="7" style="text-align:center;color:#888">لا يوجد ملفات</td></tr>';
}

// ── المشرفون ──
async function loadAdmins(){
  const a=await api('/api/admins');
  document.getElementById('admins-tbody').innerHTML=a.map(x=>
    '<tr><td>'+x.user_id+'</td><td>@'+(x.username||'-')+'</td><td>'+(x.first_name||'')+'</td></tr>'
  ).join('')||'<tr><td colspan="3" style="text-align:center;color:#888">لا يوجد</td></tr>';
}

// ── السجلات ──
async function loadLogs(){
  const r=await api('/api/logs');
  document.getElementById('logs-content').textContent=r.log||'(لا يوجد سجل)';
}

// ── تشغيل تلقائي ──
refreshAll();
setInterval(refreshAll,8000);
</script>
</body>
</html>"""

# ════════════════════════════════════════════════════════════════
#  Routes
# ════════════════════════════════════════════════════════════════
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "كلمة المرور غير صحيحة"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@require_login
def dashboard():
    return render_template_string(DASHBOARD_HTML,
                                  bot_username=BOT_USERNAME,
                                  owner_id=OWNER_ID)

# ── Health check + keepalive ──
@app.route("/health")
def health():
    return jsonify({"status": "ok", "bot": bot_state.status,
                    "time": datetime.utcnow().isoformat()})

@app.route("/ping")
def ping():
    return "pong"

# ── API ──
@app.route("/api/status")
@require_login
def api_status():
    running = bot_is_running()
    uptime = ""
    if running and bot_state.started_at:
        try:
            delta = datetime.utcnow() - datetime.fromisoformat(bot_state.started_at)
            secs = int(delta.total_seconds())
            h, rem = divmod(secs, 3600); m, s = divmod(rem, 60)
            uptime = f"{h}س {m}د {s}ث"
        except Exception: pass
    return jsonify({"status": bot_state.status, "running": running,
                    "started_at": bot_state.started_at, "uptime": uptime})

@app.route("/api/stats")
@require_login
def api_stats():
    return jsonify(get_stats())

@app.route("/api/users")
@require_login
def api_users():
    return jsonify(get_users(limit=200))

@app.route("/api/files")
@require_login
def api_files():
    return jsonify(get_files(limit=100))

@app.route("/api/admins")
@require_login
def api_admins():
    return jsonify(get_admins())

@app.route("/api/users/<int:uid>/ban", methods=["POST"])
@require_login
def api_ban(uid):
    try:
        with db_conn() as c:
            c.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
            c.commit()
        return jsonify({"ok": True, "message": f"تم حظر {uid}"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@app.route("/api/users/<int:uid>/unban", methods=["POST"])
@require_login
def api_unban(uid):
    try:
        with db_conn() as c:
            c.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
            c.commit()
        return jsonify({"ok": True, "message": f"تم فك حظر {uid}"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@app.route("/api/broadcast", methods=["POST"])
@require_login
def api_broadcast():
    data = request.get_json() or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"ok": False, "error": "الرسالة فارغة"}), 400
    result = send_broadcast(msg)
    return jsonify({"ok": True, "result": result})

@app.route("/api/bot/start", methods=["POST"])
@require_login
def api_bot_start():
    ok, msg = start_bot_process()
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/bot/stop", methods=["POST"])
@require_login
def api_bot_stop():
    ok, msg = stop_bot_process()
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/logs")
@require_login
def api_logs():
    p = os.path.join(LOGS_DIR, "bot_runtime.log")
    if not os.path.exists(p):
        return jsonify({"log": "(لا يوجد سجل بعد)"})
    try:
        with open(p, "r", errors="ignore") as f:
            content = f.read()
        return jsonify({"log": content[-15000:]})
    except Exception as e:
        return jsonify({"log": f"خطأ: {e}"})

# ════════════════════════════════════════════════════════════════
#  بدء البوت تلقائياً عند تشغيل الخادم
# ════════════════════════════════════════════════════════════════
def auto_start_bot():
    time.sleep(3)
    print("[Panel] Auto-starting bot...")
    ok, msg = start_bot_process()
    print(f"[Panel] {msg}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    # بدء البوت تلقائياً
    threading.Thread(target=auto_start_bot, daemon=True).start()
    # تفعيل keepalive لمنع توقف Render
    try:
        from keepalive import start_keepalive
        start_keepalive()
    except Exception as e:
        print(f"[KeepAlive] failed: {e}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
