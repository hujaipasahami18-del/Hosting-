# -*- coding: utf-8 -*-
"""
CHX HOSTING BOT  --  PREMIUM EDITION
Features: tiers (Free/Premium/Admin/Owner), redeem codes, ban system,
auto-restart watchdog, live CPU/RAM per script, log download, server status,
safe env for user scripts, short callback ids, HTML UI, universal broadcast.
"""
import subprocess
import sys
import os


def auto_install(package, import_name=None):
    try:
        __import__(import_name or package)
    except ModuleNotFoundError:
        print(f"Installing missing package: {package} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])


for _pkg, _imp in [("pyTelegramBotAPI", "telebot"), ("psutil", "psutil"),
                   ("requests", "requests"), ("flask", "flask")]:
    auto_install(_pkg, _imp)

import telebot
import inspect as _inspect
if "style" not in _inspect.signature(telebot.types.KeyboardButton.__init__).parameters:
    try:
        print("Upgrading pyTelegramBotAPI for colored buttons ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "pyTelegramBotAPI"])
        os.execv(sys.executable, [sys.executable] + sys.argv)  # নতুন ভার্সনসহ রিস্টার্ট
    except Exception as _e:
        print("Upgrade failed, buttons will have no color:", _e)
import zipfile
import tempfile
import shutil
import time
import html
import hashlib
import json
import secrets
import sqlite3
import logging
import threading
import re
import atexit
import psutil
import requests
from datetime import datetime, timedelta
from telebot import types
from flask import Flask
from threading import Thread

try:
    import resource  # Linux only
except ImportError:
    resource = None


# =====================  COLORED BUTTONS  =====================
# Telegram এর নেটিভ বাটন কালার: success=সবুজ, primary=নীল, danger=লাল
# (নতুন pyTelegramBotAPI দরকার; না থাকলে কালার ছাড়াই বাটন চলবে, বট ভাঙবে না)
_IKB = types.InlineKeyboardButton
_KB = types.KeyboardButton
_RED = ("🗑", "🚫", "🔴", "➖", "Stop", "Delete", "Ban", "Remove", "Lock Bot", "Cancel", "❌")
_GREEN = ("🟢", "▶️", "✅", "➕", "Start", "Add", "Generate", "Unban", "Unlock", "Run All", "Buy", "Upload")
COLOR_STYLE = {"red": "danger", "green": "success", "blue": "primary"}


def auto_color(text):
    t = str(text)
    if "🔙" in t or "Back" in t:
        return ""
    if "Unban" in t or "Unlock" in t:
        return "green"
    if any(k in t for k in _RED):
        return "red"
    if any(k in t for k in _GREEN):
        return "green"
    return "blue"


def IB(text, *args, color=None, **kwargs):
    """InlineKeyboardButton + অটো কালার (color='red'/'green'/'blue'/'' দিলে নিজের পছন্দ)"""
    c = auto_color(text) if color is None else color
    if c in COLOR_STYLE and "style" not in kwargs:
        try:
            return _IKB(text, *args, style=COLOR_STYLE[c], **kwargs)
        except TypeError:
            pass
    return _IKB(text, *args, **kwargs)


def KB(text, color=""):
    if color in COLOR_STYLE:
        try:
            return _KB(text, style=COLOR_STYLE[color])
        except TypeError:
            pass
    return _KB(text)


# =====================  CONFIG  =====================
TOKEN = os.environ.get("BOT_TOKEN", "8567491796:AAF077a2Q9AH6Pw9U5N0OAbH3BqeQ7t8kTs")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")

OWNER_ID = int(os.environ.get("OWNER_ID", "7676787759"))
ADMIN_ID = int(os.environ.get("ADMIN_ID", str(OWNER_ID)))
YOUR_USERNAME = os.environ.get("YOUR_USERNAME", "@rabbi999x")
UPDATE_CHANNEL = os.environ.get("UPDATE_CHANNEL", "https://t.me/")
BOT_NAME = os.environ.get("BOT_NAME", "CHX Hosting")
MAX_PY_MEM_MB = int(os.environ.get("MAX_PY_MEM_MB", "1024"))   # 0 = off (python scripts only)
MAX_UPLOAD_MB = 20

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, "upload_bots")
DATA_DIR = os.path.join(BASE_DIR, "inf")
DATABASE_PATH = os.path.join(DATA_DIR, "bot_data.db")
os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

LIMITS = {"owner": float("inf"), "admin": 999,
          "premium": int(os.environ.get("PREMIUM_USER_LIMIT", "15")),
          "free": int(os.environ.get("FREE_USER_LIMIT", "0"))}   # 0 = hosting is paid only
TIER_LABEL = {"owner": "👑 Owner", "admin": "🛡 Admin", "premium": "💎 Premium", "free": "🆓 Free"}
SECRET_ENV = {"BOT_TOKEN", "OWNER_ID", "ADMIN_ID"}
START_TIME = datetime.now()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("chx")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=True)

# =====================  STATE  =====================
DB_LOCK = threading.Lock()
bot_scripts = {}            # script_key -> info
user_subscriptions = {}     # uid -> {'expiry': datetime}
user_files = {}             # uid -> [(name, type)]
active_users = set()
banned = {}                 # uid -> reason
autorestart = set()         # (uid, name)
manual_stopped = set()      # script_key
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
last_hit = {}
pending_bc = {}             # admin_id -> (chat_id, message_id)


# =====================  DATABASE  =====================
def db(query, params=(), fetch=False):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH)
        try:
            cur = conn.execute(query, params)
            rows = cur.fetchall() if fetch else None
            conn.commit()
            return rows
        except sqlite3.Error as e:
            logger.error(f"DB error: {e} | {query}")
            return [] if fetch else None
        finally:
            conn.close()


def init_db():
    db("CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT)")
    db("CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, "
       "PRIMARY KEY (user_id, file_name))")
    db("CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)")
    db("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
    db("CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY, reason TEXT)")
    db("CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, days INTEGER, used_by INTEGER, used_at TEXT)")
    db("CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY, user_id INTEGER, plan_days INTEGER, "
       "amount INTEGER, method TEXT, trx_id TEXT UNIQUE, status TEXT, created TEXT)")
    db("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    db("CREATE TABLE IF NOT EXISTS autorestart (user_id INTEGER, file_name TEXT, PRIMARY KEY (user_id, file_name))")
    db("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))
    db("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (ADMIN_ID,))


def load_data():
    for uid, exp in db("SELECT user_id, expiry FROM subscriptions", fetch=True):
        try:
            user_subscriptions[uid] = {"expiry": datetime.fromisoformat(exp)}
        except ValueError:
            pass
    for uid, name, ftype in db("SELECT user_id, file_name, file_type FROM user_files", fetch=True):
        user_files.setdefault(uid, []).append((name, ftype))
    active_users.update(r[0] for r in db("SELECT user_id FROM active_users", fetch=True))
    admin_ids.update(r[0] for r in db("SELECT user_id FROM admins", fetch=True))
    for uid, reason in db("SELECT user_id, reason FROM banned", fetch=True):
        banned[uid] = reason or "No reason"
    for uid, name in db("SELECT user_id, file_name FROM autorestart", fetch=True):
        autorestart.add((uid, name))


init_db()
load_data()

# =====================  KEEP ALIVE  =====================
app = Flask(__name__)


@app.route("/")
def home():
    return f"{BOT_NAME} is running."


def keep_alive():
    t = Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True)
    t.start()


# =====================  HELPERS  =====================
def h(x):
    return html.escape(str(x))


def fid(name):
    return hashlib.sha1(name.encode()).hexdigest()[:8]


def fmt_td(td):
    s = int(td.total_seconds())
    d, s = divmod(s, 86400)
    hr, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{d}d " if d else "") + f"{hr:02d}h {m:02d}m {s:02d}s"


def is_premium(uid):
    s = user_subscriptions.get(uid)
    return bool(s and s["expiry"] > datetime.now())


def tier(uid):
    if uid == OWNER_ID:
        return "owner"
    if uid in admin_ids:
        return "admin"
    return "premium" if is_premium(uid) else "free"


def file_limit(uid):
    return LIMITS[tier(uid)]


def limit_str(uid):
    l = file_limit(uid)
    return "∞" if l == float("inf") else str(l)


def user_folder(uid):
    p = os.path.join(UPLOAD_BOTS_DIR, str(uid))
    os.makedirs(p, exist_ok=True)
    return p


def log_path(uid, name):
    return os.path.join(user_folder(uid), os.path.splitext(name)[0] + ".log")


def find_file(owner, fid_):
    for name, ftype in user_files.get(owner, []):
        if fid(name) == fid_:
            return name, ftype
    return None


def send(cid, text, markup=None, **kw):
    try:
        return bot.send_message(cid, text, reply_markup=markup, **kw)
    except Exception as e:
        logger.error(f"send error to {cid}: {e}")


def edit(cid, mid, text, markup=None):
    try:
        bot.edit_message_text(text, cid, mid, reply_markup=markup)
    except telebot.apihelper.ApiTelegramException as e:
        if "not modified" not in str(e):
            logger.error(f"edit error: {e}")
    except Exception as e:
        logger.error(f"edit error: {e}")


def allowed(uid, cid, rate_limit=True):
    if uid in banned:
        send(cid, f"🚫 <b>You are banned.</b>\nReason: <i>{h(banned[uid])}</i>")
        return False
    if bot_locked and uid not in admin_ids:
        send(cid, "🔒 <b>Bot is under maintenance.</b> Please try later.")
        return False
    if rate_limit:
        now = time.time()
        if now - last_hit.get(uid, 0) < 0.6:
            return False
        last_hit[uid] = now
    return True


def save_file(uid, name, ftype):
    db("INSERT OR REPLACE INTO user_files VALUES (?,?,?)", (uid, name, ftype))
    user_files[uid] = [(n, t) for n, t in user_files.get(uid, []) if n != name] + [(name, ftype)]


def remove_file_record(uid, name):
    db("DELETE FROM user_files WHERE user_id=? AND file_name=?", (uid, name))
    db("DELETE FROM autorestart WHERE user_id=? AND file_name=?", (uid, name))
    autorestart.discard((uid, name))
    user_files[uid] = [(n, t) for n, t in user_files.get(uid, []) if n != name]
    if not user_files[uid]:
        user_files.pop(uid, None)


def add_active_user(uid):
    active_users.add(uid)
    db("INSERT OR IGNORE INTO active_users VALUES (?)", (uid,))


def set_subscription(uid, expiry):
    db("INSERT OR REPLACE INTO subscriptions VALUES (?,?)", (uid, expiry.isoformat()))
    user_subscriptions[uid] = {"expiry": expiry}


def drop_subscription(uid):
    db("DELETE FROM subscriptions WHERE user_id=?", (uid,))
    user_subscriptions.pop(uid, None)


def extend_premium(uid, days):
    cur = user_subscriptions.get(uid, {}).get("expiry")
    base = cur if cur and cur > datetime.now() else datetime.now()
    new = base + timedelta(days=days)
    set_subscription(uid, new)
    return new


# =====================  PROCESS MANAGEMENT  =====================
def script_key(uid, name):
    return f"{uid}|{name}"


def is_running(uid, name):
    key = script_key(uid, name)
    info = bot_scripts.get(key)
    if not info:
        return False
    try:
        p = psutil.Process(info["process"].pid)
        ok = p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        ok = False
    if not ok:
        try:
            info["log_file"].close()
        except Exception:
            pass
        bot_scripts.pop(key, None)
    return ok


def kill_tree(info):
    try:
        info["log_file"].close()
    except Exception:
        pass
    try:
        parent = psutil.Process(info["process"].pid)
        procs = parent.children(recursive=True)
        for c in procs:
            try:
                c.terminate()
            except Exception:
                pass
        _, alive = psutil.wait_procs(procs, timeout=2)
        for c in alive:
            try:
                c.kill()
            except Exception:
                pass
        parent.terminate()
        try:
            parent.wait(timeout=3)
        except psutil.TimeoutExpired:
            parent.kill()
    except psutil.NoSuchProcess:
        pass
    except Exception as e:
        logger.error(f"kill error: {e}")


def stop_script(uid, name, manual=True):
    key = script_key(uid, name)
    info = bot_scripts.get(key)
    if info:
        kill_tree(info)
        bot_scripts.pop(key, None)
    if manual:
        manual_stopped.add(key)


def proc_stats(info):
    try:
        p = psutil.Process(info["process"].pid)
        procs = [p] + p.children(recursive=True)
        mem = sum(x.memory_info().rss for x in procs) / 1024 / 1024
        cpu = sum(x.cpu_percent(interval=None) for x in procs)
        return round(cpu, 1), round(mem, 1)
    except Exception:
        return 0.0, 0.0


def clean_env(folder):
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env["HOME"] = folder
    env["PYTHONUNBUFFERED"] = "1"
    return env


def make_preexec(ftype):
    if os.name != "posix" or resource is None:
        return None

    def _limits():
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (500 * 1024 * 1024, 500 * 1024 * 1024))
            if ftype == "py" and MAX_PY_MEM_MB > 0:
                b = MAX_PY_MEM_MB * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (b, b))
        except Exception:
            pass
    return _limits


PIP_MAP = {
    "telebot": "pyTelegramBotAPI", "telegram": "python-telegram-bot", "bs4": "beautifulsoup4",
    "cv2": "opencv-python", "yaml": "PyYAML", "dotenv": "python-dotenv", "PIL": "Pillow",
    "dateutil": "python-dateutil", "pyrogram": "pyrogram", "telethon": "telethon",
    "aiogram": "aiogram", "flask": "Flask", "sklearn": "scikit-learn", "Crypto": "pycryptodome",
}


def pip_install(module, cid):
    pkg = PIP_MAP.get(module, PIP_MAP.get(module.lower(), module.split(".")[0]))
    send(cid, f"🐍 Installing <code>{h(pkg)}</code> ...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", pkg],
                       capture_output=True, text=True, errors="ignore")
    if r.returncode == 0:
        send(cid, f"✅ <code>{h(pkg)}</code> installed.")
        return True
    send(cid, f"❌ Failed to install <code>{h(pkg)}</code>\n<pre>{h((r.stderr or r.stdout)[-800:])}</pre>")
    return False


def npm_install(module, folder, cid):
    send(cid, f"🟠 Installing node package <code>{h(module)}</code> ...")
    try:
        r = subprocess.run(["npm", "install", module], capture_output=True, text=True, cwd=folder, errors="ignore")
    except FileNotFoundError:
        send(cid, "❌ npm not found on server.")
        return False
    if r.returncode == 0:
        send(cid, f"✅ <code>{h(module)}</code> installed.")
        return True
    send(cid, f"❌ npm failed\n<pre>{h((r.stderr or r.stdout)[-800:])}</pre>")
    return False


def start_script(uid, name, cid, attempt=1):
    """Starts a hosted script (py/js). Sends feedback to chat `cid`."""
    if file_limit(uid) == 0:
        send(cid, "🔒 Your plan has expired. Buy a plan to run scripts.", plans_markup())
        return False
    ftype = next((t for n, t in user_files.get(uid, []) if n == name), None)
    folder = user_folder(uid)
    path = os.path.join(folder, name)
    if not ftype or not os.path.exists(path):
        send(cid, f"❌ File <code>{h(name)}</code> not found. Re-upload it.")
        if ftype:
            remove_file_record(uid, name)
        return False
    if is_running(uid, name):
        send(cid, f"⚠️ <code>{h(name)}</code> is already running.")
        return True
    cmd = [sys.executable, path] if ftype == "py" else ["node", path]
    key = script_key(uid, name)
    env = clean_env(folder)

    # ---- quick pre-check (detects missing modules) ----
    if attempt == 1:
        chk = None
        try:
            chk = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, errors="ignore", env=env, preexec_fn=make_preexec(ftype))
            _, err = chk.communicate(timeout=6)
            if chk.returncode != 0 and err:
                m = re.search(r"No module named '([^']+)'", err) if ftype == "py" else \
                    re.search(r"Cannot find module '([^']+)'", err)
                if m and not m.group(1).startswith((".", "/")):
                    mod = m.group(1)
                    ok = pip_install(mod, cid) if ftype == "py" else npm_install(mod, folder, cid)
                    if ok:
                        return start_script(uid, name, cid, attempt=2)
                    return False
                send(cid, f"❌ <b>Script crashed on start</b> — <code>{h(name)}</code>\n<pre>{h(err[-900:])}</pre>")
                return False
        except subprocess.TimeoutExpired:
            if chk:
                chk.kill()
                chk.communicate()
        except FileNotFoundError:
            send(cid, "❌ Interpreter not found (node/python missing on server).")
            return False
        except Exception as e:
            send(cid, f"❌ Pre-check error: <code>{h(e)}</code>")
            return False
        finally:
            if chk and chk.poll() is None:
                chk.kill()

    # ---- real launch ----
    try:
        lf = open(log_path(uid, name), "w", encoding="utf-8", errors="ignore")
        proc = subprocess.Popen(cmd, cwd=folder, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                                env=env, preexec_fn=make_preexec(ftype))
        bot_scripts[key] = {"process": proc, "log_file": lf, "name": name, "uid": uid,
                            "start": datetime.now(), "type": ftype}
        manual_stopped.discard(key)
        send(cid, f"✅ <b>Started</b> <code>{h(name)}</code>\n🆔 PID: <code>{proc.pid}</code>")
        return True
    except Exception as e:
        send(cid, f"❌ Could not start <code>{h(name)}</code>: <code>{h(e)}</code>")
        return False


# =====================  UI BUILDERS  =====================
def profile_text(user):
    uid = user.id
    t = tier(uid)
    exp = ""
    if t == "premium":
        left = user_subscriptions[uid]["expiry"] - datetime.now()
        exp = f"\n⏳ <b>Expires in:</b> {left.days}d {left.seconds // 3600}h"
    running = sum(1 for n, _ in user_files.get(uid, []) if is_running(uid, n))
    return (f"╭─── <b>{h(BOT_NAME)}</b> ───╮\n"
            f"👤 <b>{h(user.first_name)}</b>\n"
            f"🆔 <code>{uid}</code>\n"
            f"🏅 <b>Plan:</b> {TIER_LABEL[t]}{exp}\n"
            f"📁 <b>Files:</b> {len(user_files.get(uid, []))}/{limit_str(uid)}\n"
            f"🟢 <b>Running:</b> {running}\n"
            f"╰────────────────╯\n"
            f"<i>Host Python / JS scripts or ZIP projects 24/7.</i>")


def main_menu(uid):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(IB("📤 Upload", callback_data="m:upload"),
          IB("📂 My Files", callback_data="m:files"))
    m.add(IB("👤 Profile", callback_data="m:profile"),
          IB("🎟 Redeem", callback_data="m:redeem"))
    m.add(IB("🖥 Server", callback_data="m:server"),
          IB("⚡ Speed", callback_data="m:speed"))
    m.add(IB("📢 Updates", url=UPDATE_CHANNEL),
          IB("💎 Buy Plan", callback_data="m:buy"))
    if uid in admin_ids:
        m.add(IB("👑 Admin Panel", callback_data="m:admin", color="red"))
    return m


def reply_keyboard(uid):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, is_persistent=True,
                                  input_field_placeholder="👇 Choose from the buttons below")
    k.row(KB("📤 Upload File", "green"), KB("📂 Check Files", "blue"))
    k.row(KB("👤 Profile", "blue"), KB("💎 Buy Plan", "green"))
    k.row(KB("🎟 Redeem Code", "red"))
    k.row(KB("🖥 Server Status", "blue"), KB("⚡ Bot Speed", "green"))
    k.row(KB("📢 Updates Channel", "green"), KB("📞 Contact Owner", "blue"))
    if uid in admin_ids:
        k.row(KB("👑 Admin Panel", "red"))
    return k


def admin_menu():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(IB("💎 Add Premium", callback_data="adm:subadd"),
          IB("➖ Remove Premium", callback_data="adm:subdel"))
    m.add(IB("🔍 Check User", callback_data="adm:subchk"),
          IB("🎟 Generate Codes", callback_data="adm:gen"))
    m.add(IB("🚫 Ban", callback_data="adm:ban"),
          IB("✅ Unban", callback_data="adm:unban"))
    m.add(IB("📊 Statistics", callback_data="adm:stats"),
          IB("📢 Broadcast", callback_data="adm:bc"))
    m.add(IB("🔓 Unlock Bot" if bot_locked else "🔒 Lock Bot", callback_data="adm:lock"),
          IB("▶️ Run All Scripts", callback_data="adm:runall"))
    if True:
        m.add(IB("➕ Add Admin", callback_data="adm:addadm"),
              IB("➖ Remove Admin", callback_data="adm:rmadm"))
    m.add(IB("📱 Set bKash No.", callback_data="adm:setbkash"),
          IB("📱 Set Nagad No.", callback_data="adm:setnagad"))
    m.add(IB("📦 Add/Edit Plan", callback_data="adm:setplan"),
          IB("🗑 Remove Plan", callback_data="adm:delplan"))
    m.add(IB("💳 View Payment Settings", callback_data="adm:payinfo"))
    m.add(IB("📋 List Admins", callback_data="adm:listadm"),
          IB("🔙 Back", callback_data="m:home"))
    return m


def files_markup(uid):
    m = types.InlineKeyboardMarkup(row_width=1)
    for name, ftype in sorted(user_files.get(uid, [])):
        icon = "🟢" if is_running(uid, name) else "🔴"
        ar = " ♻️" if (uid, name) in autorestart else ""
        m.add(IB(f"{icon} {name} [{ftype}]{ar}", callback_data=f"f:{uid}:{fid(name)}"))
    m.add(IB("🔙 Back", callback_data="m:home"))
    return m


def file_card(owner, name, ftype):
    running = is_running(owner, name)
    lines = [f"⚙️ <b>{h(name)}</b>  <code>{ftype}</code>",
             f"👤 Owner: <code>{owner}</code>",
             f"📡 Status: {'🟢 <b>Running</b>' if running else '🔴 <b>Stopped</b>'}"]
    if running:
        info = bot_scripts[script_key(owner, name)]
        cpu, mem = proc_stats(info)
        lines += [f"🆔 PID: <code>{info['process'].pid}</code>",
                  f"⏱ Uptime: {fmt_td(datetime.now() - info['start'])}",
                  f"🧠 RAM: {mem} MB  |  ⚙️ CPU: {cpu}%"]
    lines.append(f"♻️ Auto-restart: {'<b>ON</b>' if (owner, name) in autorestart else 'OFF'}")
    return "\n".join(lines), running


def control_markup(owner, name, running):
    f = fid(name)
    m = types.InlineKeyboardMarkup(row_width=2)
    if running:
        m.add(IB("🔴 Stop", callback_data=f"a:stop:{owner}:{f}"),
              IB("🔄 Restart", callback_data=f"a:rst:{owner}:{f}"))
    else:
        m.add(IB("🟢 Start", callback_data=f"a:run:{owner}:{f}"))
    m.add(IB("📜 Logs", callback_data=f"a:log:{owner}:{f}"),
          IB("📥 Download Log", callback_data=f"a:dl:{owner}:{f}"))
    m.add(IB("♻️ Toggle Auto-Restart", callback_data=f"a:ar:{owner}:{f}"),
          IB("🗑 Delete", callback_data=f"a:del:{owner}:{f}"))
    m.add(IB("🔄 Refresh", callback_data=f"f:{owner}:{f}"),
          IB("🔙 Files", callback_data="m:files"))
    return m


def server_text():
    cpu = psutil.cpu_percent(interval=0.4)
    vm = psutil.virtual_memory()
    try:
        du = shutil.disk_usage(BASE_DIR)
        disk = f"{du.used / 1e9:.1f}/{du.total / 1e9:.1f} GB ({du.used / du.total * 100:.0f}%)"
    except Exception:
        disk = "N/A"
    total_running = sum(1 for k, i in list(bot_scripts.items()) if is_running(i["uid"], i["name"]))
    return (f"🖥 <b>Server Status</b>\n\n"
            f"⚙️ CPU: <b>{cpu}%</b>\n"
            f"🧠 RAM: <b>{vm.percent}%</b> ({vm.used / 1e9:.1f}/{vm.total / 1e9:.1f} GB)\n"
            f"💾 Disk: <b>{disk}</b>\n"
            f"🤖 Hosted scripts running: <b>{total_running}</b>\n"
            f"⏱ Bot uptime: <b>{fmt_td(datetime.now() - START_TIME)}</b>")


def stats_text():
    prem = sum(1 for u in user_subscriptions if is_premium(u))
    total_files = sum(len(v) for v in user_files.values())
    running = sum(1 for k, i in list(bot_scripts.items()) if is_running(i["uid"], i["name"]))
    revenue = (db("SELECT SUM(amount) FROM payments WHERE status='approved'", fetch=True) or [[0]])[0][0] or 0
    pending = (db("SELECT COUNT(*) FROM payments WHERE status='pending'", fetch=True) or [[0]])[0][0]
    return (f"📊 <b>Statistics</b>\n\n"
            f"👥 Users: <b>{len(active_users)}</b>\n"
            f"💎 Premium: <b>{prem}</b>\n"
            f"🛡 Admins: <b>{len(admin_ids)}</b>\n"
            f"🚫 Banned: <b>{len(banned)}</b>\n"
            f"📂 Files: <b>{total_files}</b>\n"
            f"🟢 Running: <b>{running}</b>\n"
            f"💰 Revenue: <b>{CURRENCY}{revenue}</b>\n"
            f"⏳ Pending payments: <b>{pending}</b>\n"
            f"🔒 Locked: <b>{'Yes' if bot_locked else 'No'}</b>")


# =====================  FILE UPLOAD  =====================
def install_deps(root, cid):
    req = os.path.join(root, "requirements.txt")
    if os.path.exists(req):
        send(cid, "🔄 Installing Python requirements ...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", req],
                           capture_output=True, text=True, errors="ignore")
        if r.returncode != 0:
            send(cid, f"❌ requirements failed\n<pre>{h((r.stderr or r.stdout)[-900:])}</pre>")
            return False
        send(cid, "✅ Python requirements installed.")
    if os.path.exists(os.path.join(root, "package.json")):
        send(cid, "🔄 Installing Node dependencies ...")
        try:
            r = subprocess.run(["npm", "install"], capture_output=True, text=True, cwd=root, errors="ignore")
        except FileNotFoundError:
            send(cid, "❌ npm not found on server.")
            return False
        if r.returncode != 0:
            send(cid, f"❌ npm install failed\n<pre>{h((r.stderr or r.stdout)[-900:])}</pre>")
            return False
        send(cid, "✅ Node dependencies installed.")
    return True


def handle_zip(content, zip_name, uid, cid):
    tmp = tempfile.mkdtemp(prefix=f"u{uid}_")
    try:
        zpath = os.path.join(tmp, "upload.zip")
        with open(zpath, "wb") as f:
            f.write(content)
        ex = os.path.join(tmp, "ex")
        os.makedirs(ex)
        with zipfile.ZipFile(zpath) as z:
            base = os.path.abspath(ex)
            for mbr in z.infolist():
                target = os.path.abspath(os.path.join(ex, mbr.filename))
                if not (target == base or target.startswith(base + os.sep)):
                    raise zipfile.BadZipFile(f"unsafe path: {mbr.filename}")
            z.extractall(ex)
        root = ex
        items = os.listdir(root)
        if len(items) == 1 and os.path.isdir(os.path.join(root, items[0])):
            root = os.path.join(root, items[0])
        items = os.listdir(root)
        if not install_deps(root, cid):
            return
        main, ftype = None, None
        for p in ("main.py", "bot.py", "app.py", "index.py"):
            if p in items:
                main, ftype = p, "py"
                break
        if not main:
            for p in ("index.js", "main.js", "bot.js", "app.js"):
                if p in items:
                    main, ftype = p, "js"
                    break
        if not main:
            py = [x for x in items if x.endswith(".py")]
            js = [x for x in items if x.endswith(".js")]
            if py:
                main, ftype = py[0], "py"
            elif js:
                main, ftype = js[0], "js"
        if not main:
            send(cid, "❌ No <code>.py</code> / <code>.js</code> file found at the top level of the ZIP.")
            return
        folder = user_folder(uid)
        for it in items:
            dst = os.path.join(folder, it)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            elif os.path.exists(dst):
                os.remove(dst)
            shutil.move(os.path.join(root, it), dst)
        save_file(uid, main, ftype)
        send(cid, f"✅ ZIP extracted. Main script: <code>{h(main)}</code>")
        start_script(uid, main, cid)
    except zipfile.BadZipFile as e:
        send(cid, f"❌ Invalid ZIP: <code>{h(e)}</code>")
    except Exception as e:
        logger.error(f"zip error: {e}", exc_info=True)
        send(cid, f"❌ ZIP error: <code>{h(e)}</code>")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@bot.message_handler(content_types=["document"])
def on_document(message):
    uid, cid = message.from_user.id, message.chat.id
    if not allowed(uid, cid):
        return
    doc = message.document
    name = os.path.basename(doc.file_name or "")
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in (".py", ".js", ".zip"):
        send(cid, "⚠️ Only <code>.py</code>, <code>.js</code> and <code>.zip</code> files are supported.")
        return
    if file_limit(uid) == 0 or (len(user_files.get(uid, [])) >= file_limit(uid) and
                                not any(n == name for n, _ in user_files.get(uid, []))):
        if file_limit(uid) == 0:
            send(cid, "🔒 <b>Hosting is for paid users only.</b>\nChoose a plan to start hosting:", plans_markup())
        else:
            send(cid, f"⚠️ File limit reached (<b>{len(user_files.get(uid, []))}/{limit_str(uid)}</b>).\n"
                      f"Delete a file or upgrade your plan.")
        return
    if doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
        send(cid, f"⚠️ File too large (max {MAX_UPLOAD_MB} MB).")
        return
    try:
        try:
            bot.forward_message(OWNER_ID, cid, message.message_id)
            send(OWNER_ID, f"⬆️ <code>{h(name)}</code> from {h(message.from_user.first_name)} (<code>{uid}</code>)")
        except Exception:
            pass
        wait = send(cid, f"⏳ Downloading <code>{h(name)}</code> ...")
        content = bot.download_file(bot.get_file(doc.file_id).file_path)
        if wait:
            edit(cid, wait.message_id, f"✅ Downloaded <code>{h(name)}</code>. Processing ...")
        if ext == ".zip":
            threading.Thread(target=handle_zip, args=(content, name, uid, cid), daemon=True).start()
        else:
            path = os.path.join(user_folder(uid), name)
            if is_running(uid, name):
                stop_script(uid, name, manual=False)
            with open(path, "wb") as f:
                f.write(content)
            save_file(uid, name, ext[1:])
            threading.Thread(target=start_script, args=(uid, name, cid), daemon=True).start()
    except Exception as e:
        logger.error(f"upload error: {e}", exc_info=True)
        send(cid, f"❌ Upload error: <code>{h(e)}</code>")


# =====================  USER COMMANDS  =====================
def do_welcome(message):
    u = message.from_user
    if not allowed(u.id, message.chat.id):
        return
    if u.id not in active_users:
        add_active_user(u.id)
        send(OWNER_ID, f"🎉 <b>New user</b>\n👤 {h(u.first_name)}\n✳️ @{h(u.username or 'N/A')}\n🆔 <code>{u.id}</code>")
    send(message.chat.id, "⌨️ <i>Quick menu activated.</i>", reply_keyboard(u.id))
    send(message.chat.id, profile_text(u), main_menu(u.id))


@bot.message_handler(commands=["start", "help", "menu"])
def cmd_start(message):
    do_welcome(message)


@bot.message_handler(commands=["buy", "plans", "plan"])
def cmd_buy(message):
    if allowed(message.from_user.id, message.chat.id):
        send(message.chat.id, plans_text(), plans_markup())


@bot.message_handler(commands=["ping"])
def cmd_ping(message):
    t = time.time()
    m = send(message.chat.id, "🏓 Pong!")
    if m:
        edit(message.chat.id, m.message_id, f"🏓 Pong! <b>{round((time.time() - t) * 1000)} ms</b>")


def do_speed(cid, uid):
    t = time.time()
    bot.send_chat_action(cid, "typing")
    ms = round((time.time() - t) * 1000, 1)
    send(cid, f"⚡ <b>Speed</b>\n⏱ API: <b>{ms} ms</b>\n🏅 Plan: {TIER_LABEL[tier(uid)]}")


def do_upload_info(cid, uid):
    if file_limit(uid) == 0:
        send(cid, "🔒 <b>Hosting is for paid users only.</b>\nChoose a plan to start hosting:", plans_markup())
        return
    cur = len(user_files.get(uid, []))
    send(cid, f"📤 <b>Send your file now</b>\n\nSupported: <code>.py</code> · <code>.js</code> · <code>.zip</code>\n"
              f"Slots used: <b>{cur}/{limit_str(uid)}</b>\n"
              f"<i>ZIP can include requirements.txt / package.json — auto installed.</i>")


def do_files(cid, uid, mid=None):
    if not user_files.get(uid):
        text, mk = "📂 <b>Your files</b>\n\n<i>No files uploaded yet.</i>", None
        mk = types.InlineKeyboardMarkup().add(IB("🔙 Back", callback_data="m:home"))
    else:
        text, mk = "📂 <b>Your files</b>\nTap a file to manage it.", files_markup(uid)
    if mid:
        edit(cid, mid, text, mk)
    else:
        send(cid, text, mk)


def do_redeem_prompt(cid, uid):
    m = send(cid, "🎟 <b>Send your redeem code</b>\nExample: <code>CHX-AB12-CD34</code>\n/cancel to abort.")
    if m:
        bot.register_next_step_handler(m, process_redeem)


def process_redeem(message):
    uid, cid = message.from_user.id, message.chat.id
    if not message.text or message.text.startswith("/cancel"):
        send(cid, "Cancelled.")
        return
    if uid in banned:
        return
    code = message.text.strip().upper()
    row = db("SELECT days, used_by FROM codes WHERE code=?", (code,), fetch=True)
    if not row:
        send(cid, "❌ Invalid code.")
        return
    days, used = row[0]
    if used:
        send(cid, "⚠️ This code was already used.")
        return
    db("UPDATE codes SET used_by=?, used_at=? WHERE code=?", (uid, datetime.now().isoformat(), code))
    new = extend_premium(uid, days)
    send(cid, f"🎉 <b>Premium activated!</b>\n+{days} days\nExpires: <b>{new:%Y-%m-%d %H:%M}</b>")
    send(OWNER_ID, f"🎟 Code <code>{code}</code> redeemed by <code>{uid}</code> (+{days}d)")


TEXT_BUTTONS = {
    "📤 Upload File": lambda m: do_upload_info(m.chat.id, m.from_user.id),
    "📂 Check Files": lambda m: do_files(m.chat.id, m.from_user.id),
    "👤 Profile": lambda m: send(m.chat.id, profile_text(m.from_user), main_menu(m.from_user.id)),
    "🎟 Redeem Code": lambda m: do_redeem_prompt(m.chat.id, m.from_user.id),
    "💎 Buy Plan": lambda m: send(m.chat.id, plans_text(), plans_markup()),
    "🖥 Server Status": lambda m: send(m.chat.id, server_text()),
    "⚡ Bot Speed": lambda m: do_speed(m.chat.id, m.from_user.id),
    "📢 Updates Channel": lambda m: send(m.chat.id, "📢 Join our channel:", types.InlineKeyboardMarkup().add(
        IB("Open Channel", url=UPDATE_CHANNEL))),
    "📞 Contact Owner": lambda m: send(m.chat.id, "📞 Contact the owner:", types.InlineKeyboardMarkup().add(
        IB("Message Owner", url=f"https://t.me/{YOUR_USERNAME.lstrip('@')}"))),
    "👑 Admin Panel": lambda m: send(m.chat.id, "👑 <b>Admin Panel</b>", admin_menu()) if m.from_user.id in admin_ids else None,
}


@bot.message_handler(func=lambda m: m.text in TEXT_BUTTONS)
def on_text_button(message):
    if not allowed(message.from_user.id, message.chat.id):
        return
    TEXT_BUTTONS[message.text](message)


# =====================  CALLBACKS  =====================
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    uid, cid, mid = call.from_user.id, call.message.chat.id, call.message.message_id
    data = call.data
    if uid in banned:
        bot.answer_callback_query(call.id, "🚫 Banned", show_alert=True)
        return
    if bot_locked and uid not in admin_ids and data not in ("m:home",):
        bot.answer_callback_query(call.id, "🔒 Maintenance mode", show_alert=True)
        return
    try:
        p = data.split(":")
        kind = p[0]
        if kind == "m":
            menu_cb(call, p[1], uid, cid, mid)
        elif kind == "f":
            file_open_cb(call, int(p[1]), p[2], uid, cid, mid)
        elif kind == "a":
            file_action_cb(call, p[1], int(p[2]), p[3], uid, cid, mid)
        elif kind == "adm":
            if uid not in admin_ids:
                bot.answer_callback_query(call.id, "⚠️ Admins only", show_alert=True)
                return
            admin_cb(call, p[1], uid, cid, mid)
        elif kind == "bc":
            broadcast_cb(call, p[1], uid, cid, mid)
        elif kind == "buy":
            buy_cb(call, p[1], uid, cid, mid)
        elif kind == "pay":
            pay_cb(call, p, uid, cid, mid)
        else:
            bot.answer_callback_query(call.id, "Unknown action")
    except Exception as e:
        logger.error(f"callback error {data}: {e}", exc_info=True)
        try:
            bot.answer_callback_query(call.id, "Error", show_alert=True)
        except Exception:
            pass


def menu_cb(call, act, uid, cid, mid):
    bot.answer_callback_query(call.id)
    if act == "home" or act == "profile":
        edit(cid, mid, profile_text(call.from_user), main_menu(uid))
    elif act == "upload":
        do_upload_info(cid, uid)
    elif act == "files":
        do_files(cid, uid, mid)
    elif act == "redeem":
        do_redeem_prompt(cid, uid)
    elif act == "server":
        edit(cid, mid, server_text(), types.InlineKeyboardMarkup().add(
            IB("🔄 Refresh", callback_data="m:server"),
            IB("🔙 Back", callback_data="m:home")))
    elif act == "speed":
        do_speed(cid, uid)
    elif act == "buy":
        edit(cid, mid, plans_text(), plans_markup())
    elif act == "admin":
        if uid in admin_ids:
            edit(cid, mid, "👑 <b>Admin Panel</b>", admin_menu())


def file_open_cb(call, owner, f, uid, cid, mid):
    if uid != owner and uid not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Not your file", show_alert=True)
        return
    found = find_file(owner, f)
    if not found:
        bot.answer_callback_query(call.id, "File not found", show_alert=True)
        do_files(cid, uid, mid)
        return
    bot.answer_callback_query(call.id)
    text, running = file_card(owner, *found)
    edit(cid, mid, text, control_markup(owner, found[0], running))


def file_action_cb(call, act, owner, f, uid, cid, mid):
    if uid != owner and uid not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Permission denied", show_alert=True)
        return
    found = find_file(owner, f)
    if not found:
        bot.answer_callback_query(call.id, "File not found", show_alert=True)
        do_files(cid, uid, mid)
        return
    name, ftype = found

    def refresh():
        text, running = file_card(owner, name, ftype)
        edit(cid, mid, text, control_markup(owner, name, running))

    if act == "run":
        bot.answer_callback_query(call.id, "⏳ Starting ...")
        start_script(owner, name, cid)
        time.sleep(1)
        refresh()
    elif act == "stop":
        bot.answer_callback_query(call.id, "⏳ Stopping ...")
        stop_script(owner, name)
        refresh()
    elif act == "rst":
        bot.answer_callback_query(call.id, "⏳ Restarting ...")
        stop_script(owner, name, manual=False)
        time.sleep(1.2)
        start_script(owner, name, cid)
        time.sleep(1)
        refresh()
    elif act == "ar":
        if tier(owner) == "free":
            bot.answer_callback_query(call.id, "💎 Auto-restart is a Premium feature", show_alert=True)
            return
        if (owner, name) in autorestart:
            autorestart.discard((owner, name))
            db("DELETE FROM autorestart WHERE user_id=? AND file_name=?", (owner, name))
            bot.answer_callback_query(call.id, "♻️ Auto-restart OFF")
        else:
            autorestart.add((owner, name))
            db("INSERT OR IGNORE INTO autorestart VALUES (?,?)", (owner, name))
            bot.answer_callback_query(call.id, "♻️ Auto-restart ON")
        refresh()
    elif act == "log":
        lp = log_path(owner, name)
        if not os.path.exists(lp):
            bot.answer_callback_query(call.id, "No logs yet", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        with open(lp, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 3000))
            txt = fh.read().decode("utf-8", errors="ignore").strip() or "(empty)"
        send(cid, f"📜 <b>Logs</b> — <code>{h(name)}</code>\n<pre>{h(txt[-3000:])}</pre>")
    elif act == "dl":
        lp = log_path(owner, name)
        if not os.path.exists(lp) or os.path.getsize(lp) == 0:
            bot.answer_callback_query(call.id, "Log is empty", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        with open(lp, "rb") as fh:
            bot.send_document(cid, fh, visible_file_name=os.path.basename(lp), caption=f"📥 Log of {name}")
    elif act == "del":
        bot.answer_callback_query(call.id)
        m = types.InlineKeyboardMarkup().add(
            IB("✅ Yes, delete", callback_data=f"a:delok:{owner}:{f}"),
            IB("❌ Cancel", callback_data=f"f:{owner}:{f}"))
        edit(cid, mid, f"🗑 Delete <code>{h(name)}</code> permanently?", m)
    elif act == "delok":
        stop_script(owner, name, manual=False)
        folder = user_folder(owner)
        for pth in (os.path.join(folder, name), log_path(owner, name)):
            try:
                os.remove(pth)
            except OSError:
                pass
        remove_file_record(owner, name)
        bot.answer_callback_query(call.id, "🗑 Deleted")
        do_files(cid, uid, mid)


# =====================  ADMIN ACTIONS  =====================
PROMPTS = {
    "subadd": "💎 Send: <code>USER_ID DAYS</code>",
    "subdel": "➖ Send the <code>USER_ID</code> to remove premium",
    "subchk": "🔍 Send the <code>USER_ID</code> to check",
    "gen": "🎟 Send: <code>DAYS COUNT</code>  (e.g. <code>30 5</code> = five 30-day codes)",
    "ban": "🚫 Send: <code>USER_ID reason</code>",
    "unban": "✅ Send the <code>USER_ID</code> to unban",
    "addadm": "➕ Send the <code>USER_ID</code> to promote (owner only)",
    "rmadm": "➖ Send the <code>USER_ID</code> to demote (owner only)",
    "setbkash": "📱 Send your <b>bKash number</b> (e.g. <code>017XXXXXXXX</code>)\nSend <code>off</code> to disable bKash. (owner only)",
    "setnagad": "📱 Send your <b>Nagad number</b> (e.g. <code>018XXXXXXXX</code>)\nSend <code>off</code> to disable Nagad. (owner only)",
    "setplan": "📦 Send: <code>DAYS PRICE</code>\nExample: <code>3 30</code> = 3 days for 30. Same days again = update price. (owner only)",
    "delplan": "🗑 Send the <b>DAYS</b> of the plan to remove (e.g. <code>3</code>). (owner only)",
}


def admin_cb(call, act, uid, cid, mid):
    global bot_locked
    if act in PROMPTS:
        if act in ("addadm", "rmadm", "setbkash", "setnagad", "setplan", "delplan") and uid != OWNER_ID:
            bot.answer_callback_query(call.id, "⚠️ Owner only", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        m = send(cid, PROMPTS[act] + "\n/cancel to abort.")
        if m:
            bot.register_next_step_handler(m, admin_input, act)
    elif act == "payinfo":
        bot.answer_callback_query(call.id)
        edit(cid, mid, pay_settings_text(), types.InlineKeyboardMarkup().add(
            IB("🔙 Back", callback_data="m:admin")))
    elif act == "stats":
        bot.answer_callback_query(call.id)
        edit(cid, mid, stats_text(), types.InlineKeyboardMarkup().add(
            IB("🔙 Back", callback_data="m:admin")))
    elif act == "lock":
        bot_locked = not bot_locked
        bot.answer_callback_query(call.id, "🔒 Locked" if bot_locked else "🔓 Unlocked")
        edit(cid, mid, "👑 <b>Admin Panel</b>", admin_menu())
    elif act == "listadm":
        bot.answer_callback_query(call.id)
        lst = "\n".join(f"• <code>{a}</code>{' (Owner)' if a == OWNER_ID else ''}" for a in sorted(admin_ids))
        edit(cid, mid, f"👑 <b>Admins</b>\n\n{lst}", types.InlineKeyboardMarkup().add(
            IB("🔙 Back", callback_data="m:admin")))
    elif act == "runall":
        bot.answer_callback_query(call.id, "⏳ Starting all ...")
        threading.Thread(target=run_all, args=(cid,), daemon=True).start()
    elif act == "bc":
        bot.answer_callback_query(call.id)
        m = send(cid, "📢 Send the message to broadcast (text / photo / video / file ...).\n/cancel to abort.")
        if m:
            bot.register_next_step_handler(m, broadcast_input)


def parse_uid(txt):
    return int(txt.split()[0])


def admin_input(message, act):
    uid, cid = message.from_user.id, message.chat.id
    if uid not in admin_ids:
        return
    txt = (message.text or "").strip()
    if txt.startswith("/cancel"):
        send(cid, "Cancelled.")
        return
    try:
        if act == "subadd":
            t, d = txt.split()
            t, d = int(t), int(d)
            new = extend_premium(t, d)
            send(cid, f"✅ <code>{t}</code> is Premium until <b>{new:%Y-%m-%d}</b>")
            send(t, f"🎉 <b>Premium activated</b> (+{d} days)\nExpires: {new:%Y-%m-%d}")
        elif act == "subdel":
            t = parse_uid(txt)
            drop_subscription(t)
            send(cid, f"✅ Premium removed from <code>{t}</code>")
        elif act == "subchk":
            t = parse_uid(txt)
            if is_premium(t):
                e = user_subscriptions[t]["expiry"]
                send(cid, f"💎 <code>{t}</code> active until <b>{e:%Y-%m-%d %H:%M}</b>")
            else:
                send(cid, f"🆓 <code>{t}</code> has no active premium. Plan: {TIER_LABEL[tier(t)]}")
        elif act == "gen":
            d, n = txt.split()
            d, n = int(d), min(int(n), 20)
            codes = []
            for _ in range(n):
                c = f"CHX-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
                db("INSERT INTO codes (code, days) VALUES (?,?)", (c, d))
                codes.append(c)
            send(cid, f"🎟 <b>{n} code(s) — {d} days each</b>\n\n" + "\n".join(f"<code>{c}</code>" for c in codes))
        elif act == "ban":
            parts = txt.split(None, 1)
            t = int(parts[0])
            if t in admin_ids:
                send(cid, "⚠️ You can't ban an admin.")
                return
            reason = parts[1] if len(parts) > 1 else "No reason"
            banned[t] = reason
            db("INSERT OR REPLACE INTO banned VALUES (?,?)", (t, reason))
            for n, _ in list(user_files.get(t, [])):
                stop_script(t, n, manual=True)
            send(cid, f"🚫 <code>{t}</code> banned. Their scripts were stopped.")
        elif act == "unban":
            t = parse_uid(txt)
            banned.pop(t, None)
            db("DELETE FROM banned WHERE user_id=?", (t,))
            send(cid, f"✅ <code>{t}</code> unbanned.")
        elif act in ("setbkash", "setnagad"):
            if uid != OWNER_ID:
                send(cid, "⚠️ Owner only.")
                return
            key = act[3:]
            label = "bKash" if key == "bkash" else "Nagad"
            if txt.lower() in ("off", "remove"):
                set_setting(key, "off")
                load_pay_settings()
                send(cid, f"✅ {label} disabled.")
                return
            num = txt.replace(" ", "")
            if not re.fullmatch(r"\+?\d{10,15}", num):
                send(cid, "⚠️ Invalid number. Use digits only, e.g. <code>01712345678</code>.")
                return
            set_setting(key, num)
            load_pay_settings()
            send(cid, f"✅ {label} number set: <code>{h(num)}</code>")
        elif act == "setplan":
            if uid != OWNER_ID:
                send(cid, "⚠️ Owner only.")
                return
            d, pr = txt.split()
            d, pr = int(d), int(pr)
            if d <= 0 or pr <= 0 or d > 3650:
                raise ValueError
            PLANS[d] = pr
            save_plans()
            send(cid, f"✅ Plan saved: <b>{d} days — {CURRENCY}{pr}</b>")
        elif act == "delplan":
            if uid != OWNER_ID:
                send(cid, "⚠️ Owner only.")
                return
            d = int(txt.split()[0])
            if d not in PLANS:
                send(cid, "⚠️ No such plan.")
                return
            PLANS.pop(d)
            save_plans()
            send(cid, f"✅ {d}-day plan removed.")
        elif act == "addadm":
            if uid != OWNER_ID:
                return
            t = parse_uid(txt)
            admin_ids.add(t)
            db("INSERT OR IGNORE INTO admins VALUES (?)", (t,))
            send(cid, f"✅ <code>{t}</code> is now Admin.")
            send(t, "🛡 You are now an <b>Admin</b>.")
        elif act == "rmadm":
            if uid != OWNER_ID:
                return
            t = parse_uid(txt)
            if t == OWNER_ID:
                send(cid, "⚠️ Owner can't be removed.")
                return
            admin_ids.discard(t)
            db("DELETE FROM admins WHERE user_id=?", (t,))
            send(cid, f"✅ <code>{t}</code> removed from Admins.")
    except (ValueError, IndexError):
        send(cid, "⚠️ Invalid format. Try again from the Admin Panel.")
    except Exception as e:
        logger.error(f"admin_input error: {e}", exc_info=True)
        send(cid, f"❌ Error: <code>{h(e)}</code>")


def run_all(cid):
    started = skipped = 0
    for uid, files in list(user_files.items()):
        if uid in banned or file_limit(uid) == 0:
            continue
        for name, _ in list(files):
            key = script_key(uid, name)
            if is_running(uid, name) or key in manual_stopped:
                continue
            if start_script(uid, name, cid):
                started += 1
            else:
                skipped += 1
            time.sleep(0.7)
    send(cid, f"✅ <b>Done</b>\n▶️ Started: {started}\n⚠️ Failed: {skipped}")


# =====================  BROADCAST  =====================
def broadcast_input(message):
    uid = message.from_user.id
    if uid not in admin_ids:
        return
    if message.text and message.text.startswith("/cancel"):
        send(message.chat.id, "Cancelled.")
        return
    pending_bc[uid] = (message.chat.id, message.message_id)
    m = types.InlineKeyboardMarkup().add(
        IB("✅ Send", callback_data="bc:go"),
        IB("❌ Cancel", callback_data="bc:no"))
    bot.reply_to(message, f"📢 Broadcast this to <b>{len(active_users)}</b> users?", reply_markup=m)


def broadcast_cb(call, act, uid, cid, mid):
    if uid not in admin_ids:
        return
    src = pending_bc.pop(uid, None)
    if act == "no" or not src:
        bot.answer_callback_query(call.id, "Cancelled")
        edit(cid, mid, "❌ Broadcast cancelled.")
        return
    bot.answer_callback_query(call.id, "🚀 Sending ...")
    edit(cid, mid, f"📢 Broadcasting to {len(active_users)} users ...")
    threading.Thread(target=execute_broadcast, args=(src[0], src[1], cid), daemon=True).start()


def execute_broadcast(from_chat, msg_id, admin_chat):
    ok = fail = 0
    t0 = time.time()
    for i, target in enumerate(list(active_users)):
        if target in banned:
            continue
        try:
            bot.copy_message(target, from_chat, msg_id)
            ok += 1
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                time.sleep(int(getattr(e, "result_json", {}).get("parameters", {}).get("retry_after", 5)) + 1)
                try:
                    bot.copy_message(target, from_chat, msg_id)
                    ok += 1
                    continue
                except Exception:
                    pass
            fail += 1
        except Exception:
            fail += 1
        time.sleep(0.05 if i % 25 else 1.0)
    send(admin_chat, f"📢 <b>Broadcast complete</b>\n✅ Sent: {ok}\n❌ Failed: {fail}\n"
                     f"⏱ {round(time.time() - t0, 1)}s")


# =====================  PAID PLANS  =====================
def _parse_plans():
    out = {}
    for part in os.environ.get("PLANS", "3:30,7:60,15:110,30:200").split(","):
        try:
            d, pr = part.strip().split(":")
            out[int(d)] = int(pr)
        except ValueError:
            pass
    return out


PLANS = _parse_plans()
CURRENCY = os.environ.get("CURRENCY", "৳")
PAY_METHODS = {}


def get_setting(key, default=""):
    rows = db("SELECT value FROM settings WHERE key=?", (key,), fetch=True)
    return rows[0][0] if rows else default


def set_setting(key, value):
    db("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))


def save_plans():
    set_setting("plans", json.dumps({str(k): v for k, v in PLANS.items()}))


def load_pay_settings():
    """Loads plans + payment numbers saved from the admin panel (falls back to env)."""
    raw = get_setting("plans", "")
    if raw:
        try:
            PLANS.clear()
            PLANS.update({int(k): int(v) for k, v in json.loads(raw).items()})
        except Exception as e:
            logger.error(f"plans load error: {e}")
    PAY_METHODS.clear()
    for key, label, env_name in (("bkash", "bKash", "BKASH_NUMBER"), ("nagad", "Nagad", "NAGAD_NUMBER")):
        num = get_setting(key, os.environ.get(env_name, ""))
        if num and num.lower() != "off" and "X" not in num.upper():
            PAY_METHODS[key] = (label, num)


def pay_settings_text():
    lines = ["💳 <b>Payment Settings</b>\n"]
    for key, label in (("bkash", "bKash"), ("nagad", "Nagad")):
        if key in PAY_METHODS:
            lines.append(f"• {label}: <code>{h(PAY_METHODS[key][1])}</code>")
        else:
            lines.append(f"• {label}: ❌ not set")
    lines.append("\n📦 <b>Plans</b>")
    if PLANS:
        lines += [f"• {d} days — {CURRENCY}{pr}" for d, pr in sorted(PLANS.items())]
    else:
        lines.append("• ❌ no plans")
    return "\n".join(lines)


load_pay_settings()


def plans_text():
    lines = ["💎 <b>Premium Hosting Plans</b>\n"]
    for d, pr in sorted(PLANS.items()):
        lines.append(f"• <b>{d} days</b> — {CURRENCY}{pr}")
    lines.append(f"\n📁 Up to <b>{LIMITS['premium']}</b> files · ♻️ Auto-restart · 🟢 24/7 hosting")
    lines.append("<i>Tap a plan below to pay.</i>")
    return "\n".join(lines)


def plans_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    for d, pr in sorted(PLANS.items()):
        m.add(IB(f"💎 {d} Days — {CURRENCY}{pr}", callback_data=f"buy:{d}"))
    m.add(IB("🔙 Back", callback_data="m:home"))
    return m


def buy_cb(call, days_s, uid, cid, mid):
    days = int(days_s)
    if days not in PLANS:
        bot.answer_callback_query(call.id, "Plan not found", show_alert=True)
        return
    if not PAY_METHODS:
        bot.answer_callback_query(call.id, "Payment is not available right now. Contact the owner.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(*[IB(f"💳 {label}", callback_data=f"pay:m:{days}:{key}")
            for key, (label, _) in PAY_METHODS.items()])
    m.add(IB("🔙 Plans", callback_data="m:buy"))
    edit(cid, mid, f"💎 <b>{days}-Day Plan</b> — {CURRENCY}{PLANS[days]}\n\nChoose a payment method:", m)


def pay_cb(call, p, uid, cid, mid):
    act = p[1]
    if act == "m":
        days, method = int(p[2]), p[3]
        if days not in PLANS or method not in PAY_METHODS:
            bot.answer_callback_query(call.id, "Invalid option", show_alert=True)
            return
        label, number = PAY_METHODS[method]
        bot.answer_callback_query(call.id)
        m = send(cid, f"💳 <b>{label} Payment</b>\n\n"
                      f"📦 Plan: <b>{days} days</b>\n"
                      f"💰 Amount: <b>{CURRENCY}{PLANS[days]}</b>\n"
                      f"📲 Send Money to: <code>{h(number)}</code>\n\n"
                      f"1️⃣ Send the exact amount\n"
                      f"2️⃣ Reply here with your <b>Transaction ID</b> "
                      f"(a screenshot with the TrxID as caption also works)\n\n/cancel to abort.")
        if m:
            bot.register_next_step_handler(m, process_trx, days, method)
    elif act in ("ok", "no"):
        if uid not in admin_ids:
            bot.answer_callback_query(call.id, "⚠️ Admins only", show_alert=True)
            return
        review_payment(call, act, int(p[2]), uid, cid, mid)


def process_trx(message, days, method):
    uid, cid = message.from_user.id, message.chat.id
    if uid in banned:
        return
    txt = (message.text or message.caption or "").strip()
    if txt.startswith("/cancel"):
        send(cid, "Cancelled.")
        return
    trx = txt.upper()
    if not re.fullmatch(r"[A-Z0-9]{6,20}", trx):
        send(cid, "⚠️ Invalid Transaction ID. Open <b>💎 Buy Plan</b> and try again.")
        return
    if db("SELECT 1 FROM payments WHERE trx_id=?", (trx,), fetch=True):
        send(cid, "⚠️ This Transaction ID was already submitted.")
        return
    pid = int(time.time() * 1000)
    db("INSERT INTO payments (id, user_id, plan_days, amount, method, trx_id, status, created) "
       "VALUES (?,?,?,?,?,?,?,?)",
       (pid, uid, days, PLANS[days], method, trx, "pending", datetime.now().isoformat()))
    send(cid, "✅ <b>Payment request submitted.</b>\nAn admin will verify it shortly — "
              "your plan activates automatically after approval.")
    kb = types.InlineKeyboardMarkup().add(
        IB("✅ Approve", callback_data=f"pay:ok:{pid}"),
        IB("❌ Reject", callback_data=f"pay:no:{pid}"))
    info = (f"💰 <b>New payment request</b>\n\n"
            f"👤 {h(message.from_user.first_name)} (<code>{uid}</code>)\n"
            f"📦 Plan: <b>{days} days</b>\n"
            f"💵 Amount: <b>{CURRENCY}{PLANS[days]}</b>\n"
            f"💳 Method: <b>{PAY_METHODS[method][0]}</b>\n"
            f"🧾 TrxID: <code>{trx}</code>")
    for a in list(admin_ids):
        try:
            if message.content_type == "photo":
                bot.forward_message(a, cid, message.message_id)
            send(a, info, kb)
        except Exception as e:
            logger.error(f"payment notify error: {e}")


def review_payment(call, act, pid, uid, cid, mid):
    row = db("SELECT user_id, plan_days, amount, status FROM payments WHERE id=?", (pid,), fetch=True)
    if not row:
        bot.answer_callback_query(call.id, "Payment not found", show_alert=True)
        return
    buyer, days, amount, status = row[0]
    if status != "pending":
        bot.answer_callback_query(call.id, f"Already {status}", show_alert=True)
        return
    if act == "ok":
        db("UPDATE payments SET status='approved' WHERE id=?", (pid,))
        new = extend_premium(buyer, days)
        send(buyer, f"🎉 <b>Payment approved!</b>\n💎 {days}-day plan activated.\n"
                    f"Expires: <b>{new:%Y-%m-%d %H:%M}</b>\n\nSend your file to start hosting.")
        note = f"✅ <b>Approved</b> by <code>{uid}</code>"
        bot.answer_callback_query(call.id, "Approved")
    else:
        db("UPDATE payments SET status='rejected' WHERE id=?", (pid,))
        send(buyer, "❌ <b>Your payment was rejected.</b>\nIf you think this is a mistake, contact the owner.",
             types.InlineKeyboardMarkup().add(IB(
                 "📞 Contact Owner", url=f"https://t.me/{YOUR_USERNAME.lstrip('@')}")))
        note = f"❌ <b>Rejected</b> by <code>{uid}</code>"
        bot.answer_callback_query(call.id, "Rejected")
    edit(cid, mid, call.message.html_text + "\n\n" + note)


# =====================  WATCHDOG (auto-restart + expiry)  =====================
def watchdog():
    restart_log = {}
    while True:
        time.sleep(20)
        try:
            now = datetime.now()
            for uid, sub in list(user_subscriptions.items()):
                if sub["expiry"] <= now:
                    drop_subscription(uid)
                    if file_limit(uid) == 0:
                        for n, _ in list(user_files.get(uid, [])):
                            stop_script(uid, n, manual=True)
                    send(uid, "⌛ Your 💎 <b>plan has expired</b> and your scripts were stopped.\nRenew to host again.",
                         plans_markup())
            for uid, name in list(autorestart):
                key = script_key(uid, name)
                if uid in banned or key in manual_stopped:
                    continue
                if tier(uid) == "free" or not any(n == name for n, _ in user_files.get(uid, [])):
                    continue
                if is_running(uid, name):
                    continue
                hist = [t for t in restart_log.get(key, []) if (now - t).total_seconds() < 3600]
                if len(hist) >= 5:
                    continue
                hist.append(now)
                restart_log[key] = hist
                send(uid, f"♻️ <b>Auto-restart</b>: <code>{h(name)}</code> had stopped — restarting ...")
                start_script(uid, name, uid, attempt=2)
        except Exception as e:
            logger.error(f"watchdog error: {e}", exc_info=True)


# =====================  CLEANUP & MAIN  =====================
def cleanup():
    for info in list(bot_scripts.values()):
        kill_tree(info)


atexit.register(cleanup)

if __name__ == "__main__":
    logger.info(f"{BOT_NAME} PREMIUM starting | owner={OWNER_ID} | admins={admin_ids}")
    keep_alive()
    Thread(target=watchdog, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30, skip_pending=True)
        except requests.exceptions.ReadTimeout:
            time.sleep(5)
        except requests.exceptions.ConnectionError:
            time.sleep(15)
        except Exception as e:
            logger.critical(f"Polling crashed: {e}", exc_info=True)
            time.sleep(30)
