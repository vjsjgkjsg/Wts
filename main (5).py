import os
import httpx
import sqlite3
import hashlib
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

BOT_TOKEN = os.getenv("BOT_TOKEN", "8274782796:AAFBK4sJpQhtXnIE9IxOMmNhivlM2dXEgp4")
API_ID    = int(os.getenv("API_ID", "26508724"))
API_HASH  = os.getenv("API_HASH", "2ada38c67ea946fe3be7fdd8e2507366")
# Telegram IDs которые автоматически получают роль admin
def parse_ids(env_var):
    val = os.getenv(env_var, "").strip()
    if not val:
        return []
    try:
        return [int(x.strip()) for x in val.split(",") if x.strip().isdigit()]
    except:
        return []

ADMIN_IDS = parse_ids("ADMIN_IDS")
MOD_IDS   = parse_ids("MOD_IDS")
print(f"ADMIN_IDS loaded: {ADMIN_IDS}")

TG = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = "/tmp/wts.db"

# ── Telethon ──
client = TelegramClient("wts_session", API_ID, API_HASH)

@app.on_event("startup")
async def startup():
    init_db()
    init_groups_db()
    await client.start(bot_token=BOT_TOKEN)

@app.on_event("shutdown")
async def shutdown():
    await client.disconnect()

# ── DATABASE ──
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        tg_id     INTEGER PRIMARY KEY,
        name      TEXT,
        username  TEXT,
        photo     TEXT,
        role      TEXT DEFAULT 'user',
        pin_hash  TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login DATETIME
    )''')
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

# ── MODELS ──
class RegisterRequest(BaseModel):
    tg_id:    int
    name:     str
    username: str
    photo:    str = None
    pin:      str

class LoginRequest(BaseModel):
    tg_id: int
    pin:   str

class ChangePinRequest(BaseModel):
    tg_id:   int
    old_pin: str
    new_pin: str

# ── ROUTES ──

@app.get("/user/{tg_id}")
async def get_user(tg_id: int):
    """Check if user exists and return their info"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT tg_id, name, username, photo, role FROM users WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return {"exists": False}
    # Override role from env config
    role = "admin" if tg_id in ADMIN_IDS else ("mod" if tg_id in MOD_IDS else row[4])
    return {"exists": True, "tg_id": row[0], "name": row[1], "username": row[2], "photo": row[3], "role": role}

@app.post("/register")
async def register(req: RegisterRequest):
    """Register new user with PIN"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT tg_id FROM users WHERE tg_id=?", (req.tg_id,))
    if c.fetchone():
        conn.close()
        return {"ok": False, "error": "Пользователь уже зарегистрирован"}
    role = "admin" if req.tg_id in ADMIN_IDS else ("mod" if req.tg_id in MOD_IDS else "user")
    c.execute(
        "INSERT INTO users (tg_id, name, username, photo, role, pin_hash) VALUES (?,?,?,?,?,?)",
        (req.tg_id, req.name, req.username, req.photo, role, hash_pin(req.pin))
    )
    conn.commit()
    conn.close()
    return {"ok": True, "role": role}

@app.post("/login")
async def login(req: LoginRequest):
    """Verify PIN login"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT tg_id, name, username, photo, role, pin_hash FROM users WHERE tg_id=?", (req.tg_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "Пользователь не найден"}
    if row[5] != hash_pin(req.pin):
        conn.close()
        return {"ok": False, "error": "Неверный PIN"}
    # Update last login
    c.execute("UPDATE users SET last_login=CURRENT_TIMESTAMP WHERE tg_id=?", (req.tg_id,))
    conn.commit()
    conn.close()
    role = "admin" if req.tg_id in ADMIN_IDS else ("mod" if req.tg_id in MOD_IDS else row[4])
    return {"ok": True, "tg_id": row[0], "name": row[1], "username": row[2], "photo": row[3], "role": role}

@app.get("/update-role")
async def update_role(tg_id: int, role: str = None):
    """Force update role - pass role directly or detect from ADMIN_IDS"""
    conn = get_db()
    c = conn.cursor()
    if not role:
        role = "admin" if tg_id in ADMIN_IDS else ("mod" if tg_id in MOD_IDS else "user")
    # Only allow valid roles
    if role not in ["admin", "mod", "user"]:
        conn.close()
        return {"ok": False, "error": "Invalid role"}
    c.execute("UPDATE users SET role=? WHERE tg_id=?", (role, tg_id))
    conn.commit()
    conn.close()
    return {"ok": True, "role": role, "admin_ids": ADMIN_IDS}

@app.post("/change-pin")
async def change_pin(req: ChangePinRequest):
    """Change user PIN"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT pin_hash FROM users WHERE tg_id=?", (req.tg_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "Пользователь не найден"}
    if row[0] != hash_pin(req.old_pin):
        conn.close()
        return {"ok": False, "error": "Неверный текущий PIN"}
    c.execute("UPDATE users SET pin_hash=? WHERE tg_id=?", (hash_pin(req.new_pin), req.tg_id))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/lookup")
async def lookup(username: str):
    username = username.lstrip("@").strip()
    if not username:
        return {"ok": False, "error": "Введи username"}
    try:
        entity = await client.get_entity(f"@{username}")
        first = getattr(entity, "first_name", "") or ""
        last  = getattr(entity, "last_name", "") or ""
        uname = getattr(entity, "username", username) or username
        full_name = f"{first} {last}".strip() or uname
        user_id = entity.id
        photo_b64 = None
        try:
            import base64
            photo_bytes = await client.download_profile_photo(entity, file=bytes)
            if photo_bytes:
                photo_b64 = "data:image/jpeg;base64," + base64.b64encode(photo_bytes).decode()
        except Exception:
            pass
        return {"ok": True, "id": user_id, "name": full_name, "username": f"@{uname}", "photo": photo_b64, "letter": full_name[0].upper() if full_name else "?"}
    except Exception as e:
        err = str(e)
        if "Cannot find" in err or "No user" in err:
            return {"ok": True, "id": None, "name": username, "username": f"@{username}", "photo": None, "letter": username[0].upper(), "note": "private"}
        return {"ok": False, "error": "Пользователь не найден"}

# ── GROUP MODELS ──
class GroupRequest(BaseModel):
    chat_id: int
    title:   str = "Группа"

class GroupRemove(BaseModel):
    chat_id: int

# ── GROUP DB ──
def init_groups_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS groups (
        chat_id   INTEGER PRIMARY KEY,
        title     TEXT,
        added_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

@app.post("/groups/add")
async def add_group(req: GroupRequest):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO groups (chat_id, title) VALUES (?,?)", (req.chat_id, req.title))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/groups/remove")
async def remove_group(req: GroupRemove):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM groups WHERE chat_id=?", (req.chat_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/groups")
async def get_groups():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT chat_id, title FROM groups ORDER BY added_at DESC")
    rows = c.fetchall()
    conn.close()
    return {"ok": True, "groups": [{"chat_id": r[0], "title": r[1]} for r in rows]}

@app.post("/send-to-group")
async def send_to_group(data: dict):
    chat_id  = data.get("chat_id")
    scammer  = data.get("scammer", {})
    if not chat_id:
        return {"ok": False, "error": "chat_id required"}

    threat_map = {"high": "🔴 СКАМЕР", "med": "🟠 ПОДОЗРИТЕЛЬНЫЙ", "low": "🔵 ОСТОРОЖНО"}
    status_map = {"active": "🔴 Активен", "blocked": "🔒 Заблокирован"}

    name = scammer.get("name", "?")
    username = scammer.get("username", "")
    threat = threat_map.get(scammer.get("threat", "high"), "СКАМЕР")
    status = status_map.get(scammer.get("status", "active"), "Активен")
    reason = scammer.get("reason", "")
    msg = "*НОВЫЙ СКАМЕР В БАЗЕ WTS*\n"
    msg += ("─" * 28) + "\n\n"
    msg += "*" + name + "* " + username + "\n"
    msg += "Статус: " + threat + "\n"
    msg += "Аккаунт: " + status + "\n\n"
    msg += "*Причина:*\n_" + reason + "_\n"
    if scammer.get("amount"):
        msg += "\nУщерб: " + str(scammer.get("amount","")) + " " + scammer.get("currency","USDT")
    if scammer.get("victims"):
        msg += "\nЖертв: " + str(scammer.get("victims",""))
    msg += "\n\nWTS Blacklist"

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
        )
        result = r.json()
        if not result.get("ok"):
            return {"ok": False, "error": result.get("description", "Ошибка")}
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}
