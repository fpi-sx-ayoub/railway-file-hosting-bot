import os
import signal
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from functools import wraps

import requests
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)

DATA_DIR = "data"
DB_PATH = os.path.join(DATA_DIR, "bot_data.db")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


def _safe_int(value, default):
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _normalize_username(value: str) -> str:
    value = (value or "").strip()
    return value[1:] if value.startswith("@") else value


ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "XcT-x-TeaM-BoT-iS-BesT")
SECRET_KEY = os.getenv("SECRET_KEY", "xct-x-team-panel-secret-key")
OWNER_ID = _safe_int(os.getenv("OWNER_ID", "8695276303"), 8695276303)
OWNER_USERNAME = _normalize_username(os.getenv("OWNER_USERNAME", "XcT_xAyOuB"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@XcT_x_HostinG_BoT")

app = Flask(__name__)
app.secret_key = SECRET_KEY


class BotState:
    process = None
    started_at = None
    status = "stopped"
    lock = threading.Lock()


bot_state = BotState()


def db_conn():
    return sqlite3.connect(DB_PATH, timeout=30)


def db_query(sql, params=(), one=False):
    try:
        with db_conn() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            if one:
                return rows[0] if rows else None
            return rows
    except Exception as exc:
        print(f"[DB] {exc}")
        return None if one else []


def get_stats():
    def q(sql, default=0):
        try:
            with db_conn() as conn:
                row = conn.execute(sql).fetchone()
                return row[0] if row and row[0] is not None else default
        except Exception:
            return default

    return {
        "users_total": q("SELECT COUNT(*) FROM users"),
        "users_active": q("SELECT COUNT(*) FROM users WHERE banned=0"),
        "users_banned": q("SELECT COUNT(*) FROM users WHERE banned=1"),
        "users_admins": q("SELECT COUNT(*) FROM users WHERE is_admin=1"),
        "files_total": q("SELECT COUNT(*) FROM files"),
        "files_pending": q("SELECT COUNT(*) FROM files WHERE status='pending'"),
        "files_approved": q("SELECT COUNT(*) FROM files WHERE status='approved'"),
        "files_rejected": q("SELECT COUNT(*) FROM files WHERE status='rejected'"),
        "files_running": q("SELECT COUNT(*) FROM files WHERE running=1"),
        "channels": q("SELECT COUNT(*) FROM channels"),
    }


def get_users(limit=200, offset=0):
    rows = db_query(
        "SELECT id, username, first, last, joined, banned, is_admin "
        "FROM users ORDER BY joined DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ) or []
    data = []
    for r in rows:
        data.append(
            {
                "user_id": r[0],
                "username": (r[1] or "").lstrip("@"),
                "first_name": r[2] or "",
                "last_name": r[3] or "",
                "joined_at": str(r[4] or ""),
                "is_banned": bool(r[5]),
                "is_admin": bool(r[6]),
                "avatar_url": f"/api/users/{r[0]}/avatar",
            }
        )
    return data


def _derive_file_log_path(file_row):
    if not file_row:
        return None
    cont_id = file_row[9] if len(file_row) > 9 else None
    if cont_id:
        candidate = os.path.join(LOGS_DIR, f"{str(cont_id)[:12]}.log")
        if os.path.exists(candidate):
            return candidate
    # fallback by filename search
    stored_name = file_row[2] if len(file_row) > 2 else ""
    if stored_name:
        prefix = os.path.splitext(stored_name)[0][:12]
        candidate = os.path.join(LOGS_DIR, f"{prefix}.log")
        if os.path.exists(candidate):
            return candidate
    return None


def get_file_row(fid):
    return db_query("SELECT * FROM files WHERE id=?", (fid,), one=True)


def get_file_logs(fid, lines=120):
    file_row = get_file_row(fid)
    if not file_row:
        return None, "الملف غير موجود"
    log_path = _derive_file_log_path(file_row)
    if not log_path:
        return "", "لا يوجد ملف لوجز لهذا العنصر حالياً"
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as handle:
            return "".join(handle.readlines()[-lines:]), None
    except Exception as exc:
        return "", f"تعذر قراءة اللوجز: {exc}"


def get_files(limit=200):
    rows = db_query(
        "SELECT id, user_id, filename, orig_name, filepath, size, ftype, uploaded, status, cont_id, port, running "
        "FROM files ORDER BY uploaded DESC LIMIT ?",
        (limit,),
    ) or []
    data = []
    for r in rows:
        log_path = _derive_file_log_path(r)
        data.append(
            {
                "id": r[0],
                "user_id": r[1],
                "filename": r[2] or "",
                "orig_name": r[3] or r[2] or "",
                "filepath": r[4] or "",
                "size": int(r[5] or 0),
                "file_type": r[6] or "",
                "created_at": str(r[7] or ""),
                "status": r[8] or "unknown",
                "container_id": r[9] or "",
                "port": r[10],
                "is_running": bool(r[11]),
                "can_download": bool(r[4] and os.path.exists(r[4])),
                "has_logs": bool(log_path and os.path.exists(log_path)),
            }
        )
    return data


def get_admins():
    rows = db_query(
        "SELECT id, username, first FROM users WHERE is_admin=1 ORDER BY joined DESC"
    ) or []
    return [
        {
            "user_id": r[0],
            "username": (r[1] or "").lstrip("@"),
            "first_name": r[2] or "",
        }
        for r in rows
    ]


def get_active_user_ids():
    rows = db_query("SELECT id FROM users WHERE banned=0") or []
    return [row[0] for row in rows]


def start_bot_process():
    with bot_state.lock:
        if bot_state.process and bot_state.process.poll() is None:
            return False, "البوت يعمل بالفعل"
        bot_state.status = "starting"
        try:
            log_path = os.path.join(LOGS_DIR, "bot_runtime.log")
            log_file = open(log_path, "a", buffering=1, encoding="utf-8")
            log_file.write(f"\n\n===== Bot started at {datetime.utcnow().isoformat()} =====\n")
            env = os.environ.copy()
            env["WEB_PANEL_MODE"] = "1"
            bot_state.process = subprocess.Popen(
                ["python", "-u", "bot.py"],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                preexec_fn=os.setsid,
            )
            bot_state.started_at = datetime.utcnow().isoformat()
            bot_state.status = "running"
            return True, "تم تشغيل البوت بنجاح"
        except Exception as exc:
            bot_state.status = "stopped"
            return False, f"فشل تشغيل البوت: {exc}"


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
        except Exception as exc:
            bot_state.status = "stopped"
            return False, f"تعذر الإيقاف: {exc}"


def bot_is_running():
    if bot_state.process and bot_state.process.poll() is None:
        return True
    if bot_state.status == "running":
        bot_state.status = "stopped"
    return False


def send_broadcast(message_text):
    if not BOT_TOKEN:
        return {"success": 0, "failed": 0, "total": 0, "error": "BOT_TOKEN غير مضبوط"}
    text = f"📢 <b>رسالة من الإدارة</b>\n\n{message_text}\n\n👑 @{OWNER_USERNAME}"
    success = 0
    failed = 0
    user_ids = get_active_user_ids()
    for uid in user_ids:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": uid, "text": text, "parse_mode": "HTML"},
                timeout=12,
            )
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            if response.status_code == 200 and payload.get("ok"):
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        time.sleep(0.05)
    return {"success": success, "failed": failed, "total": len(user_ids)}


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def _placeholder_avatar_svg(label: str):
    chars = (label or "U")[:2].upper()
    svg = f"""
    <svg xmlns='http://www.w3.org/2000/svg' width='80' height='80' viewBox='0 0 80 80'>
      <defs>
        <linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>
          <stop offset='0%' stop-color='#667eea'/>
          <stop offset='100%' stop-color='#764ba2'/>
        </linearGradient>
      </defs>
      <rect width='80' height='80' rx='18' fill='url(#g)'/>
      <text x='40' y='47' text-anchor='middle' font-family='Segoe UI,Tahoma,sans-serif' font-size='26' font-weight='700' fill='white'>{chars}</text>
    </svg>
    """.strip()
    return Response(svg, mimetype="image/svg+xml")


def _fetch_avatar_binary(uid: int):
    if not BOT_TOKEN:
        return None
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUserProfilePhotos",
            params={"user_id": uid, "limit": 1},
            timeout=12,
        )
        data = resp.json()
        if not data.get("ok") or not data.get("result", {}).get("photos"):
            return None
        photos = data["result"]["photos"][0]
        photo = photos[-1]
        file_resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": photo["file_id"]},
            timeout=12,
        )
        file_data = file_resp.json()
        if not file_data.get("ok"):
            return None
        file_path = file_data["result"].get("file_path")
        if not file_path:
            return None
        bin_resp = requests.get(
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}",
            timeout=20,
        )
        if bin_resp.status_code != 200:
            return None
        return {
            "content": bin_resp.content,
            "content_type": bin_resp.headers.get("content-type", "image/jpeg"),
        }
    except Exception:
        return None


LOGIN_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>تسجيل الدخول</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;background:radial-gradient(circle at top,#1e2355,#0c0f21 55%,#06070f);padding:20px;color:#fff}
.card{width:100%;max-width:420px;background:rgba(18,22,46,.92);backdrop-filter:blur(18px);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:32px;box-shadow:0 24px 70px rgba(0,0,0,.45)}
.logo{width:74px;height:74px;border-radius:22px;margin:0 auto 18px;background:linear-gradient(135deg,#667eea,#764ba2);display:flex;align-items:center;justify-content:center;font-size:34px}
h1{text-align:center;font-size:28px;margin-bottom:10px}
.sub{text-align:center;color:#aab1d3;font-size:14px;margin-bottom:24px}
label{display:block;margin-bottom:8px;color:#ccd2f3;font-size:14px;font-weight:600}
input{width:100%;padding:14px 16px;border-radius:14px;border:1px solid #2e3469;background:#0f1330;color:#fff;font-size:15px}
input:focus{outline:none;border-color:#8090ff;box-shadow:0 0 0 3px rgba(128,144,255,.15)}
button{width:100%;padding:14px;border:0;border-radius:14px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;font-size:16px;font-weight:800;cursor:pointer;margin-top:18px}
.err{background:rgba(231,76,60,.15);border:1px solid rgba(231,76,60,.35);color:#ffb4b4;padding:12px 14px;border-radius:12px;margin-bottom:16px;text-align:center}
.note{text-align:center;color:#7f87ad;font-size:12px;margin-top:14px}
</style>
</head>
<body>
<div class="card">
  <div class="logo">🔐</div>
  <h1>لوحة المالك</h1>
  <p class="sub">تحكم كامل في البوت والمستخدمين والملفات</p>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST">
    <label>كلمة المرور</label>
    <input type="password" name="password" placeholder="أدخل كلمة المرور" required autofocus>
    <button type="submit">دخول سريع</button>
  </form>
  <div class="note">{{ bot_username }} • @{{ owner_username }}</div>
</div>
</body>
</html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>لوحة المالك</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}
body{background:#090b16;color:#eef1ff;min-height:100vh}
a{text-decoration:none;color:inherit}
.topbar{padding:18px 24px;background:linear-gradient(135deg,#101633,#1b2558);border-bottom:1px solid rgba(255,255,255,.07);display:flex;justify-content:space-between;align-items:center;gap:18px;flex-wrap:wrap;position:sticky;top:0;z-index:5}
.brand h1{font-size:24px;margin-bottom:6px}
.brand p{font-size:13px;color:#a7afd4}
.top-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.container{max-width:1450px;margin:0 auto;padding:24px}
.card{background:linear-gradient(180deg,rgba(22,28,59,.96),rgba(11,14,30,.96));border:1px solid rgba(255,255,255,.08);border-radius:22px;box-shadow:0 18px 55px rgba(0,0,0,.32)}
.status-card{padding:24px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;gap:18px;flex-wrap:wrap}
.status-meta h2{font-size:24px;margin-bottom:8px}
.status-meta p{font-size:13px;color:#aeb6db;margin-top:5px}
.controls{display:flex;gap:10px;flex-wrap:wrap}
.btn{border:0;border-radius:12px;padding:11px 16px;font-size:14px;font-weight:800;cursor:pointer;color:#fff;transition:.2s transform,.2s box-shadow}
.btn:hover{transform:translateY(-2px);box-shadow:0 10px 24px rgba(0,0,0,.28)}
.btn:disabled{opacity:.55;cursor:not-allowed;transform:none;box-shadow:none}
.btn-primary{background:linear-gradient(135deg,#5865f2,#7d4dff)}
.btn-success{background:linear-gradient(135deg,#1fbf75,#16a085)}
.btn-danger{background:linear-gradient(135deg,#f1416c,#d63031)}
.btn-warning{background:linear-gradient(135deg,#f39c12,#e67e22)}
.btn-dark{background:#161b34;border:1px solid rgba(255,255,255,.08)}
.status-badge{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;font-size:13px;font-weight:900}
.s-running{background:rgba(46,204,113,.18);color:#58f09d}
.s-stopped{background:rgba(241,65,108,.18);color:#ff9ab5}
.s-starting,.s-stopping{background:rgba(243,156,18,.18);color:#ffd07f}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:20px}
.stat{padding:20px;border-radius:18px;border:1px solid rgba(255,255,255,.07);background:linear-gradient(180deg,#151b39,#0d1022)}
.stat .icn{font-size:24px;margin-bottom:10px;display:block}
.stat .num{font-size:30px;font-weight:900;color:#8ea2ff}
.stat .lbl{font-size:13px;color:#abb3da;margin-top:6px}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.tab{padding:12px 18px;border-radius:14px;background:#12162d;border:1px solid rgba(255,255,255,.06);color:#b7bfdf;font-weight:800;cursor:pointer}
.tab.active{background:linear-gradient(135deg,#5a6bf4,#7c4cff);color:#fff}
.panel{display:none;padding:22px}
.panel.active{display:block}
.panel-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.panel h3{font-size:20px}
textarea,input[type=text]{width:100%;padding:14px 16px;border-radius:14px;border:1px solid rgba(255,255,255,.08);background:#0d1124;color:#eef1ff;font-size:14px}
textarea{min-height:140px;resize:vertical}
textarea:focus,input:focus{outline:none;border-color:#7c8cff;box-shadow:0 0 0 3px rgba(124,140,255,.15)}
.field{margin-bottom:16px}
.field label{display:block;margin-bottom:9px;font-size:14px;color:#c4cbeb;font-weight:700}
.table-wrap{overflow:auto;border-radius:16px;border:1px solid rgba(255,255,255,.06)}
table{width:100%;border-collapse:collapse;min-width:900px;background:#0c1021}
th,td{padding:14px 12px;border-bottom:1px solid rgba(255,255,255,.06);text-align:right;font-size:13px;vertical-align:middle}
th{background:#121734;color:#9eb0ff;position:sticky;top:0;font-size:12px;letter-spacing:.2px}
tr:hover td{background:rgba(255,255,255,.02)}
.badge{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;font-size:11px;font-weight:800}
.b-admin{background:rgba(155,89,182,.18);color:#daa5ff}
.b-banned{background:rgba(231,76,60,.18);color:#ffb0a8}
.b-active{background:rgba(39,174,96,.16);color:#84f0ae}
.b-pending{background:rgba(243,156,18,.16);color:#ffd27c}
.b-approved{background:rgba(39,174,96,.16);color:#84f0ae}
.b-rejected{background:rgba(231,76,60,.16);color:#ffb0a8}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.icon-btn{border:0;border-radius:10px;padding:8px 10px;font-size:12px;font-weight:800;cursor:pointer;color:#fff}
.avatar{width:42px;height:42px;border-radius:14px;object-fit:cover;border:1px solid rgba(255,255,255,.08);background:#141934}
.muted{color:#93a0d6;font-size:12px}
.alert{padding:13px 15px;border-radius:14px;margin-bottom:14px;font-size:14px}
.alert-info{background:rgba(52,152,219,.14);border:1px solid rgba(52,152,219,.35);color:#9fd7ff}
.alert-success{background:rgba(46,204,113,.14);border:1px solid rgba(46,204,113,.35);color:#8ff0b9}
.alert-error{background:rgba(231,76,60,.14);border:1px solid rgba(231,76,60,.35);color:#ffbbb0}
pre.log{background:#070a16;border:1px solid rgba(255,255,255,.06);color:#93ffb0;padding:16px;border-radius:16px;max-height:520px;overflow:auto;line-height:1.55;font-size:12px;white-space:pre-wrap}
.modal{position:fixed;inset:0;background:rgba(3,5,12,.72);display:none;align-items:center;justify-content:center;padding:20px;z-index:15}
.modal.open{display:flex}
.modal-card{width:min(980px,100%);max-height:85vh;overflow:hidden;background:#0e1328;border:1px solid rgba(255,255,255,.08);border-radius:24px;box-shadow:0 30px 80px rgba(0,0,0,.45)}
.modal-head{padding:18px 20px;border-bottom:1px solid rgba(255,255,255,.06);display:flex;justify-content:space-between;align-items:center;gap:10px}
.modal-body{padding:20px}
.footer{padding:20px 0 8px;text-align:center;color:#7f88b7;font-size:12px}
@media (max-width:780px){.container{padding:16px}.brand h1{font-size:20px}.stats-grid{grid-template-columns:repeat(2,1fr)}.panel{padding:16px}}
</style>
</head>
<body>
<div class="topbar">
  <div class="brand">
    <h1>👑 لوحة المالك</h1>
    <p>البوت: <b>{{ bot_username }}</b> • المالك: <b>@{{ owner_username }}</b> • ID: <b>{{ owner_id }}</b></p>
  </div>
  <div class="top-actions">
    <span class="muted">إدارة المستخدمين • الملفات • اللوجز</span>
    <a href="/logout"><button class="btn btn-danger">خروج</button></a>
  </div>
</div>

<div class="container">
  <div class="card status-card">
    <div class="status-meta">
      <h2>⚡ حالة البوت <span id="bot-status-badge" class="status-badge s-stopped">جارٍ التحميل</span></h2>
      <p id="uptime-text">جارٍ قراءة الحالة...</p>
      <p>لوحة مطورة لعرض المستخدمين مع صورهم، وتنزيل الملفات، وقراءة لوجز كل ملف.</p>
    </div>
    <div class="controls">
      <button class="btn btn-success" id="btn-start" onclick="startBot()">▶️ تشغيل</button>
      <button class="btn btn-danger" id="btn-stop" onclick="stopBot()">⏹️ إيقاف</button>
      <button class="btn btn-warning" onclick="restartBot()">🔄 إعادة تشغيل</button>
      <button class="btn btn-primary" onclick="refreshAll(true)">🔃 تحديث شامل</button>
    </div>
  </div>

  <div class="stats-grid" id="stats-grid">
    <div class="stat"><span class="icn">👥</span><div class="num" id="s-users-total">0</div><div class="lbl">إجمالي المستخدمين</div></div>
    <div class="stat"><span class="icn">✅</span><div class="num" id="s-users-active">0</div><div class="lbl">المستخدمون النشطون</div></div>
    <div class="stat"><span class="icn">🚫</span><div class="num" id="s-users-banned">0</div><div class="lbl">المحظورون</div></div>
    <div class="stat"><span class="icn">👑</span><div class="num" id="s-users-admins">0</div><div class="lbl">المشرفون</div></div>
    <div class="stat"><span class="icn">📁</span><div class="num" id="s-files-total">0</div><div class="lbl">إجمالي الملفات</div></div>
    <div class="stat"><span class="icn">⏳</span><div class="num" id="s-files-pending">0</div><div class="lbl">بانتظار الموافقة</div></div>
    <div class="stat"><span class="icn">🟢</span><div class="num" id="s-files-running">0</div><div class="lbl">الملفات العاملة</div></div>
    <div class="stat"><span class="icn">📢</span><div class="num" id="s-channels">0</div><div class="lbl">القنوات</div></div>
  </div>

  <div class="tabs">
    <button class="tab active" data-tab="broadcast">📢 إذاعة</button>
    <button class="tab" data-tab="users">👥 المستخدمون</button>
    <button class="tab" data-tab="files">📁 ملفات المستخدمين</button>
    <button class="tab" data-tab="admins">👑 المشرفون</button>
    <button class="tab" data-tab="logs">📜 لوجز البوت</button>
  </div>

  <div class="card panel active" id="p-broadcast">
    <div class="panel-head"><h3>📢 إرسال إذاعة</h3></div>
    <div id="broadcast-result"></div>
    <div class="field">
      <label>نص الرسالة</label>
      <textarea id="broadcast-msg" placeholder="اكتب الرسالة هنا..."></textarea>
    </div>
    <button class="btn btn-primary" id="bc-btn" onclick="sendBroadcast()">📤 إرسال الإذاعة</button>
  </div>

  <div class="card panel" id="p-users">
    <div class="panel-head">
      <h3>👥 المستخدمون مع صورهم</h3>
      <button class="btn btn-dark" onclick="loadUsers()">🔃 تحديث</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>الصورة</th><th>ID</th><th>المعرف</th><th>الاسم</th><th>تاريخ الانضمام</th><th>الحالة</th><th>إجراء</th></tr></thead>
        <tbody id="users-tbody"></tbody>
      </table>
    </div>
  </div>

  <div class="card panel" id="p-files">
    <div class="panel-head">
      <h3>📁 ملفات المستخدمين</h3>
      <button class="btn btn-dark" onclick="loadFiles()">🔃 تحديث</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>ID</th><th>المستخدم</th><th>الاسم الأصلي</th><th>الاسم المخزن</th><th>النوع</th><th>الحجم</th><th>الحالة</th><th>التشغيل</th><th>التاريخ</th><th>إجراءات</th></tr></thead>
        <tbody id="files-tbody"></tbody>
      </table>
    </div>
  </div>

  <div class="card panel" id="p-admins">
    <div class="panel-head">
      <h3>👑 المشرفون</h3>
      <button class="btn btn-dark" onclick="loadAdmins()">🔃 تحديث</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>ID</th><th>المعرف</th><th>الاسم</th></tr></thead>
        <tbody id="admins-tbody"></tbody>
      </table>
    </div>
  </div>

  <div class="card panel" id="p-logs">
    <div class="panel-head">
      <h3>📜 لوجز تشغيل البوت</h3>
      <button class="btn btn-dark" onclick="loadLogs()">🔃 تحديث</button>
    </div>
    <pre class="log" id="logs-content">جارٍ التحميل...</pre>
  </div>

  <div class="footer">{{ bot_username }} • @{{ owner_username }} • Render / Flask Panel</div>
</div>

<div class="modal" id="file-log-modal">
  <div class="modal-card">
    <div class="modal-head">
      <h3 id="file-log-title">📜 لوجز الملف</h3>
      <div class="actions">
        <button class="btn btn-dark" id="file-log-refresh">🔄 تحديث</button>
        <button class="btn btn-danger" onclick="closeFileLogModal()">إغلاق</button>
      </div>
    </div>
    <div class="modal-body">
      <pre class="log" id="file-log-content">جارٍ التحميل...</pre>
    </div>
  </div>
</div>

<script>
const STATE = {fileLogId:null};
function esc(v){return String(v ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function shortDate(v){return v ? String(v).replace('T',' ').slice(0,16) : '-';}
async function api(url, opts={}){
  const res = await fetch(url, {credentials:'same-origin', ...opts});
  const type = res.headers.get('content-type') || '';
  if(type.includes('application/json')) return await res.json();
  throw new Error('استجابة غير متوقعة');
}
function setTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.dataset.tab===name));
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active', p.id===('p-'+name)));
  if(name==='users') loadUsers();
  if(name==='files') loadFiles();
  if(name==='admins') loadAdmins();
  if(name==='logs') loadLogs();
}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>setTab(t.dataset.tab));

async function refreshStatus(){
  const s = await api('/api/status');
  const badge = document.getElementById('bot-status-badge');
  const map = {running:'🟢 يعمل', stopped:'🔴 متوقف', starting:'🟡 جارٍ التشغيل', stopping:'🟡 جارٍ الإيقاف'};
  badge.textContent = map[s.status] || s.status;
  badge.className = 'status-badge s-' + (s.status || 'stopped');
  document.getElementById('btn-start').disabled = ['running','starting'].includes(s.status);
  document.getElementById('btn-stop').disabled = ['stopped'].includes(s.status);
  document.getElementById('uptime-text').innerHTML = s.uptime ? ('⏱️ يعمل منذ <b>' + esc(s.uptime) + '</b>') : '⏸️ البوت متوقف حالياً';
}
async function refreshStats(){
  const s = await api('/api/stats');
  ['users_total','users_active','users_banned','users_admins','files_total','files_pending','files_running','channels'].forEach(k=>{
    const el = document.getElementById('s-' + k.replaceAll('_','-'));
    if(el) el.textContent = s[k] || 0;
  });
}
async function refreshAll(withTables=false){
  await Promise.all([refreshStatus(), refreshStats()]);
  if(withTables){
    const active = document.querySelector('.tab.active')?.dataset.tab;
    if(active==='users') await loadUsers();
    if(active==='files') await loadFiles();
    if(active==='admins') await loadAdmins();
    if(active==='logs') await loadLogs();
  }
}
async function startBot(){
  const r = await api('/api/bot/start', {method:'POST'});
  alert(r.message || 'تم');
  await refreshAll(true);
}
async function stopBot(){
  if(!confirm('هل تريد إيقاف البوت؟')) return;
  const r = await api('/api/bot/stop', {method:'POST'});
  alert(r.message || 'تم');
  await refreshAll(true);
}
async function restartBot(){
  if(!confirm('إعادة تشغيل البوت؟')) return;
  await api('/api/bot/stop', {method:'POST'}).catch(()=>null);
  setTimeout(async()=>{ await api('/api/bot/start', {method:'POST'}).catch(()=>null); await refreshAll(true); }, 1800);
}

async function sendBroadcast(){
  const msg = document.getElementById('broadcast-msg').value.trim();
  if(!msg){ alert('اكتب الرسالة أولاً'); return; }
  if(!confirm('إرسال الرسالة لجميع المستخدمين؟')) return;
  const btn = document.getElementById('bc-btn');
  const box = document.getElementById('broadcast-result');
  btn.disabled = true; btn.textContent = '⏳ جارٍ الإرسال...';
  box.innerHTML = '<div class="alert alert-info">جارٍ إرسال الرسالة...</div>';
  try{
    const r = await api('/api/broadcast', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:msg})});
    if(r.ok){
      box.innerHTML = `<div class="alert alert-success">✅ تم الإرسال بنجاح<br>نجح: <b>${r.result.success}</b> • فشل: <b>${r.result.failed}</b> • إجمالي: <b>${r.result.total}</b></div>`;
      document.getElementById('broadcast-msg').value = '';
    } else {
      box.innerHTML = `<div class="alert alert-error">❌ ${esc(r.error || 'فشل الإرسال')}</div>`;
    }
  }catch(e){
    box.innerHTML = `<div class="alert alert-error">❌ ${esc(e.message || e)}</div>`;
  }
  btn.disabled = false; btn.textContent = '📤 إرسال الإذاعة';
}

async function loadUsers(){
  const users = await api('/api/users');
  const tb = document.getElementById('users-tbody');
  tb.innerHTML = users.map(u=>{
    const badges = [];
    if(u.is_admin) badges.push('<span class="badge b-admin">👑 مشرف</span>');
    if(u.is_banned) badges.push('<span class="badge b-banned">🚫 محظور</span>');
    else badges.push('<span class="badge b-active">✅ نشط</span>');
    const action = u.is_banned
      ? `<button class="icon-btn btn-success" onclick="userAction(${u.user_id}, 'unban')">فك حظر</button>`
      : `<button class="icon-btn btn-danger" onclick="userAction(${u.user_id}, 'ban')">حظر</button>`;
    const uname = u.username ? '@' + esc(u.username) : '-';
    const fullName = [u.first_name || '', u.last_name || ''].join(' ').trim() || '-';
    return `<tr>
      <td><img class="avatar" src="${u.avatar_url}" alt="avatar"></td>
      <td>${u.user_id}</td>
      <td>${uname}</td>
      <td>${esc(fullName)}</td>
      <td>${esc(shortDate(u.joined_at))}</td>
      <td>${badges.join(' ')}</td>
      <td>${action}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" style="text-align:center">لا يوجد مستخدمون</td></tr>';
}
async function userAction(uid, action){
  const r = await api(`/api/users/${uid}/${action}`, {method:'POST'});
  alert(r.message || 'تم');
  await Promise.all([loadUsers(), refreshStats()]);
}

async function loadFiles(){
  const files = await api('/api/files');
  const tb = document.getElementById('files-tbody');
  tb.innerHTML = files.map(f=>{
    const statusClass = ({pending:'b-pending', approved:'b-approved', rejected:'b-rejected'})[f.status] || 'b-pending';
    const runState = f.is_running ? '🟢 يعمل' : '🔴 متوقف';
    const actions = [];
    if(f.can_download){
      actions.push(`<a href="/api/files/${f.id}/download" target="_blank"><button class="icon-btn btn-primary">📥 تحميل</button></a>`);
    }
    actions.push(`<button class="icon-btn btn-warning" onclick="showFileLogs(${f.id}, '${esc(f.orig_name || f.filename)}')">📜 Logs</button>`);
    return `<tr>
      <td>${f.id}</td>
      <td>${f.user_id}</td>
      <td>${esc(f.orig_name || '-')}</td>
      <td>${esc(f.filename || '-')}</td>
      <td>${esc((f.file_type || '').toUpperCase())}</td>
      <td>${Math.max(0, Math.round((f.size || 0)/1024))} KB</td>
      <td><span class="badge ${statusClass}">${esc(f.status)}</span></td>
      <td>${runState}</td>
      <td>${esc(shortDate(f.created_at))}</td>
      <td><div class="actions">${actions.join('')}</div></td>
    </tr>`;
  }).join('') || '<tr><td colspan="10" style="text-align:center">لا يوجد ملفات</td></tr>';
}

async function loadAdmins(){
  const admins = await api('/api/admins');
  const tb = document.getElementById('admins-tbody');
  tb.innerHTML = admins.map(a=>`<tr><td>${a.user_id}</td><td>${a.username ? '@' + esc(a.username) : '-'}</td><td>${esc(a.first_name || '-')}</td></tr>`).join('') || '<tr><td colspan="3" style="text-align:center">لا يوجد مشرفون</td></tr>';
}

async function loadLogs(){
  const r = await api('/api/logs');
  document.getElementById('logs-content').textContent = r.log || '(لا يوجد سجل)';
}

function closeFileLogModal(){
  document.getElementById('file-log-modal').classList.remove('open');
}
async function showFileLogs(fid, title=''){
  STATE.fileLogId = fid;
  document.getElementById('file-log-title').textContent = '📜 لوجز الملف ' + title;
  document.getElementById('file-log-content').textContent = 'جارٍ التحميل...';
  document.getElementById('file-log-modal').classList.add('open');
  await refreshFileLog();
}
async function refreshFileLog(){
  if(!STATE.fileLogId) return;
  const r = await api(`/api/files/${STATE.fileLogId}/logs`);
  document.getElementById('file-log-content').textContent = r.log || r.message || '(لا يوجد لوجز)';
}
document.getElementById('file-log-refresh').onclick = refreshFileLog;
document.getElementById('file-log-modal').addEventListener('click', e=>{ if(e.target.id==='file-log-modal') closeFileLogModal(); });

refreshAll();
setInterval(refreshAll, 8000);
</script>
</body>
</html>"""


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        error = 'كلمة المرور غير صحيحة'
    return render_template_string(LOGIN_HTML, error=error, bot_username=BOT_USERNAME, owner_username=OWNER_USERNAME)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@require_login
def dashboard():
    return render_template_string(
        DASHBOARD_HTML,
        bot_username=BOT_USERNAME,
        owner_username=OWNER_USERNAME,
        owner_id=OWNER_ID,
    )


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'bot': bot_state.status, 'time': datetime.utcnow().isoformat()})


@app.route('/ping')
def ping():
    return 'pong'


@app.route('/api/status')
@require_login
def api_status():
    running = bot_is_running()
    uptime = ''
    if running and bot_state.started_at:
        try:
            delta = datetime.utcnow() - datetime.fromisoformat(bot_state.started_at)
            seconds = int(delta.total_seconds())
            hours, rem = divmod(seconds, 3600)
            minutes, sec = divmod(rem, 60)
            uptime = f'{hours}س {minutes}د {sec}ث'
        except Exception:
            uptime = ''
    return jsonify({'status': bot_state.status, 'running': running, 'started_at': bot_state.started_at, 'uptime': uptime})


@app.route('/api/stats')
@require_login
def api_stats():
    return jsonify(get_stats())


@app.route('/api/users')
@require_login
def api_users():
    return jsonify(get_users())


@app.route('/api/users/<int:uid>/avatar')
@require_login
def api_user_avatar(uid):
    row = db_query('SELECT username, first, last FROM users WHERE id=?', (uid,), one=True)
    label = 'U'
    if row:
        username = (row[0] or '').strip().lstrip('@')
        first = (row[1] or '').strip()
        last = (row[2] or '').strip()
        label = username[:2] or (first[:1] + last[:1]).strip() or first[:2] or 'U'
    avatar = _fetch_avatar_binary(uid)
    if avatar:
        return Response(avatar['content'], mimetype=avatar['content_type'])
    return _placeholder_avatar_svg(label)


@app.route('/api/files')
@require_login
def api_files():
    return jsonify(get_files())


@app.route('/api/files/<int:fid>/download')
@require_login
def api_file_download(fid):
    row = get_file_row(fid)
    if not row:
        abort(404)
    path = row[4]
    download_name = row[3] or row[2] or f'file-{fid}'
    if not path or not os.path.exists(path):
        return jsonify({'ok': False, 'message': 'الملف غير موجود على الخادم'}), 404
    return send_file(path, as_attachment=True, download_name=download_name)


@app.route('/api/files/<int:fid>/logs')
@require_login
def api_file_logs(fid):
    log, error = get_file_logs(fid)
    if log is None:
        return jsonify({'ok': False, 'message': error or 'الملف غير موجود'}), 404
    return jsonify({'ok': True, 'log': log or '', 'message': error or ''})


@app.route('/api/admins')
@require_login
def api_admins():
    return jsonify(get_admins())


@app.route('/api/users/<int:uid>/ban', methods=['POST'])
@require_login
def api_ban(uid):
    try:
        with db_conn() as conn:
            conn.execute('UPDATE users SET banned=1 WHERE id=?', (uid,))
            conn.commit()
        return jsonify({'ok': True, 'message': f'تم حظر المستخدم {uid}'})
    except Exception as exc:
        return jsonify({'ok': False, 'message': str(exc)}), 500


@app.route('/api/users/<int:uid>/unban', methods=['POST'])
@require_login
def api_unban(uid):
    try:
        with db_conn() as conn:
            conn.execute('UPDATE users SET banned=0 WHERE id=?', (uid,))
            conn.commit()
        return jsonify({'ok': True, 'message': f'تم فك حظر المستخدم {uid}'})
    except Exception as exc:
        return jsonify({'ok': False, 'message': str(exc)}), 500


@app.route('/api/broadcast', methods=['POST'])
@require_login
def api_broadcast():
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'ok': False, 'error': 'الرسالة فارغة'}), 400
    return jsonify({'ok': True, 'result': send_broadcast(message)})


@app.route('/api/bot/start', methods=['POST'])
@require_login
def api_bot_start():
    ok, message = start_bot_process()
    return jsonify({'ok': ok, 'message': message})


@app.route('/api/bot/stop', methods=['POST'])
@require_login
def api_bot_stop():
    ok, message = stop_bot_process()
    return jsonify({'ok': ok, 'message': message})


@app.route('/api/logs')
@require_login
def api_logs():
    path = os.path.join(LOGS_DIR, 'bot_runtime.log')
    if not os.path.exists(path):
        return jsonify({'log': '(لا يوجد سجل بعد)'})
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as handle:
            return jsonify({'log': handle.read()[-20000:]})
    except Exception as exc:
        return jsonify({'log': f'خطأ أثناء قراءة السجل: {exc}'})


def auto_start_bot():
    time.sleep(3)
    print('[Panel] Auto-starting bot...')
    ok, msg = start_bot_process()
    print(f'[Panel] {msg}')


if __name__ == '__main__':
    port = int(os.getenv('PORT', '10000'))
    threading.Thread(target=auto_start_bot, daemon=True).start()
    try:
        from keepalive import start_keepalive
        start_keepalive()
    except Exception as exc:
        print(f'[KeepAlive] failed: {exc}')
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
