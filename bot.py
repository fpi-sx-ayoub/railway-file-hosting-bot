# ════════════════════════════════════════════════════════════════
#  بوت استضافة الملفات - النسخة الكاملة المُصلَحة والمُطوَّرة
#  FpI sX tEaM
# ════════════════════════════════════════════════════════════════

import os, sys, subprocess

# ── تثبيت تلقائي (تم التعطيل للاستضافة) ─────────────────────────
# تم نقل التثبيت إلى Dockerfile لضمان السرعة والاستقرار في Railway


# ── استيرادات ──────────────────────────────────────────────────
import asyncio, logging, sqlite3, shutil, zipfile
import random, string, time, re
from datetime import datetime
from functools import wraps

from telegram import (Update, InlineKeyboardButton,
                      InlineKeyboardMarkup, Document)
from telegram.ext import (Application, CommandHandler,
                           MessageHandler, CallbackQueryHandler,
                           filters, ContextTypes)
from telegram.constants import ParseMode

try:
    import docker as _docker_lib
    _DOCKER_OK = True
except ImportError:
    _DOCKER_OK = False

from dotenv import load_dotenv

# ════════════════════════════════════════════════════════════════
#  إعدادات
# ════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
LOG = logging.getLogger(__name__)
load_dotenv()

BOT_TOKEN    = os.getenv("BOT_TOKEN",    "8466789309:AAGSRU-Dmk-u9MQU0TyzP4sk-VW6cfVa6Ec")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@MTX_SX_TEAM_BOT")
OWNER_ID     = int(os.getenv("OWNER_ID", "7375963526"))
OWNER_USER   = os.getenv("OWNER_USERNAME","noseyrobot")
TEAM_NAME    = "FpI sX tEaM"
DOCKER_NET   = os.getenv("DOCKER_NETWORK","bridge")

UPLOAD_DIR   = "uploads"
DB_PATH      = "bot_data.db"
LOGS_DIR     = "logs"

MAX_FILE_MB  = 100
ALLOWED_EXT  = ["py","php","js","sh","zip"]

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,   exist_ok=True)

# ════════════════════════════════════════════════════════════════
#  قاعدة البيانات
# ════════════════════════════════════════════════════════════════
class DB:
    def __init__(self):
        self._init()

    def _init(self):
        c = self._conn()
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT, first TEXT, last TEXT,
            joined TIMESTAMP, banned INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            files_count INTEGER DEFAULT 0)""")

        c.execute("""CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, filename TEXT, orig_name TEXT,
            filepath TEXT, size INTEGER, ftype TEXT,
            uploaded TIMESTAMP, status TEXT DEFAULT 'pending',
            cont_id TEXT, port INTEGER,
            running INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id))""")

        c.execute("""CREATE TABLE IF NOT EXISTS admins(
            id INTEGER PRIMARY KEY, added_by INTEGER,
            added_at TIMESTAMP,
            FOREIGN KEY(id) REFERENCES users(id))""")

        c.execute("""CREATE TABLE IF NOT EXISTS channels(
            ch_id INTEGER PRIMARY KEY,
            username TEXT, title TEXT,
            added_by INTEGER, added_at TIMESTAMP)""")

        c.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT)""")

        c.execute("INSERT OR IGNORE INTO settings VALUES('max_files','3')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('stealth_mode','1')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('virus_scan','1')")

        c.commit()
        c.close()
        self.add_admin(OWNER_ID, OWNER_ID)

    def _conn(self):
        return sqlite3.connect(DB_PATH)

    def get_setting(self, key, default=None):
        c = self._conn()
        r = c.execute("SELECT value FROM settings WHERE key=?",
                      (key,)).fetchone()
        c.close()
        return r[0] if r else default

    def set_setting(self, key, value):
        c = self._conn()
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",
                  (key, str(value)))
        c.commit(); c.close()

    def get_max_files(self):
        return int(self.get_setting("max_files", 3))

    def set_max_files(self, n):
        self.set_setting("max_files", n)

    def add_user(self, uid, uname=None, first=None, last=None):
        c = self._conn()
        c.execute("""INSERT OR IGNORE INTO users
                     (id,username,first,last,joined)
                     VALUES(?,?,?,?,?)""",
                  (uid,uname,first,last,datetime.now()))
        c.commit(); c.close()

    def get_user(self, uid):
        c = self._conn()
        r = c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        c.close(); return r

    def get_all_users(self):
        c = self._conn()
        r = c.execute("SELECT id,username,first,last,banned,is_admin FROM users").fetchall()
        c.close(); return r

    def get_active_ids(self):
        c = self._conn()
        r = c.execute("SELECT id FROM users WHERE banned=0").fetchall()
        c.close(); return [x[0] for x in r]

    def ban_user(self, uid):
        c = self._conn()
        c.execute("UPDATE users SET banned=1 WHERE id=?",(uid,))
        c.commit(); c.close()

    def unban_user(self, uid):
        c = self._conn()
        c.execute("UPDATE users SET banned=0 WHERE id=?",(uid,))
        c.commit(); c.close()

    def add_admin(self, uid, added_by):
        c = self._conn()
        c.execute("""INSERT OR IGNORE INTO admins
                     VALUES(?,?,?)""",(uid,added_by,datetime.now()))
        c.execute("UPDATE users SET is_admin=1 WHERE id=?",(uid,))
        c.commit(); c.close()

    def remove_admin(self, uid):
        c = self._conn()
        c.execute("DELETE FROM admins WHERE id=?",(uid,))
        c.execute("UPDATE users SET is_admin=0 WHERE id=?",(uid,))
        c.commit(); c.close()

    def is_admin(self, uid):
        if uid == OWNER_ID: return True
        c = self._conn()
        r = c.execute("SELECT 1 FROM admins WHERE id=?",(uid,)).fetchone()
        c.close(); return r is not None

    def get_admins(self):
        c = self._conn()
        r = c.execute("SELECT id,added_by,added_at FROM admins").fetchall()
        c.close(); return r

    # ══════════════════════════════════════════════════════
    # ✅ إصلاح Bug #1: استخدام cursor.lastrowid بدل c.lastrowid
    # ══════════════════════════════════════════════════════
    def add_file(self, uid, fname, orig, path, size, ftype):
        c   = self._conn()
        cur = c.execute(                          # ← حفظ المؤشر
            """INSERT INTO files
               (user_id,filename,orig_name,filepath,size,ftype,uploaded)
               VALUES(?,?,?,?,?,?,?)""",
            (uid, fname, orig, path, size, ftype, datetime.now()))
        fid = cur.lastrowid                       # ← المؤشر وليس الاتصال
        c.execute(
            "UPDATE users SET files_count=files_count+1 WHERE id=?",
            (uid,))
        c.commit(); c.close()
        return fid

    def get_user_files(self, uid):
        c = self._conn()
        r = c.execute("""SELECT * FROM files WHERE user_id=?
                         ORDER BY uploaded DESC""",(uid,)).fetchall()
        c.close(); return r

    def get_all_files(self):
        c = self._conn()
        r = c.execute("SELECT * FROM files ORDER BY uploaded DESC").fetchall()
        c.close(); return r

    def get_pending(self):
        c = self._conn()
        r = c.execute("""SELECT * FROM files WHERE status='pending'
                         ORDER BY uploaded""").fetchall()
        c.close(); return r

    def get_running_files(self):
        c = self._conn()
        r = c.execute("SELECT * FROM files WHERE running=1").fetchall()
        c.close(); return r

    def get_file(self, fid):
        c = self._conn()
        r = c.execute("SELECT * FROM files WHERE id=?",(fid,)).fetchone()
        c.close(); return r

    def update_status(self, fid, status, cont_id=None, port=None):
        c = self._conn()
        if cont_id and port:
            c.execute("""UPDATE files
                         SET status=?,cont_id=?,port=?,running=1
                         WHERE id=?""",(status,cont_id,port,fid))
        else:
            c.execute("UPDATE files SET status=? WHERE id=?",(status,fid))
        c.commit(); c.close()

    def stop_file(self, fid):
        c = self._conn()
        c.execute("UPDATE files SET running=0,cont_id=NULL WHERE id=?",(fid,))
        c.commit(); c.close()

    # ══════════════════════════════════════════════════════
    # ✅ إصلاح Bug #2: جلب user_id قبل الحذف لا بعده
    # ══════════════════════════════════════════════════════
    def delete_file(self, fid):
        c = self._conn()
        # ← أولاً نجلب user_id قبل حذف السجل
        row = c.execute(
            "SELECT user_id FROM files WHERE id=?", (fid,)).fetchone()
        c.execute("DELETE FROM files WHERE id=?", (fid,))
        if row:
            c.execute(
                "UPDATE users SET files_count=MAX(0,files_count-1) WHERE id=?",
                (row[0],))
        c.commit(); c.close()

    def delete_missing_files(self):
        c = self._conn()
        all_files = c.execute("SELECT id,filepath FROM files").fetchall()
        deleted = []
        for fid, fpath in all_files:
            if not os.path.exists(fpath):
                c.execute("DELETE FROM files WHERE id=?", (fid,))
                deleted.append(fid)
        c.commit(); c.close()
        return deleted

    def add_channel(self, ch_id, uname, title, added_by):
        c = self._conn()
        c.execute("""INSERT OR REPLACE INTO channels VALUES(?,?,?,?,?)""",
                  (ch_id,uname,title,added_by,datetime.now()))
        c.commit(); c.close()

    def del_channel(self, ch_id):
        c = self._conn()
        c.execute("DELETE FROM channels WHERE ch_id=?",(ch_id,))
        c.commit(); c.close()

    def get_channels(self):
        c = self._conn()
        r = c.execute("SELECT * FROM channels").fetchall()
        c.close(); return r

    def stats(self):
        c = self._conn()
        def q(sql): return c.execute(sql).fetchone()[0]
        s = {
            "users":    q("SELECT COUNT(*) FROM users"),
            "banned":   q("SELECT COUNT(*) FROM users WHERE banned=1"),
            "admins":   q("SELECT COUNT(*) FROM admins"),
            "files":    q("SELECT COUNT(*) FROM files"),
            "running":  q("SELECT COUNT(*) FROM files WHERE running=1"),
            "pending":  q("SELECT COUNT(*) FROM files WHERE status='pending'"),
            "approved": q("SELECT COUNT(*) FROM files WHERE status='approved'"),
            "rejected": q("SELECT COUNT(*) FROM files WHERE status='rejected'"),
        }
        c.close(); return s


# ════════════════════════════════════════════════════════════════
#  كاشف الفيروسات الذكي
# ════════════════════════════════════════════════════════════════
class VirusScanner:
    PATTERNS = {
        "reverse_shell":    (90, [
            r"socket\.connect\(", r"os\.dup2\(", r"bash\s*-i",
            r"/dev/tcp/", r"nc\s+-e", r"ncat\s+", r"socat\s+"]),
        "crypto_miner":     (85, [
            r"stratum\+tcp", r"monero", r"xmrig",
            r"hashrate", r"pool\.minexmr"]),
        "ddos":             (85, [
            r"flood\(", r"syn_flood", r"udp_flood",
            r"slowloris", r"asyncio.*loop.*send.*\d{3,}"]),
        "credential_theft": (80, [
            r"\.env", r"id_rsa", r"password.*=.*['\"]",
            r"token.*=.*['\"]", r"api.?key.*=.*['\"]"]),
        "ransomware":       (90, [
            r"\.encrypt\(", r"AES\.(encrypt|decrypt)",
            r"fernet", r"cryptography\.fernet"]),
        "root_escalation":  (85, [
            r"setuid\(0\)", r"chmod.*4755",
            r"sudo.*nopasswd", r"/etc/sudoers"]),
        "dangerous_exec":   (60, [
            r"\beval\(", r"\bexec\(", r"__import__",
            r"compile\(.*exec", r"execfile\("]),
        "system_cmd":       (55, [
            r"os\.system\(", r"subprocess\.(run|Popen|call)\(",
            r"popen\(", r"shell=True"]),
        "net_connection":   (45, [
            r"socket\.socket\(", r"requests\.(get|post)\(",
            r"urllib\.request", r"http\.client",
            r"ftplib", r"smtplib"]),
        "file_ops":         (35, [
            r"open\(.*['\"]w['\"]", r"shutil\.rmtree",
            r"os\.remove\(", r"os\.unlink\(",
            r"glob\.glob\("]),
        "telegram_spam":    (50, [
            r"bot\.send_message.*for.*in",
            r"sendMessage.*while",
            r"mass.*send"]),
        "obfuscated":       (65, [
            r"base64\.b64decode",
            r"zlib\.decompress",
            r"marshal\.loads",
            r"\\x[0-9a-f]{2}(\\x[0-9a-f]{2}){10,}"]),
    }

    RISK_LABELS = {
        (0,  20):  ("✅ آمن",           "🟢"),
        (20, 45):  ("🔵 منخفض الخطر",   "🔵"),
        (45, 65):  ("🟡 مشبوه",         "🟡"),
        (65, 80):  ("🟠 خطر متوسط",     "🟠"),
        (80, 101): ("🔴 خطر عالٍ جداً", "🔴"),
    }

    @staticmethod
    def _read_file(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    @classmethod
    def scan(cls, filepath: str, ftype: str) -> dict:
        if ftype == "zip":
            return cls._scan_zip(filepath)
        return cls._scan_content(cls._read_file(filepath), ftype)

    @classmethod
    def _scan_zip(cls, zpath: str) -> dict:
        combined = ""
        try:
            with zipfile.ZipFile(zpath, "r") as z:
                for name in z.namelist():
                    ext = name.split(".")[-1].lower()
                    if ext in ALLOWED_EXT:
                        try:
                            combined += z.read(name).decode(
                                "utf-8", errors="ignore") + "\n"
                        except Exception:
                            pass
        except Exception:
            pass
        return cls._scan_content(combined, "zip")

    @classmethod
    def _scan_content(cls, content: str, ftype: str) -> dict:
        total_score = 0
        findings = []

        for cat, (base_score, pats) in cls.PATTERNS.items():
            hits = []
            for pat in pats:
                matches = re.findall(pat, content,
                                     re.IGNORECASE | re.MULTILINE)
                if matches:
                    hits.extend(matches[:3])
            if hits:
                total_score = min(100, total_score + base_score)
                findings.append({
                    "category": cat,
                    "score":    base_score,
                    "hits":     hits[:5],
                })

        risk_label = "✅ آمن"
        risk_icon  = "🟢"
        for (lo, hi), (lbl, icon) in cls.RISK_LABELS.items():
            if lo <= total_score < hi:
                risk_label = lbl
                risk_icon  = icon
                break

        return {
            "score":       total_score,
            "risk_label":  risk_label,
            "risk_icon":   risk_icon,
            "findings":    findings,
            "safe":        total_score < 45,
        }

    @classmethod
    def format_report(cls, result: dict, filename: str) -> str:
        r  = f"🔍 تقرير فحص الفيروسات\n"
        r += f"📁 الملف : {filename}\n"
        r += f"{'─'*30}\n"
        r += f"{result['risk_icon']} المخاطرة : {result['risk_label']}\n"
        r += f"📊 النقاط  : {result['score']}/100\n"
        if result["findings"]:
            r += f"\n⚠️ المخاوف المكتشفة:\n"
            for f in result["findings"]:
                r += f"  • {f['category']} (+{f['score']}pt)\n"
                for h in f["hits"][:2]:
                    snippet = str(h)[:40]
                    r += f"    └ `{snippet}`\n"
        else:
            r += "\n✅ لم يُكتشف أي نمط خطير\n"
        return r


# ════════════════════════════════════════════════════════════════
#  خدمة Docker / Subprocess
# ════════════════════════════════════════════════════════════════
class DockerService:
    def __init__(self):
        self._procs = {}
        self._client = None
        self.use_docker = False

        if _DOCKER_OK:
            try:
                self._client = _docker_lib.from_env()
                self._client.ping()
                self.use_docker = True
                LOG.info("✅ Docker متصل")
            except Exception as e:
                LOG.warning(f"Docker غير متاح: {e}")

    def run(self, filepath, ftype, port=None):
        if not port:
            port = random.randint(8000, 9000)
        if self.use_docker:
            r = self._docker_run(filepath, ftype, port)
            if r[0]:
                return r
        return self._sub_run(filepath, ftype, port)

    def _docker_run(self, filepath, ftype, port):
        filepath = os.path.abspath(filepath)
        fdir     = os.path.dirname(filepath)
        fname    = os.path.basename(filepath)
        img_map  = {
            "py":  ("python:3.9-slim", f"python /app/{fname}"),
            "php": ("php:7.4-cli",     f"php /app/{fname}"),
            "js":  ("node:14-slim",    f"node /app/{fname}"),
            "sh":  ("alpine:latest",   f"sh /app/{fname}"),
        }
        if ftype == "zip":
            ex = self._extract_zip(filepath)
            if not ex: return None, None
            return self._docker_run(
                os.path.join(ex["dir"], ex["main"]),
                ex["main"].split(".")[-1].lower(), port)
        if ftype not in img_map: return None, None
        img, cmd = img_map[ftype]
        try:
            cont = self._client.containers.run(
                image=img, command=cmd, detach=True,
                volumes={fdir: {"bind": "/app", "mode": "ro"}},
                working_dir="/app", network=DOCKER_NET,
                restart_policy={"Name": "unless-stopped"},
                mem_limit="256m", cpu_quota=50000, cpu_period=100000)
            return cont.id, port
        except Exception as e:
            LOG.error(f"Docker error: {e}"); return None, None

    def _sub_run(self, filepath, ftype, port):
        try:
            filepath = os.path.abspath(filepath)
            cmd_map  = {
                "py":  [sys.executable, filepath],
                "php": ["php",          filepath],
                "js":  ["node",         filepath],
                "sh":  ["sh",           filepath],
            }
            if ftype == "zip":
                ex = self._extract_zip(filepath)
                if not ex: return None, None
                
                # Try to install requirements if exists
                req_file = os.path.join(ex["dir"], "requirements.txt")
                if os.path.exists(req_file):
                    try:
                        subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], 
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception: pass

                main = os.path.join(ex["dir"], ex["main"])
                ext  = ex["main"].split(".")[-1].lower()
                return self._sub_run(main, ext, port)
            if ftype not in cmd_map:
                return None, None
            exe = cmd_map[ftype][0]
            if exe != sys.executable:
                if subprocess.run(["which", exe],
                                  capture_output=True).returncode != 0:
                    return None, None
            fake_id  = "".join(random.choices(
                string.ascii_lowercase + string.digits, k=64))
            log_path = os.path.join(LOGS_DIR, f"{fake_id[:12]}.log")
            logf     = open(log_path, "w", encoding="utf-8")
            proc     = subprocess.Popen(
                cmd_map[ftype], stdout=logf, stderr=logf,
                cwd=os.path.dirname(filepath) or ".", close_fds=True)
            self._procs[fake_id] = {
                "proc": proc, "logf": logf, "logpath": log_path}
            return fake_id, port
        except Exception as e:
            LOG.error(f"Subprocess error: {e}"); return None, None

    def stop(self, cid):
        if not cid: return False
        if cid in self._procs:
            try:
                info = self._procs.pop(cid)
                info["proc"].terminate()
                try: info["proc"].wait(timeout=5)
                except subprocess.TimeoutExpired: info["proc"].kill()
                try: info["logf"].close()
                except Exception: pass
                return True
            except Exception: return False
        if self.use_docker:
            try:
                c = self._client.containers.get(cid)
                c.stop(timeout=5); c.remove(force=True)
                return True
            except Exception: return False
        return False

    def status(self, cid):
        if not cid: return None
        if cid in self._procs:
            p = self._procs[cid]["proc"]
            return "running" if p.poll() is None else "exited"
        if self.use_docker:
            try:
                return self._client.containers.get(cid).status
            except Exception: pass
        return None

    def logs(self, cid, lines=50):
        if not cid: return ""
        if cid in self._procs:
            try:
                lp = self._procs[cid]["logpath"]
                with open(lp, "r", encoding="utf-8", errors="ignore") as f:
                    return "".join(f.readlines()[-lines:])
            except Exception: return ""
        if self.use_docker:
            try:
                return self._client.containers.get(cid).logs(
                    tail=lines).decode("utf-8", errors="ignore")
            except Exception: return ""
        return ""

    def _extract_zip(self, zpath):
        try:
            exdir = zpath.replace(".zip", "_extracted")
            os.makedirs(exdir, exist_ok=True)
            with zipfile.ZipFile(zpath, "r") as z:
                z.extractall(exdir)
            for ext in ["py","js","sh","php"]:
                for f in os.listdir(exdir):
                    if f.endswith(f".{ext}"):
                        return {"dir": exdir, "main": f}
            return None
        except Exception as e:
            LOG.error(f"ZIP error: {e}"); return None


# ════════════════════════════════════════════════════════════════
#  Singletons
# ════════════════════════════════════════════════════════════════
db    = DB()
dkr   = DockerService()
vscan = VirusScanner()

# ════════════════════════════════════════════════════════════════
#  مساعدات
# ════════════════════════════════════════════════════════════════
def _rand_name(orig):
    ext  = orig.rsplit(".", 1)[-1] if "." in orig else ""
    rnd  = "".join(random.choices(
        string.ascii_lowercase + string.digits, k=12))
    return f"{rnd}.{ext}" if ext else rnd


async def _check_sub(bot, uid, channels):
    not_sub = []
    for ch in channels:
        try:
            m = await bot.get_chat_member(chat_id=ch[0], user_id=uid)
            if m.status in ["left", "kicked"]:
                not_sub.append(ch)
        except Exception:
            not_sub.append(ch)
    return len(not_sub) == 0, not_sub


def _owner_only(fn):
    @wraps(fn)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE,
                      *a, **kw):
        uid = upd.effective_user.id
        if uid != OWNER_ID:
            msg = upd.message or (
                upd.callback_query.message
                if upd.callback_query else None)
            if msg:
                await msg.reply_text("⛔ هذه الخاصية للمالك فقط!")
            return
        return await fn(upd, ctx, *a, **kw)
    return wrapper


def _admin_only(fn):
    @wraps(fn)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE,
                      *a, **kw):
        uid = upd.effective_user.id
        if not db.is_admin(uid):
            msg = upd.message or (
                upd.callback_query.message
                if upd.callback_query else None)
            if msg:
                await msg.reply_text("⛔ هذه الخاصية للمشرفين فقط!")
            return
        return await fn(upd, ctx, *a, **kw)
    return wrapper


# ════════════════════════════════════════════════════════════════
#  أزرار رئيسية
# ════════════════════════════════════════════════════════════════
def _main_kb(uid):
    kb = [
        [InlineKeyboardButton("📤 رفع ملف",   callback_data="act_upload"),
         InlineKeyboardButton("📋 ملفاتي",    callback_data="act_myfiles")],
        [InlineKeyboardButton("▶️ تشغيل",     callback_data="act_run"),
         InlineKeyboardButton("⏹️ إيقاف",     callback_data="act_stop")],
        [InlineKeyboardButton("📊 الحالة",    callback_data="act_status"),
         InlineKeyboardButton("📜 اللوجز",    callback_data="act_logs")],
        [InlineKeyboardButton("ℹ️ مساعدة",   callback_data="act_help"),
         InlineKeyboardButton("👤 حسابي",     callback_data="act_profile")],
    ]
    if db.is_admin(uid):
        kb.append([InlineKeyboardButton(
            "👑 لوحة التحكم", callback_data="adm_panel")])
    return InlineKeyboardMarkup(kb)


def _admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 إحصاءات",       callback_data="adm_stats"),
         InlineKeyboardButton("👥 المستخدمون",    callback_data="adm_users")],
        [InlineKeyboardButton("⏳ المعلقة",        callback_data="adm_pending"),
         InlineKeyboardButton("📁 كل الملفات",    callback_data="adm_allfiles")],
        [InlineKeyboardButton("📂 ملفات مستخدم",  callback_data="adm_userfiles"),
         InlineKeyboardButton("📢 القنوات",        callback_data="adm_channels")],
        [InlineKeyboardButton("▶️ تشغيل الكل",    callback_data="adm_runall"),
         InlineKeyboardButton("⏹️ إيقاف الكل",   callback_data="adm_stopall")],
        [InlineKeyboardButton("🔧 إعدادات",        callback_data="adm_settings"),
         InlineKeyboardButton("📨 إرسال للجميع",  callback_data="adm_broadcast")],
        [InlineKeyboardButton("🧹 تنظيف الملفات", callback_data="adm_cleanup"),
         InlineKeyboardButton("👑 المشرفون",       callback_data="adm_admins")],
        [InlineKeyboardButton("🔙 رجوع",          callback_data="act_back")],
    ])


def _settings_kb():
    mf = db.get_max_files()
    sm = db.get_setting("stealth_mode","1")
    vs = db.get_setting("virus_scan","1")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📁 حد الملفات : {mf}  ✏️",
                              callback_data="set_maxfiles")],
        [InlineKeyboardButton(
             f"🕵️ الوضع الخفي : {'✅' if sm=='1' else '❌'}",
             callback_data="set_stealth")],
        [InlineKeyboardButton(
             f"🦠 فحص الفيروسات : {'✅' if vs=='1' else '❌'}",
             callback_data="set_virusscan")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")],
    ])


# ════════════════════════════════════════════════════════════════
#  /start
# ════════════════════════════════════════════════════════════════
async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = upd.effective_user
    db.add_user(u.id, u.username, u.first_name, u.last_name)

    chs = db.get_channels()
    if chs:
        ok, not_sub = await _check_sub(ctx.bot, u.id, chs)
        if not ok:
            txt  = "⚠️ يجب الاشتراك في هذه القنوات أولاً:\n\n"
            keys = []
            for ch in not_sub:
                txt += f"📢 {ch[2]}\n"
                keys.append([InlineKeyboardButton(
                    f"اشترك ➜ {ch[2]}",
                    url=f"https://t.me/{ch[1].lstrip('@')}")])
            keys.append([InlineKeyboardButton(
                "✅ تحقق من الاشتراك", callback_data="check_sub")])
            await upd.message.reply_text(
                txt, reply_markup=InlineKeyboardMarkup(keys))
            return

    mode = "🐋 Docker" if dkr.use_docker else "⚙️ Subprocess"
    txt = (
        f"🌟 مرحباً <b>{u.first_name}</b>!\n\n"
        f"👑 المالك  : @{OWNER_USER}\n"
        f"🔥 الفريق : {TEAM_NAME}\n"
        f"🤖 البوت  : {BOT_USERNAME}\n"
        f"🖥️ الوضع  : {mode}\n\n"
        f"💡 اختر من القائمة أدناه 👇"
    )
    await upd.message.reply_html(txt, reply_markup=_main_kb(u.id))


# ════════════════════════════════════════════════════════════════
#  استقبال الملفات  ✅ إصلاح Bug #3: send_html → send_message
# ════════════════════════════════════════════════════════════════
async def handle_file(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u   = upd.effective_user
    uid = u.id
    usr = db.get_user(uid)
    if not usr:
        db.add_user(uid, u.username, u.first_name, u.last_name)

    usr = db.get_user(uid)
    if usr and usr[5]:
        await upd.message.reply_text("⛔ أنت محظور من استخدام البوت!")
        return

    max_f = db.get_max_files()
    if len(db.get_user_files(uid)) >= max_f:
        await upd.message.reply_text(
            f"❌ لا يمكنك رفع أكثر من {max_f} ملفات\n"
            f"احذف ملفاً قديماً لرفع ملف جديد.")
        return

    doc = upd.message.document
    if not doc:
        await upd.message.reply_text("❌ يرجى إرسال ملف")
        return

    if doc.file_size > MAX_FILE_MB * 1024 * 1024:
        await upd.message.reply_text(
            f"❌ حجم الملف كبير جداً - الحد {MAX_FILE_MB} MB")
        return

    orig_name = doc.file_name or "file"
    ext       = orig_name.rsplit(".", 1)[-1].lower() if "." in orig_name else ""
    if ext not in ALLOWED_EXT:
        await upd.message.reply_text(
            f"❌ الامتداد غير مدعوم\n"
            f"المدعوم: {' / '.join(ALLOWED_EXT)}")
        return

    msg = await upd.message.reply_text("📥 جارٍ تحميل الملف ...")
    tg_file  = await ctx.bot.get_file(doc.file_id)
    new_name = _rand_name(orig_name)
    filepath = os.path.join(UPLOAD_DIR, new_name)
    await tg_file.download_to_drive(filepath)

    virus_result = None
    if db.get_setting("virus_scan", "1") == "1":
        try:
            await msg.edit_text("🦠 جارٍ فحص الملف ...")
        except Exception:
            pass
        virus_result = VirusScanner.scan(filepath, ext)

    fid = db.add_file(uid, new_name, orig_name, filepath,
                      doc.file_size, ext)

    stealth = db.get_setting("stealth_mode", "1") == "1"
    if uid != OWNER_ID:
        usr_info = db.get_user(uid)
        uname_txt = f"@{usr_info[1]}" if usr_info and usr_info[1] else f"ID: {uid}"

        virus_txt = ""
        if virus_result:
            virus_txt = (
                f"\n{'─'*25}\n"
                f"{VirusScanner.format_report(virus_result, orig_name)}"
            )

        if stealth:
            owner_txt = (
                f"📥 ملف جديد <b>مجهول</b>\n"
                f"{'─'*25}\n"
                f"📁 الملف : {orig_name}\n"
                f"📦 الحجم : {doc.file_size/1024:.1f} KB\n"
                f"🔤 النوع : {ext.upper()}\n"
                f"🆔 رقم   : {fid}"
                f"{virus_txt}"
            )
        else:
            owner_txt = (
                f"📥 ملف جديد\n"
                f"{'─'*25}\n"
                f"👤 المستخدم : {uname_txt}\n"
                f"📁 الملف    : {orig_name}\n"
                f"📦 الحجم    : {doc.file_size/1024:.1f} KB\n"
                f"🔤 النوع    : {ext.upper()}\n"
                f"🆔 رقم      : {fid}"
                f"{virus_txt}"
            )

        approve_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ قبول",  callback_data=f"approve_{fid}"),
            InlineKeyboardButton("❌ رفض",   callback_data=f"reject_{fid}"),
        ]])

        # ✅ إصلاح: send_message مباشرة بدل send_html غير الموجودة
        try:
            await ctx.bot.send_message(
                OWNER_ID,
                owner_txt,
                reply_markup=approve_kb,
                parse_mode=ParseMode.HTML)
        except Exception as e:
            LOG.error(f"Failed to notify owner: {e}")

    if virus_result and not virus_result["safe"]:
        status_txt = (
            f"⚠️ تم رفع الملف لكن تم اكتشاف أنماط مشبوهة!\n"
            f"{virus_result['risk_icon']} {virus_result['risk_label']}\n"
            f"⏳ الحالة: في انتظار موافقة المالك"
        )
    else:
        status_txt = "⏳ الحالة: في انتظار موافقة المالك"

    try:
        await msg.edit_text(
            f"✅ تم رفع الملف بنجاح\n"
            f"📁 الاسم : {orig_name}\n"
            f"📦 الحجم : {doc.file_size/1024:.1f} KB\n"
            f"🔤 النوع : {ext.upper()}\n"
            f"{status_txt}"
        )
    except Exception:
        await upd.message.reply_text(
            f"✅ تم رفع الملف بنجاح\n"
            f"📁 الاسم : {orig_name}\n"
            f"📦 الحجم : {doc.file_size/1024:.1f} KB\n"
            f"🔤 النوع : {ext.upper()}\n"
            f"{status_txt}"
        )


# ════════════════════════════════════════════════════════════════
#  معالج الأزرار
# ════════════════════════════════════════════════════════════════
async def button_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = upd.callback_query
    await q.answer()
    d   = q.data
    uid = q.from_user.id
    msg = q.message

    if d == "check_sub":
        chs = db.get_channels()
        ok, _ = await _check_sub(ctx.bot, uid, chs) if chs else (True, [])
        if ok:
            await q.edit_message_text("✅ تم التحقق! اضغط /start")
        else:
            await q.answer("❌ لم تشترك في جميع القنوات بعد", show_alert=True)
        return

    if d == "act_back":
        u = q.from_user
        mode = "🐋 Docker" if dkr.use_docker else "⚙️ Subprocess"
        txt = (
            f"🌟 مرحباً <b>{u.first_name}</b>!\n\n"
            f"👑 المالك  : @{OWNER_USER}\n"
            f"🔥 الفريق : {TEAM_NAME}\n"
            f"🖥️ الوضع  : {mode}\n\n"
            f"💡 اختر من القائمة أدناه 👇"
        )
        await q.edit_message_text(
            txt, reply_markup=_main_kb(uid),
            parse_mode=ParseMode.HTML)
        return

    if d == "act_upload":
        max_f = db.get_max_files()
        await q.edit_message_text(
            f"📤 أرسل الملف الذي تريد رفعه\n"
            f"الامتدادات المدعومة : {' / '.join(ALLOWED_EXT)}\n"
            f"الحد الأقصى : {max_f} ملفات",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
            ]]))
        return

    if d == "act_myfiles":
        await _show_myfiles(q, uid)
        return

    if d == "act_help":
        await q.edit_message_text(
            "🆘 <b>دليل الاستخدام</b>\n\n"
            "1️⃣ اضغط <b>رفع ملف</b> وأرسل ملفك\n"
            "2️⃣ انتظر موافقة المالك\n"
            "3️⃣ اضغط <b>تشغيل</b> لتشغيل الملف\n"
            "4️⃣ اضغط <b>إيقاف</b> لإيقافه\n"
            "5️⃣ اضغط <b>اللوجز</b> لعرض المخرجات\n\n"
            f"📦 الامتدادات: {' / '.join(ALLOWED_EXT)}\n"
            f"👑 @{OWNER_USER} | {TEAM_NAME}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
            ]]))
        return

    if d == "act_profile":
        usr   = db.get_user(uid)
        files = db.get_user_files(uid)
        if usr:
            running = sum(1 for f in files if f[11])
            txt = (
                f"👤 <b>معلومات حسابك</b>\n\n"
                f"🆔 : <code>{usr[0]}</code>\n"
                f"👤 : {usr[2]} {usr[3] or ''}\n"
                f"📝 : @{usr[1] or 'لا يوجد'}\n"
                f"📅 : {str(usr[4])[:19]}\n"
                f"📁 الملفات    : {len(files)}\n"
                f"▶️ تعمل الآن : {running}\n"
                f"🚫 محظور      : {'نعم ❌' if usr[5] else 'لا ✅'}\n"
                f"👑 مشرف       : {'نعم ✅' if usr[6] or uid==OWNER_ID else 'لا ❌'}"
            )
        else:
            txt = "❌ لم يتم العثور على معلوماتك"
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
            ]]))
        return

    if d == "act_run":
        files = db.get_user_files(uid)
        ready = [f for f in files if f[8] == "approved" and not f[11]]
        if not ready:
            await q.edit_message_text(
                "❌ لا يوجد ملفات جاهزة للتشغيل\n"
                "(يجب أن تكون معتمدة وغير مُشغَّلة)",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
                ]]))
            return
        keys = [[InlineKeyboardButton(
            f"🚀 {f[3]}", callback_data=f"run_{f[0]}"
        )] for f in ready]
        keys.append([InlineKeyboardButton("🔙 رجوع", callback_data="act_back")])
        await q.edit_message_text(
            "▶️ اختر الملف للتشغيل:",
            reply_markup=InlineKeyboardMarkup(keys))
        return

    if d == "act_stop":
        files   = db.get_user_files(uid)
        running = [f for f in files if f[11]]
        if not running:
            await q.edit_message_text(
                "❌ لا يوجد ملفات تعمل حالياً",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
                ]]))
            return
        keys = [[InlineKeyboardButton(
            f"⏹️ {f[3]}", callback_data=f"stop_{f[0]}"
        )] for f in running]
        keys.append([InlineKeyboardButton("🔙 رجوع", callback_data="act_back")])
        await q.edit_message_text(
            "⏹️ اختر الملف للإيقاف:",
            reply_markup=InlineKeyboardMarkup(keys))
        return

    if d == "act_status":
        files = db.get_user_files(uid)
        if not files:
            await q.edit_message_text(
                "📭 لا يوجد لديك أي ملفات",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
                ]]))
            return
        txt = "📊 <b>حالة ملفاتك</b>\n\n"
        for f in files:
            st_icon  = {"pending":"⏳","approved":"✅","rejected":"❌"}.get(f[8],"❓")
            run_icon = "🟢 يعمل" if f[11] else "🔴 متوقف"
            txt += (f"{st_icon} <b>{f[3]}</b>\n"
                    f"   التشغيل: {run_icon} | النوع: {f[6].upper()}\n\n")
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
            ]]))
        return

    if d == "act_logs":
        files   = db.get_user_files(uid)
        running = [f for f in files if f[11]]
        if not running:
            await q.edit_message_text(
                "❌ لا يوجد ملفات تعمل حالياً",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
                ]]))
            return
        keys = [[InlineKeyboardButton(
            f"📜 {f[3]}", callback_data=f"logs_{f[0]}"
        )] for f in running]
        keys.append([InlineKeyboardButton("🔙 رجوع", callback_data="act_back")])
        await q.edit_message_text(
            "📜 اختر الملف لعرض اللوجز:",
            reply_markup=InlineKeyboardMarkup(keys))
        return

    if d.startswith("run_"):
        fid = int(d.split("_",1)[1])
        f   = db.get_file(fid)
        if not f:
            await q.answer("❌ الملف غير موجود أو محذوف", show_alert=True); return
        if f[1] != uid:
            await q.answer("❌ هذا الملف ليس لك", show_alert=True); return
        if f[8] != "approved":
            await q.answer("❌ الملف غير معتمد بعد", show_alert=True); return
        if f[11]:
            await q.answer("❌ الملف يعمل بالفعل", show_alert=True); return
        if not os.path.exists(f[4]):
            db.delete_file(fid)
            await q.answer("❌ الملف غير موجود على السيرفر وتم حذفه", show_alert=True); return
        await q.edit_message_text("🚀 جارٍ تشغيل الملف ...")
        cid, port = dkr.run(f[4], f[6])
        if cid:
            db.update_status(fid, "approved", cid, port)
            mode = "🐋 Docker" if dkr.use_docker else "⚙️ Subprocess"
            await q.edit_message_text(
                f"✅ تم تشغيل الملف بنجاح\n"
                f"📁 الملف    : {f[3]}\n"
                f"🆔 الحاوية : <code>{cid[:12]}</code>...\n"
                f"🔌 المنفذ  : {port}\n"
                f"🖥️ الوضع   : {mode}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏹️ إيقاف", callback_data=f"stop_{fid}"),
                     InlineKeyboardButton("📜 لوجز",  callback_data=f"logs_{fid}")],
                    [InlineKeyboardButton("🔙 رجوع",  callback_data="act_back")],
                ]))
        else:
            await q.edit_message_text(
                "❌ فشل في تشغيل الملف\n"
                "تأكد أن python/node/php/sh مثبتة على السيرفر",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
                ]]))
        return

    if d.startswith("stop_"):
        fid = int(d.split("_",1)[1])
        f   = db.get_file(fid)
        if not f:
            await q.answer("❌ الملف غير موجود", show_alert=True); return
        if f[1] != uid and uid != OWNER_ID:
            await q.answer("❌ هذا الملف ليس لك", show_alert=True); return
        if not f[11]:
            await q.answer("❌ الملف لا يعمل", show_alert=True); return
        await q.edit_message_text("⏹️ جارٍ إيقاف الملف ...")
        ok = dkr.stop(f[9])
        db.stop_file(fid)
        if ok:
            await q.edit_message_text(
                f"✅ تم إيقاف الملف <b>{f[3]}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
                ]]))
        else:
            await q.edit_message_text(
                "⚠️ تم تحديث الحالة (قد يكون الملف توقف بالفعل)",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
                ]]))
        return

    if d.startswith("logs_"):
        fid = int(d.split("_",1)[1])
        f   = db.get_file(fid)
        if not f:
            await q.answer("❌ الملف غير موجود", show_alert=True); return
        if f[1] != uid and uid != OWNER_ID:
            await q.answer("❌ هذا الملف ليس لك", show_alert=True); return
        log = dkr.logs(f[9])
        if log:
            log = log.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            log = log[-3500:]
        else:
            log = "لا يوجد لوجز متاح"
        txt = f"📜 <b>لوجز {f[3]}</b>\n\n"
        txt += f"<code>{log}</code>"
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 تحديث", callback_data=f"logs_{fid}"),
                InlineKeyboardButton("🔙 رجوع",  callback_data="act_back"),
            ]]))
        return

    if d.startswith("del_"):
        fid = int(d.split("_",1)[1])
        f   = db.get_file(fid)
        if not f:
            await q.answer("❌ الملف غير موجود", show_alert=True); return
        if f[1] != uid and uid != OWNER_ID:
            await q.answer("❌ هذا الملف ليس لك", show_alert=True); return
        await q.edit_message_text(
            f"⚠️ هل أنت متأكد من حذف <b>{f[3]}</b>؟",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"delconfirm_{fid}"),
                 InlineKeyboardButton("❌ إلغاء",      callback_data="act_myfiles")],
            ]))
        return

    if d.startswith("delconfirm_"):
        fid = int(d.split("_",1)[1])
        f   = db.get_file(fid)
        if not f:
            await q.answer("❌ الملف غير موجود", show_alert=True); return
        if f[1] != uid and uid != OWNER_ID:
            await q.answer("❌ هذا الملف ليس لك", show_alert=True); return
        if f[11] and f[9]:
            dkr.stop(f[9])
        try:
            if os.path.exists(f[4]):
                os.remove(f[4])
        except Exception:
            pass
        fname = f[3]
        db.delete_file(fid)
        await q.edit_message_text(
            f"✅ تم حذف الملف <b>{fname}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="act_back")
            ]]))
        return

    if d.startswith("approve_"):
        if not db.is_admin(uid):
            await q.answer("⛔", show_alert=True); return
        fid = int(d.split("_",1)[1])
        f   = db.get_file(fid)
        if f:
            db.update_status(fid, "approved")
            await q.edit_message_text(
                f"✅ تمت الموافقة على الملف <b>{f[3]}</b>",
                parse_mode=ParseMode.HTML)
            try:
                await ctx.bot.send_message(
                    f[1],
                    f"✅ تمت الموافقة على ملفك <b>{f[3]}</b>\n"
                    f"يمكنك الآن تشغيله من قائمة <b>ملفاتي</b>",
                    parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    if d.startswith("reject_"):
        if not db.is_admin(uid):
            await q.answer("⛔", show_alert=True); return
        fid = int(d.split("_",1)[1])
        f   = db.get_file(fid)
        if f:
            db.update_status(fid, "rejected")
            await q.edit_message_text(
                f"❌ تم رفض الملف <b>{f[3]}</b>",
                parse_mode=ParseMode.HTML)
            try:
                await ctx.bot.send_message(
                    f[1], f"❌ تم رفض ملفك <b>{f[3]}</b>",
                    parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    if d == "adm_panel":
        if not db.is_admin(uid):
            await q.answer("⛔", show_alert=True); return
        s = db.stats()
        txt = (
            f"👑 <b>لوحة التحكم</b>\n\n"
            f"👥 المستخدمون : {s['users']}\n"
            f"📁 الملفات    : {s['files']}\n"
            f"▶️ تعمل الآن : {s['running']}\n"
            f"⏳ معلقة     : {s['pending']}\n\n"
            f"🖥️ الوضع: {'🐋 Docker' if dkr.use_docker else '⚙️ Subprocess'}\n"
            f"📁 حد الملفات : {db.get_max_files()}\n"
            f"🕵️ الوضع الخفي: {'✅' if db.get_setting('stealth_mode')=='1' else '❌'}\n"
            f"🦠 فحص الفيروسات: {'✅' if db.get_setting('virus_scan')=='1' else '❌'}"
        )
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=_admin_kb())
        return

    if d == "adm_stats":
        if not db.is_admin(uid): return
        s = db.stats()
        txt = (
            f"📊 <b>إحصاءات البوت</b>\n\n"
            f"👥 المستخدمون  : {s['users']}\n"
            f"🚫 المحظورون  : {s['banned']}\n"
            f"👑 المشرفون   : {s['admins']}\n\n"
            f"📁 إجمالي الملفات : {s['files']}\n"
            f"▶️ تعمل الآن      : {s['running']}\n"
            f"⏳ معلقة          : {s['pending']}\n"
            f"✅ معتمدة         : {s['approved']}\n"
            f"❌ مرفوضة         : {s['rejected']}\n\n"
            f"🕐 الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"👑 @{OWNER_USER} | v3.0"
        )
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
            ]]))
        return

    if d == "adm_users":
        if not db.is_admin(uid): return
        users = db.get_all_users()
        txt = f"👥 <b>المستخدمون ({len(users)})</b>\n\n"
        for u in users[:30]:
            icon   = "🚫" if u[4] else ("👑" if u[5] else "👤")
            name   = u[2] or "بدون اسم"
            handle = f"@{u[1]}" if u[1] else f"ID:{u[0]}"
            txt   += f"{icon} {name} | {handle}\n"
        if len(users) > 30:
            txt += f"\n... و {len(users)-30} آخرون"
        keys = []
        if uid == OWNER_ID:
            keys = [[
                InlineKeyboardButton("➕ مشرف جديد",  callback_data="adm_addadmin"),
                InlineKeyboardButton("➖ حذف مشرف",   callback_data="adm_removeadmin"),
            ],[
                InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban"),
                InlineKeyboardButton("✅ فك الحظر",    callback_data="adm_unban"),
            ]]
        keys.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")])
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keys))
        return

    if d == "adm_admins":
        if not db.is_admin(uid): return
        admins = db.get_admins()
        txt = f"👑 <b>المشرفون ({len(admins)})</b>\n\n"
        for a in admins:
            u2     = db.get_user(a[0])
            name   = (u2[2] if u2 else None) or "غير معروف"
            handle = (f"@{u2[1]}" if u2 and u2[1] else f"ID:{a[0]}")
            is_owner = "🔱 مالك" if a[0] == OWNER_ID else "👑 مشرف"
            txt += f"{is_owner} {name} | {handle}\n"
        keys = []
        if uid == OWNER_ID:
            keys = [[
                InlineKeyboardButton("➕ إضافة مشرف", callback_data="adm_addadmin"),
                InlineKeyboardButton("➖ حذف مشرف",   callback_data="adm_removeadmin"),
            ]]
        keys.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")])
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keys))
        return

    if d == "adm_pending":
        if not db.is_admin(uid): return
        files = db.get_pending()
        if not files:
            await q.edit_message_text(
                "✅ لا يوجد ملفات معلقة",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
                ]]))
            return
        txt  = f"⏳ <b>الملفات المعلقة ({len(files)})</b>\n\n"
        keys = []
        for f in files:
            u2      = db.get_user(f[1])
            uhandle = f"@{u2[1]}" if u2 and u2[1] else f"ID:{f[1]}"
            stealth = db.get_setting("stealth_mode","1") == "1"
            if stealth:
                txt += f"📁 {f[3]} | {f[6].upper()} | {f[5]//1024}KB\n"
            else:
                txt += f"📁 {f[3]} | {uhandle} | {f[6].upper()}\n"
            keys.append([
                InlineKeyboardButton(f"✅ قبول {f[3][:15]}", callback_data=f"approve_{f[0]}"),
                InlineKeyboardButton(f"❌ رفض {f[3][:15]}",  callback_data=f"reject_{f[0]}"),
            ])
        keys.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")])
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keys))
        return

    if d == "adm_allfiles":
        if not db.is_admin(uid): return
        files = db.get_all_files()
        if not files:
            await q.edit_message_text(
                "📭 لا يوجد ملفات",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
                ]]))
            return
        txt = f"📁 <b>كل الملفات ({len(files)})</b>\n\n"
        for f in files[:25]:
            st = {"pending":"⏳","approved":"✅","rejected":"❌"}.get(f[8],"❓")
            rn = "🟢" if f[11] else "🔴"
            txt += f"{st}{rn} {f[3]} | {f[6].upper()} | ID:{f[1]}\n"
        if len(files) > 25:
            txt += f"\n... و {len(files)-25} ملف آخر"
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
            ]]))
        return

    if d == "adm_userfiles":
        if not db.is_admin(uid): return
        ctx.user_data["waiting_userfiles"] = True
        await q.edit_message_text(
            "👤 أرسل معرّف المستخدم (숫字 فقط) لعرض ملفاته:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
            ]]))
        return

    if d == "adm_runall":
        if uid != OWNER_ID:
            await q.answer("⛔ للمالك فقط", show_alert=True); return
        files = db.get_all_files()
        ready = [f for f in files if f[8] == "approved" and not f[11]]
        if not ready:
            await q.answer("❌ لا يوجد ملفات جاهزة", show_alert=True); return
        await q.edit_message_text(f"▶️ جارٍ تشغيل {len(ready)} ملف...")
        ok_n, fail_n = 0, 0
        for f in ready:
            if not os.path.exists(f[4]):
                db.delete_file(f[0]); fail_n += 1; continue
            cid, port = dkr.run(f[4], f[6])
            if cid:
                db.update_status(f[0], "approved", cid, port); ok_n += 1
            else:
                fail_n += 1
        await q.edit_message_text(
            f"✅ تم تشغيل الكل\nنجح : {ok_n} | فشل : {fail_n}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
            ]]))
        return

    if d == "adm_stopall":
        if uid != OWNER_ID:
            await q.answer("⛔ للمالك فقط", show_alert=True); return
        running = db.get_running_files()
        if not running:
            await q.answer("❌ لا يوجد ملفات تعمل", show_alert=True); return
        await q.edit_message_text(f"⏹️ جارٍ إيقاف {len(running)} ملف...")
        ok_n = 0
        for f in running:
            dkr.stop(f[9]); db.stop_file(f[0]); ok_n += 1
        await q.edit_message_text(
            f"✅ تم إيقاف الكل\nأُوقف : {ok_n}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
            ]]))
        return

    if d == "adm_cleanup":
        if not db.is_admin(uid): return
        deleted = db.delete_missing_files()
        await q.edit_message_text(
            f"🧹 تم تنظيف قاعدة البيانات\n"
            f"حُذف {len(deleted)} سجل لملفات غير موجودة",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
            ]]))
        return

    if d == "adm_channels":
        if not db.is_admin(uid): return
        chs  = db.get_channels()
        txt  = "📢 <b>القنوات الإجبارية</b>\n\n"
        keys = []
        if chs:
            for ch in chs:
                txt += f"📌 {ch[2]} | @{ch[1]}\n"
                keys.append([InlineKeyboardButton(
                    f"❌ حذف {ch[2]}", callback_data=f"delch_{ch[0]}")])
        else:
            txt += "لا توجد قنوات إجبارية حالياً\n"
        keys.append([InlineKeyboardButton("➕ إضافة قناة", callback_data="addch")])
        keys.append([InlineKeyboardButton("🔙 رجوع",       callback_data="adm_panel")])
        await q.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keys))
        return

    if d.startswith("delch_"):
        if not db.is_admin(uid): return
        ch_id = int(d.split("_",1)[1])
        db.del_channel(ch_id)
        await q.answer("✅ تم حذف القناة")
        await q.edit_message_text(
            "✅ تم حذف القناة بنجاح",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_channels")
            ]]))
        return

    if d == "addch":
        if not db.is_admin(uid): return
        ctx.user_data["waiting_channel"] = True
        await q.edit_message_text(
            "📢 أرسل معرّف القناة مثال: @MyChannel",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_channels")
            ]]))
        return

    if d == "adm_settings":
        if uid != OWNER_ID:
            await q.answer("⛔ للمالك فقط", show_alert=True); return
        await q.edit_message_text(
            "🔧 <b>إعدادات البوت</b>\n\nاضغط على الإعداد لتغييره:",
            parse_mode=ParseMode.HTML,
            reply_markup=_settings_kb())
        return

    if d == "set_maxfiles":
        if uid != OWNER_ID: return
        ctx.user_data["waiting_maxfiles"] = True
        mf = db.get_max_files()
        await q.edit_message_text(
            f"📁 حد الملفات الحالي: <b>{mf}</b>\n\nأرسل العدد الجديد (1-50):",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_settings")
            ]]))
        return

    if d == "set_stealth":
        if uid != OWNER_ID: return
        cur = db.get_setting("stealth_mode","1")
        db.set_setting("stealth_mode","0" if cur=="1" else "1")
        await q.answer(f"🕵️ الوضع الخفي: {'✅' if cur=='0' else '❌'}")
        await q.edit_message_text(
            "🔧 <b>إعدادات البوت</b>\n\nاضغط على الإعداد لتغييره:",
            parse_mode=ParseMode.HTML,
            reply_markup=_settings_kb())
        return

    if d == "set_virusscan":
        if uid != OWNER_ID: return
        cur = db.get_setting("virus_scan","1")
        db.set_setting("virus_scan","0" if cur=="1" else "1")
        await q.answer(f"🦠 فحص الفيروسات: {'✅' if cur=='0' else '❌'}")
        await q.edit_message_text(
            "🔧 <b>إعدادات البوت</b>\n\nاضغط على الإعداد لتغييره:",
            parse_mode=ParseMode.HTML,
            reply_markup=_settings_kb())
        return

    if d == "adm_broadcast":
        if not db.is_admin(uid): return
        ctx.user_data["waiting_broadcast"] = True
        await q.edit_message_text(
            "📨 أرسل الرسالة التي تريد إرسالها لجميع المستخدمين:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")
            ]]))
        return

    if d == "adm_addadmin":
        if uid != OWNER_ID:
            await q.answer("⛔ للمالك فقط", show_alert=True); return
        ctx.user_data["waiting_addadmin"] = True
        await q.edit_message_text(
            "👑 أرسل معرّف المستخدم لتعيينه مشرفاً:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_users")
            ]]))
        return

    if d == "adm_removeadmin":
        if uid != OWNER_ID:
            await q.answer("⛔ للمالك فقط", show_alert=True); return
        ctx.user_data["waiting_removeadmin"] = True
        await q.edit_message_text(
            "➖ أرسل معرّف المشرف لإزالته:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_users")
            ]]))
        return

    if d == "adm_ban":
        if not db.is_admin(uid): return
        ctx.user_data["waiting_ban"] = True
        await q.edit_message_text(
            "🚫 أرسل معرّف المستخدم لحظره:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_users")
            ]]))
        return

    if d == "adm_unban":
        if not db.is_admin(uid): return
        ctx.user_data["waiting_unban"] = True
        await q.edit_message_text(
            "✅ أرسل معرّف المستخدم لفك حظره:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data="adm_users")
            ]]))
        return


# ── عرض ملفاتي ────────────────────────────────────────────────
async def _show_myfiles(q_or_msg, uid, edit=True):
    files = db.get_user_files(uid)
    if not files:
        txt = "📭 لا يوجد لديك أي ملفات"
        kb  = InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 رفع ملف", callback_data="act_upload"),
            InlineKeyboardButton("🔙 رجوع",    callback_data="act_back"),
        ]])
        if edit: await q_or_msg.edit_message_text(txt, reply_markup=kb)
        else:    await q_or_msg.reply_text(txt, reply_markup=kb)
        return

    txt  = "📋 <b>ملفاتك</b>\n\n"
    keys = []
    st_e = {"pending":"⏳","approved":"✅","rejected":"❌"}
    for f in files:
        st  = st_e.get(f[8],"❓")
        rn  = "🟢" if f[11] else "🔴"
        txt += (f"{st} {rn} <b>{f[3]}</b>\n"
                f"   {f[6].upper()} | {f[5]//1024}KB\n\n")
        row = []
        if f[8] == "approved" and not f[11]:
            row.append(InlineKeyboardButton("▶️ تشغيل", callback_data=f"run_{f[0]}"))
        if f[11]:
            row.append(InlineKeyboardButton("⏹️ إيقاف", callback_data=f"stop_{f[0]}"))
            row.append(InlineKeyboardButton("📜 لوجز",  callback_data=f"logs_{f[0]}"))
        row.append(InlineKeyboardButton("🗑️ حذف", callback_data=f"del_{f[0]}"))
        if row: keys.append(row)

    keys.append([InlineKeyboardButton("🔙 رجوع", callback_data="act_back")])
    if edit:
        await q_or_msg.edit_message_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keys))
    else:
        await q_or_msg.reply_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keys))


# ════════════════════════════════════════════════════════════════
#  معالج الرسائل النصية
# ════════════════════════════════════════════════════════════════
async def text_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    txt = upd.message.text.strip()

    if ctx.user_data.get("waiting_maxfiles"):
        ctx.user_data.pop("waiting_maxfiles")
        if uid != OWNER_ID: return
        try:
            n = int(txt)
            if not 1 <= n <= 50: raise ValueError
            db.set_max_files(n)
            await upd.message.reply_text(
                f"✅ تم تحديث حد الملفات إلى <b>{n}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=_settings_kb())
        except ValueError:
            await upd.message.reply_text("❌ أدخل رقماً صحيحاً بين 1 و 50")
        return

    if ctx.user_data.get("waiting_addadmin"):
        ctx.user_data.pop("waiting_addadmin")
        if uid != OWNER_ID: return
        try:
            tid = int(txt)
        except ValueError:
            await upd.message.reply_text("❌ معرّف غير صحيح"); return
        u2 = db.get_user(tid)
        if not u2:
            await upd.message.reply_text("❌ المستخدم غير موجود في قاعدة البيانات"); return
        db.add_admin(tid, uid)
        await upd.message.reply_text(
            f"✅ تم تعيين <code>{tid}</code> مشرفاً",
            parse_mode=ParseMode.HTML)
        try:
            await ctx.bot.send_message(tid, f"👑 تم تعيينك مشرفاً في {TEAM_NAME}!")
        except Exception: pass
        return

    if ctx.user_data.get("waiting_removeadmin"):
        ctx.user_data.pop("waiting_removeadmin")
        if uid != OWNER_ID: return
        try:
            tid = int(txt)
        except ValueError:
            await upd.message.reply_text("❌ معرّف غير صحيح"); return
        if tid == OWNER_ID:
            await upd.message.reply_text("⛔ لا يمكن إزالة المالك"); return
        db.remove_admin(tid)
        await upd.message.reply_text(
            f"✅ تم إزالة <code>{tid}</code> من المشرفين",
            parse_mode=ParseMode.HTML)
        try:
            await ctx.bot.send_message(tid, "❌ تم إزالتك من قائمة المشرفين")
        except Exception: pass
        return

    if ctx.user_data.get("waiting_ban"):
        ctx.user_data.pop("waiting_ban")
        if not db.is_admin(uid): return
        try:
            tid = int(txt)
        except ValueError:
            await upd.message.reply_text("❌ معرّف غير صحيح"); return
        if tid == OWNER_ID:
            await upd.message.reply_text("⛔ لا يمكن حظر المالك"); return
        db.ban_user(tid)
        await upd.message.reply_text(
            f"🚫 تم حظر المستخدم <code>{tid}</code>",
            parse_mode=ParseMode.HTML)
        try:
            await ctx.bot.send_message(tid, "🚫 لقد تم حظرك من استخدام البوت")
        except Exception: pass
        return

    if ctx.user_data.get("waiting_unban"):
        ctx.user_data.pop("waiting_unban")
        if not db.is_admin(uid): return
        try:
            tid = int(txt)
        except ValueError:
            await upd.message.reply_text("❌ معرّف غير صحيح"); return
        db.unban_user(tid)
        await upd.message.reply_text(
            f"✅ تم فك حظر المستخدم <code>{tid}</code>",
            parse_mode=ParseMode.HTML)
        try:
            await ctx.bot.send_message(tid, "✅ تم فك حظرك، يمكنك استخدام البوت الآن")
        except Exception: pass
        return

    if ctx.user_data.get("waiting_channel"):
        ctx.user_data.pop("waiting_channel")
        if not db.is_admin(uid): return
        ch_user = txt.strip()
        if not ch_user.startswith("@"):
            ch_user = "@" + ch_user
        try:
            chat = await ctx.bot.get_chat(ch_user)
            db.add_channel(chat.id, chat.username, chat.title, uid)
            await upd.message.reply_text(
                f"✅ تمت إضافة القناة <b>{chat.title}</b>",
                parse_mode=ParseMode.HTML)
        except Exception as e:
            await upd.message.reply_text(f"❌ فشل في إضافة القناة: {e}")
        return

    if ctx.user_data.get("waiting_broadcast"):
        ctx.user_data.pop("waiting_broadcast")
        if not db.is_admin(uid): return
        ids     = db.get_active_ids()
        success = 0
        failed  = 0
        m       = await upd.message.reply_text(
            f"📨 جارٍ الإرسال إلى {len(ids)} مستخدم ...")
        for rid in ids:
            try:
                await ctx.bot.send_message(
                    rid,
                    f"📢 <b>رسالة من الإدارة</b>\n\n{txt}\n\n👑 {TEAM_NAME}",
                    parse_mode=ParseMode.HTML)
                success += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        await m.edit_text(f"✅ تم الإرسال\nنجح : {success} | فشل : {failed}")
        return

    if ctx.user_data.get("waiting_userfiles"):
        ctx.user_data.pop("waiting_userfiles")
        if not db.is_admin(uid): return
        try:
            tid = int(txt)
        except ValueError:
            await upd.message.reply_text("❌ معرّف غير صحيح"); return
        u2    = db.get_user(tid)
        files = db.get_user_files(tid)
        uname = (f"@{u2[1]}" if u2 and u2[1] else f"ID:{tid}") if u2 else f"ID:{tid}"
        if not files:
            await upd.message.reply_text(f"📭 لا يوجد ملفات للمستخدم {uname}")
            return
        txt2  = f"📁 <b>ملفات {uname}</b>\n\n"
        keys2 = []
        for f in files:
            st   = {"pending":"⏳","approved":"✅","rejected":"❌"}.get(f[8],"❓")
            rn   = "🟢" if f[11] else "🔴"
            txt2 += f"{st}{rn} {f[3]} | {f[6].upper()} | {f[5]//1024}KB\n"
            row   = []
            if f[8] == "pending":
                row += [
                    InlineKeyboardButton("✅ قبول", callback_data=f"approve_{f[0]}"),
                    InlineKeyboardButton("❌ رفض",  callback_data=f"reject_{f[0]}"),
                ]
            if f[11]:
                row.append(InlineKeyboardButton("⏹️ إيقاف", callback_data=f"stop_{f[0]}"))
            row.append(InlineKeyboardButton("🗑️ حذف", callback_data=f"del_{f[0]}"))
            if row: keys2.append(row)
        keys2.append([InlineKeyboardButton("🔙 رجوع", callback_data="adm_panel")])
        await upd.message.reply_text(
            txt2, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keys2))
        return

    await upd.message.reply_text(
        "❓ استخدم الأزرار للتنقل\nأو أرسل /start",
        reply_markup=_main_kb(uid))


# ════════════════════════════════════════════════════════════════
#  الدالة الرئيسية
# ════════════════════════════════════════════════════════════════
def main():
    print(
        "\n"
        "  ███████╗██████╗ ██╗    ███████╗██╗  ██╗\n"
        "  ██╔════╝██╔══██╗██║    ██╔════╝╚██╗██╔╝\n"
        "  █████╗  ██████╔╝██║    ███████╗ ╚███╔╝ \n"
        "  ██╔══╝  ██╔═══╝ ██║    ╚════██║ ██╔██╗ \n"
        "  ██║     ██║     ██║    ███████║██╔╝ ██╗\n"
        "  ╚═╝     ╚═╝     ╚═╝    ╚══════╝╚═╝  ╚═╝\n"
        f"\n  Bot     : {BOT_USERNAME}"
        f"\n  Owner   : @{OWNER_USER}"
        f"\n  Team    : {TEAM_NAME}"
        f"\n  Mode    : {'🐋 Docker' if dkr.use_docker else '⚙️ Subprocess'}"
        f"\n  MaxFiles: {db.get_max_files()}"
        f"\n  Stealth : {'ON' if db.get_setting('stealth_mode')=='1' else 'OFF'}"
        f"\n  VirusScan: {'ON' if db.get_setting('virus_scan')=='1' else 'OFF'}\n"
    )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("  ✅ Bot is running ...\n")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
