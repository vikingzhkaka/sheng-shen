#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rosebud Cloud —— 多用户版复盘工具（仅 Python 标准库 + SQLite）

能力：
- 邮箱+密码注册（需邀请码）、登录、会话（Bearer token，30 天）
- 按账户存档（每条记录绑定 user_id，任何人只能读写自己的）
- 按天导出 md / 按日期区间导出 zip
- AI 教练对话代理（主模型 deepseek-v4-flash，辅助 sensenova-6.7-flash-lite，
  由 SenseNova 网关 https://token.sensenova.cn/v1 统一提供，key 同一把）

安全约定（部署时必须遵守）：
- AI key 只从环境变量 SENSENOVA_API_KEY 读取；不要写进任何文件或仓库
- 密码用 hashlib.scrypt 哈希（随机盐），不存明文
- 会话 token 随机 32 字节，数据库只存 sha256
- 所有数据按 user_id 隔离；未登录一律 401

本地启动:   python3 server.py            → http://localhost:8732
环境变量:   SENSENOVA_API_KEY, ADMIN_EMAIL, ADMIN_PASSWORD, INVITE_CODES, PORT, DB_PATH
"""
import http.server
import io
import json
import os
import re
import secrets
import sqlite3
import hashlib
import hmac
import urllib.request
import urllib.error
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
DB_PATH = Path(os.environ.get("DB_PATH", str(BASE / "data" / "app.db")))
SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DEFAULT_MODELS = [
    {"name": "DeepSeek V4 Flash（主）", "base_url": "https://token.sensenova.cn/v1",
     "model": "deepseek-v4-flash", "api_key": "", "primary": True},
    {"name": "SenseNova 6.7-Flash-Lite（辅）", "base_url": "https://token.sensenova.cn/v1",
     "model": "sensenova-6.7-flash-lite", "api_key": "", "primary": False},
]


# ---------- 配置 ----------
def load_config():
    cfg = {"PORT": int(os.environ.get("PORT", "8732")),
           "MODELS": [dict(m) for m in DEFAULT_MODELS]}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw.get("MODELS"), list) and raw["MODELS"]:
                cfg["MODELS"] = raw["MODELS"]
            elif raw.get("LLM_API_KEY"):
                cfg["MODELS"] = [{"name": "Default",
                                  "base_url": raw.get("LLM_BASE_URL", "https://token.sensenova.cn/v1"),
                                  "model": raw.get("LLM_MODEL", "deepseek-v4-flash"),
                                  "api_key": raw["LLM_API_KEY"], "primary": True}]
        except Exception as e:  # noqa
            print("[warn] config.json 解析失败:", e)
    env_key = os.environ.get("SENSENOVA_API_KEY")
    for m in cfg["MODELS"]:
        if env_key:
            m["api_key"] = env_key
    return cfg


CONFIG = load_config()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------- 数据库 ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL,
      pass_hash TEXT NOT NULL,
      is_admin INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS sessions(
      token_hash TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL,
      created_at TEXT DEFAULT (datetime('now','localtime')),
      expires_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS invites(
      code TEXT PRIMARY KEY,
      created_at TEXT DEFAULT (datetime('now','localtime')),
      used_by INTEGER,
      used_at TEXT
    );
    CREATE TABLE IF NOT EXISTS entries(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      day TEXT NOT NULL,
      title TEXT, emoji TEXT,
      emotions TEXT, people TEXT, topics TEXT,
      reflection TEXT, transcript TEXT,
      created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_entries_user_day ON entries(user_id, day);
    """)
    c.commit()
    c.close()
    seed_admin()
    seed_invites()


def seed_admin():
    email = os.environ.get("ADMIN_EMAIL")
    pw = os.environ.get("ADMIN_PASSWORD")
    if email and pw:
        c = db()
        if not c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            c.execute("INSERT INTO users(email,pass_hash,is_admin) VALUES(?,?,1)",
                      (email, hash_password(pw)))
            c.commit()
        c.close()


def seed_invites():
    codes = [x.strip() for x in os.environ.get("INVITE_CODES", "").split(",") if x.strip()]
    if codes:
        c = db()
        for code in codes:
            c.execute("INSERT OR IGNORE INTO invites(code) VALUES(?)", (code,))
        c.commit()
        c.close()


# ---------- 密码 / 会话 ----------
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + h.hex()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_hex, hash_hex = stored.split("$")
        h = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                           n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h.hex(), hash_hex)
    except Exception:
        return False


def new_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    th = hashlib.sha256(token.encode()).hexdigest()
    exp = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    c = db()
    c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
              (th, user_id, exp))
    c.commit()
    c.close()
    return token


def user_by_token(token):
    if not token:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    c = db()
    row = c.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token_hash=? AND s.expires_at>datetime('now','localtime')",
        (th,)).fetchone()
    c.close()
    return dict(row) if row else None


def revoke_session(token):
    th = hashlib.sha256(token.encode()).hexdigest()
    c = db()
    c.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
    c.commit()
    c.close()


# ---------- 模型 / AI 代理 ----------
def active_model(name=None):
    models = CONFIG["MODELS"]
    if not models:
        return None
    if name:
        for m in models:
            if m.get("model") == name or m.get("name") == name:
                return m
    for m in models:
        if m.get("primary"):
            return m
    return models[0]


# ---------- 导出 ----------
def entry_to_md(e) -> str:
    lines = [f"## {e['emoji'] or '🌹'} {e['title'] or '复盘'}", f"### {e['created_at']}", "",
             "#### Tags",
             f"**Emotions:** {e['emotions'] or ''}",
             f"**People:** {e['people'] or ''}",
             f"**Topics:** {e['topics'] or ''}", "",
             "#### Reflection", e["reflection"] or ""]
    if e["transcript"]:
        lines += ["", "#### Entry", e["transcript"]]
    return "\n".join(lines)


def day_md(day: str, entries) -> str:
    parts = [f"---\ndate: {day}\ntype: rosebud-daily\ntags: [复盘日记]\n---\n",
             f"# 🌹 Rosebud · {day}\n"]
    parts.append("\n\n---\n\n".join(entry_to_md(e) for e in entries))
    return "\n".join(parts)


# ---------- HTTP ----------
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    # ---- helpers ----
    def _send(self, obj, status=200, ctype="application/json; charset=utf-8", raw=None):
        if raw is not None:
            data = raw
        else:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def _token(self):
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            return h[7:].strip()
        return None

    def _require_user(self):
        user = user_by_token(self._token())
        if not user:
            self._send({"error": "未登录或会话已过期"}, 401)
            return None
        return user

    def _require_admin(self):
        user = self._require_user()
        if not user:
            return None
        if not user.get("is_admin"):
            self._send({"error": "需要管理员权限"}, 403)
            return None
        return user

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    # ---- GET ----
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._serve_index()
        elif p == "/api/config":
            self._api_config()
        elif p == "/api/me":
            self._api_me()
        elif p == "/api/entries":
            self._api_entries()
        elif p == "/api/export":
            self._api_export()
        elif p == "/api/invites":
            self._api_invites()
        else:
            self._send({"error": "not found"}, 404)

    def do_DELETE(self):
        p = self.path.split("?")[0]
        if re.match(r"^/api/entries/\d+$", p):
            self._api_delete_entry(p)
        else:
            self._send({"error": "not found"}, 404)

    def _serve_index(self):
        f = BASE / "index.html"
        if not f.exists():
            self._send({"error": "index.html missing"}, 500)
            return
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api_config(self):
        active = active_model()
        self._send({
            "mode": "cloud",
            "active": active["model"] if active else None,
            "models": [{"name": m.get("name"), "model": m.get("model"),
                        "has_key": bool(m.get("api_key"))} for m in CONFIG["MODELS"]],
        })

    def _api_me(self):
        user = self._require_user()
        if not user:
            return
        c = db()
        n = c.execute("SELECT COUNT(*) FROM entries WHERE user_id=?", (user["id"],)).fetchone()[0]
        c.close()
        self._send({"email": user["email"], "is_admin": bool(user["is_admin"]),
                    "entry_count": n})

    def _api_entries(self):
        user = self._require_user()
        if not user:
            return
        c = db()
        rows = c.execute(
            "SELECT id, day, title, emoji, reflection, created_at FROM entries "
            "WHERE user_id=? ORDER BY day DESC, created_at DESC", (user["id"],)).fetchall()
        c.close()
        self._send({"entries": [dict(r) for r in rows]})

    def _api_export(self):
        user = self._require_user()
        if not user:
            return
        q = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    q[k] = v
        c = db()
        if q.get("day"):
            day = q["day"]
            if not DAY_RE.match(day):
                self._send({"error": "day 格式应为 YYYY-MM-DD"}, 400)
                return
            rows = c.execute("SELECT * FROM entries WHERE user_id=? AND day=? "
                             "ORDER BY created_at", (user["id"], day)).fetchall()
            c.close()
            md = day_md(day, [dict(r) for r in rows])
            data = md.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{day}.md"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        elif q.get("from") and q.get("to"):
            frm, to = q["from"], q["to"]
            if not (DAY_RE.match(frm) and DAY_RE.match(to)):
                self._send({"error": "from/to 格式应为 YYYY-MM-DD"}, 400)
                return
            rows = c.execute("SELECT * FROM entries WHERE user_id=? AND day BETWEEN ? AND ? "
                             "ORDER BY day, created_at", (user["id"], frm, to)).fetchall()
            c.close()
            by_day = {}
            for r in rows:
                by_day.setdefault(r["day"], []).append(dict(r))
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for day in sorted(by_day):
                    z.writestr(f"{day}.md", day_md(day, by_day[day]))
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send({"error": "需要 day=YYYY-MM-DD 或 from=..&to=.."}, 400)

    def _api_invites(self):
        admin = self._require_admin()
        if not admin:
            return
        c = db()
        rows = c.execute("""SELECT i.code, i.created_at, i.used_at, u.email AS used_by
                            FROM invites i LEFT JOIN users u ON u.id=i.used_by
                            ORDER BY i.created_at DESC""").fetchall()
        c.close()
        self._send({"invites": [dict(r) for r in rows]})

    def _api_delete_entry(self, p):
        user = self._require_user()
        if not user:
            return
        eid = int(p.rsplit("/", 1)[1])
        c = db()
        cur = c.execute("DELETE FROM entries WHERE id=? AND user_id=?", (eid, user["id"]))
        c.commit()
        c.close()
        self._send({"ok": cur.rowcount > 0})

    # ---- POST ----
    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/register":
            self._api_register()
        elif p == "/api/login":
            self._api_login()
        elif p == "/api/logout":
            self._api_logout()
        elif p == "/api/chat":
            self._api_chat()
        elif p == "/api/save":
            self._api_save()
        elif p == "/api/invite":
            self._api_invite_new()
        else:
            self._send({"error": "not found"}, 404)

    def _api_register(self):
        b = self._json()
        email = str(b.get("email", "")).strip().lower()
        pw = str(b.get("password", ""))
        code = str(b.get("invite", "")).strip()
        if not EMAIL_RE.match(email):
            self._send({"error": "邮箱格式不正确"}, 400)
            return
        if len(pw) < 6:
            self._send({"error": "密码至少 6 位"}, 400)
            return
        c = db()
        inv = c.execute("SELECT * FROM invites WHERE code=? AND used_by IS NULL", (code,)).fetchone()
        if not inv:
            c.close()
            self._send({"error": "邀请码无效或已被使用"}, 400)
            return
        if c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            c.close()
            self._send({"error": "该邮箱已注册"}, 409)
            return
        # 第一个注册用户自动成为管理员（便于自举，部署时建议用 ADMIN_EMAIL 显式指定）
        has_admin = c.execute("SELECT 1 FROM users WHERE is_admin=1 LIMIT 1").fetchone()
        is_admin = 1 if not has_admin else 0
        cur = c.execute("INSERT INTO users(email,pass_hash,is_admin) VALUES(?,?,?)",
                        (email, hash_password(pw), is_admin))
        uid = cur.lastrowid
        c.execute("UPDATE invites SET used_by=?, used_at=datetime('now','localtime') WHERE code=?",
                  (uid, code))
        c.commit()
        c.close()
        self._send({"ok": True, "token": new_session(uid), "email": email,
                    "is_admin": bool(is_admin)}, 201)

    def _api_login(self):
        b = self._json()
        email = str(b.get("email", "")).strip().lower()
        pw = str(b.get("password", ""))
        c = db()
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        c.close()
        if not row or not verify_password(pw, row["pass_hash"]):
            self._send({"error": "邮箱或密码错误"}, 401)
            return
        self._send({"ok": True, "token": new_session(row["id"]),
                    "email": row["email"], "is_admin": bool(row["is_admin"])})

    def _api_logout(self):
        tok = self._token()
        if tok:
            revoke_session(tok)
        self._send({"ok": True})

    def _api_chat(self):
        user = self._require_user()
        if not user:
            return
        b = self._json()
        messages = b.get("messages")
        if not isinstance(messages, list) or not messages:
            self._send({"error": "messages 缺失"}, 400)
            return
        m = active_model(b.get("model"))
        if not m:
            self._send({"error": "未配置任何模型"}, 400)
            return
        if not m.get("api_key"):
            self._send({"error": "服务端未配置 SENSENOVA_API_KEY"}, 500)
            return
        url = m["base_url"].rstrip("/") + "/chat/completions"
        payload = {"model": m["model"], "messages": messages,
                   "temperature": 0.8, "stream": False}
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + m["api_key"]})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            self._send({"content": content, "model": m["model"]})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            self._send({"error": f"LLM 返回 {e.code}: {detail}"}, e.code)
        except Exception as e:  # noqa
            self._send({"error": str(e)}, 500)

    def _api_save(self):
        user = self._require_user()
        if not user:
            return
        b = self._json()
        day = str(b.get("day", ""))
        if not DAY_RE.match(day):
            self._send({"error": "day 格式应为 YYYY-MM-DD"}, 400)
            return
        c = db()
        cur = c.execute(
            "INSERT INTO entries(user_id,day,title,emoji,emotions,people,topics,reflection,transcript) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (user["id"], day, str(b.get("title", "")), str(b.get("emoji", "")),
             json.dumps(b.get("emotions", []), ensure_ascii=False),
             json.dumps(b.get("people", []), ensure_ascii=False),
             json.dumps(b.get("topics", []), ensure_ascii=False),
             str(b.get("reflection", "")), str(b.get("transcript", ""))))
        c.commit()
        c.close()
        self._send({"ok": True, "id": cur.lastrowid, "day": day}, 201)

    def _api_invite_new(self):
        admin = self._require_admin()
        if not admin:
            return
        b = self._json()
        n = min(max(int(b.get("count", 1)), 1), 20)
        c = db()
        codes = []
        for _ in range(n):
            code = secrets.token_urlsafe(6).upper()
            c.execute("INSERT OR IGNORE INTO invites(code) VALUES(?)", (code,))
            codes.append(code)
        c.commit()
        c.close()
        self._send({"ok": True, "codes": codes})


def main():
    init_db()
    if not any(m.get("api_key") for m in CONFIG["MODELS"]):
        print("[提示] 未检测到 SENSENOVA_API_KEY，AI 教练不可用（注册/存档/导出不受影响）。")
    active = active_model()
    print(f"[Rosebud Cloud] http://localhost:{CONFIG['PORT']}  DB={DB_PATH}")
    if active:
        print(f"[Rosebud Cloud] 主模型: {active['name']} ({active['model']})")
    try:
        http.server.ThreadingHTTPServer(("0.0.0.0", CONFIG["PORT"]), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n[Rosebud Cloud] 已停止。")


if __name__ == "__main__":
    main()
