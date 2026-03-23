import os
import httpx
import base64
import json
import time
import hashlib
import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

BOT_TOKEN   = os.getenv("BOT_TOKEN", "8274782796:AAFBK4sJpQhtXnIE9IxOMmNhivlM2dXEgp4")
API_ID      = int(os.getenv("API_ID", "26508724"))
API_HASH    = os.getenv("API_HASH", "2ada38c67ea946fe3be7fdd8e2507366")
DATABASE_URL = os.getenv("DATABASE_URL", "")
TG          = f"https://api.telegram.org/bot{BOT_TOKEN}"

def parse_ids(env_var):
    val = os.getenv(env_var, "").strip()
    if not val: return []
    try: return [int(x.strip()) for x in val.split(",") if x.strip().isdigit()]
    except: return []

ADMIN_IDS = parse_ids("ADMIN_IDS")
MOD_IDS   = parse_ids("MOD_IDS")
print(f"ADMIN_IDS loaded: {ADMIN_IDS}")

# Telethon
client = TelegramClient("wts_session", API_ID, API_HASH)

# DB pool
db_pool = None

async def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return db_pool

@app.on_event("startup")
async def startup():
    await get_pool()
    await init_db()
    await client.start(bot_token=BOT_TOKEN)
    print("Started!")

@app.on_event("shutdown")
async def shutdown():
    global db_pool
    if db_pool: await db_pool.close()
    await client.disconnect()

async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id BIGINT PRIMARY KEY,
                name TEXT,
                username TEXT,
                photo TEXT,
                role TEXT DEFAULT 'user',
                pin_hash TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                last_login TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                id BIGSERIAL PRIMARY KEY,
                name TEXT,
                username TEXT,
                letter TEXT,
                photo TEXT,
                threat TEXT DEFAULT 'high',
                status TEXT DEFAULT 'active',
                victims INTEGER DEFAULT 1,
                reason TEXT,
                amount TEXT,
                currency TEXT DEFAULT 'USDT',
                date_str TEXT,
                ts BIGINT,
                proofs TEXT DEFAULT '[]',
                added_by BIGINT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id BIGINT PRIMARY KEY,
                title TEXT,
                added_at TIMESTAMP DEFAULT NOW()
            )
        """)
    print("DB initialized!")

def hash_pin(pin): return hashlib.sha256(pin.encode()).hexdigest()

# ── MODELS ──
class RegisterRequest(BaseModel):
    tg_id: int; name: str; username: str; photo: str = None; pin: str

class LoginRequest(BaseModel):
    tg_id: int; pin: str

class ChangePinRequest(BaseModel):
    tg_id: int; old_pin: str; new_pin: str

class BlacklistEntry(BaseModel):
    name: str; username: str; letter: str = "?"; photo: str = None
    threat: str = "high"; status: str = "active"; victims: int = 1
    reason: str; amount: str = ""; currency: str = "USDT"
    date_str: str = ""; ts: int = 0; proofs: list = []; added_by: int = None

class GroupRequest(BaseModel):
    chat_id: int; title: str = "Группа"

class GroupRemove(BaseModel):
    chat_id: int

# ── USER ROUTES ──
@app.get("/user/{tg_id}")
async def get_user(tg_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT tg_id, name, username, photo, role FROM users WHERE tg_id=$1", tg_id)
    if not row: return {"exists": False}
    role = "admin" if tg_id in ADMIN_IDS else ("mod" if tg_id in MOD_IDS else row["role"])
    return {"exists": True, "tg_id": row["tg_id"], "name": row["name"], "username": row["username"], "photo": row["photo"], "role": role}

@app.get("/user/by-username/{username}")
async def get_user_by_username(username: str):
    username = username.lstrip("@").strip().lower()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT tg_id, name, username, photo, role FROM users WHERE LOWER(username)=$1 OR LOWER(username)=$2", username, "@"+username)
    if not row: return {"exists": False}
    role = "admin" if row["tg_id"] in ADMIN_IDS else ("mod" if row["tg_id"] in MOD_IDS else row["role"])
    return {"exists": True, "tg_id": row["tg_id"], "name": row["name"], "username": row["username"], "photo": row["photo"], "role": role}

@app.post("/register")
async def register(req: RegisterRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT tg_id FROM users WHERE tg_id=$1", req.tg_id)
        if existing: return {"ok": False, "error": "Уже зарегистрирован"}
        role = "admin" if req.tg_id in ADMIN_IDS else ("mod" if req.tg_id in MOD_IDS else "user")
        await conn.execute("INSERT INTO users (tg_id, name, username, photo, role, pin_hash) VALUES ($1,$2,$3,$4,$5,$6)",
            req.tg_id, req.name, req.username, req.photo, role, hash_pin(req.pin))
    return {"ok": True, "role": role}

@app.post("/login")
async def login(req: LoginRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT tg_id, name, username, photo, role, pin_hash FROM users WHERE tg_id=$1", req.tg_id)
        if not row: return {"ok": False, "error": "Пользователь не найден"}
        if row["pin_hash"] != hash_pin(req.pin): return {"ok": False, "error": "Неверный PIN"}
        await conn.execute("UPDATE users SET last_login=NOW() WHERE tg_id=$1", req.tg_id)
    role = "admin" if req.tg_id in ADMIN_IDS else ("mod" if req.tg_id in MOD_IDS else row["role"])
    return {"ok": True, "tg_id": row["tg_id"], "name": row["name"], "username": row["username"], "photo": row["photo"], "role": role}

@app.post("/change-pin")
async def change_pin(req: ChangePinRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT pin_hash FROM users WHERE tg_id=$1", req.tg_id)
        if not row: return {"ok": False, "error": "Не найден"}
        if row["pin_hash"] != hash_pin(req.old_pin): return {"ok": False, "error": "Неверный текущий PIN"}
        await conn.execute("UPDATE users SET pin_hash=$1 WHERE tg_id=$2", hash_pin(req.new_pin), req.tg_id)
    return {"ok": True}

@app.get("/update-role")
async def update_role(tg_id: int, role: str = None):
    if not role:
        role = "admin" if tg_id in ADMIN_IDS else ("mod" if tg_id in MOD_IDS else "user")
    if role not in ["admin", "mod", "user"]: return {"ok": False, "error": "Invalid role"}
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET role=$1 WHERE tg_id=$2", role, tg_id)
    return {"ok": True, "role": role, "admin_ids": ADMIN_IDS}

@app.get("/users/all")
async def get_all_users():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tg_id, name, username, role FROM users")
    return {"ok": True, "users": [{"tg_id": r["tg_id"], "name": r["name"], "username": r["username"], "role": r["role"]} for r in rows]}

# ── BLACKLIST ROUTES ──
@app.get("/blacklist")
async def get_blacklist():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM blacklist ORDER BY ts DESC")
    result = []
    for row in rows:
        entry = dict(row)
        try: entry["proofs"] = json.loads(entry.get("proofs") or "[]")
        except: entry["proofs"] = []
        entry["date"] = entry.pop("date_str", "")
        result.append(entry)
    return {"ok": True, "blacklist": result}

@app.post("/blacklist/add")
async def add_to_blacklist(entry: BlacklistEntry):
    pool = await get_pool()
    ts = entry.ts or int(time.time()*1000)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO blacklist (name,username,letter,photo,threat,status,victims,reason,amount,currency,date_str,ts,proofs,added_by) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) RETURNING id",
            entry.name, entry.username, entry.letter, entry.photo, entry.threat, entry.status,
            entry.victims, entry.reason, entry.amount, entry.currency, entry.date_str, ts,
            json.dumps(entry.proofs), entry.added_by)
    return {"ok": True, "id": row["id"]}

@app.delete("/blacklist/{entry_id}")
async def delete_blacklist(entry_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM blacklist WHERE id=$1", entry_id)
    return {"ok": True}

@app.put("/blacklist/{entry_id}")
async def update_blacklist(entry_id: int, data: dict):
    allowed = ["reason","amount","threat","status","victims"]
    fields = [f"{k}=${i+1}" for i,(k,v) in enumerate(data.items()) if k in allowed]
    values = [v for k,v in data.items() if k in allowed]
    if not fields: return {"ok": False, "error": "No valid fields"}
    values.append(entry_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE blacklist SET {','.join(fields)} WHERE id=${len(values)}", *values)
    return {"ok": True}

# ── GROUP ROUTES ──
@app.post("/groups/add")
async def add_group(req: GroupRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO groups (chat_id, title) VALUES ($1,$2) ON CONFLICT (chat_id) DO UPDATE SET title=$2", req.chat_id, req.title)
    return {"ok": True}

@app.post("/groups/remove")
async def remove_group(req: GroupRemove):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM groups WHERE chat_id=$1", req.chat_id)
    return {"ok": True}

@app.get("/groups")
async def get_groups():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT chat_id, title FROM groups ORDER BY added_at DESC")
    return {"ok": True, "groups": [{"chat_id": r["chat_id"], "title": r["title"]} for r in rows]}

# ── LOOKUP ──
@app.get("/lookup")
async def lookup(username: str):
    username = username.lstrip("@").strip()
    if not username: return {"ok": False, "error": "Введи username"}
    try:
        entity = await client.get_entity(f"@{username}")
        first = getattr(entity, "first_name", "") or ""
        last  = getattr(entity, "last_name", "") or ""
        uname = getattr(entity, "username", username) or username
        full_name = f"{first} {last}".strip() or uname
        photo_b64 = None
        try:
            photo_bytes = await client.download_profile_photo(entity, file=bytes)
            if photo_bytes:
                photo_b64 = "data:image/jpeg;base64," + base64.b64encode(photo_bytes).decode()
        except: pass
        return {"ok": True, "id": entity.id, "name": full_name, "username": f"@{uname}", "photo": photo_b64, "letter": full_name[0].upper() if full_name else "?"}
    except Exception as e:
        return {"ok": True, "id": None, "name": username, "username": f"@{username}", "photo": None, "letter": username[0].upper(), "note": "private"}

# ── NOTIFY ──
@app.post("/notify-users")
async def notify_users(data: dict):
    scammer = data.get("scammer", {})
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tg_id FROM users")
    name = scammer.get("name","?"); username = scammer.get("username","")
    threat = scammer.get("threat","high"); reason = scammer.get("reason","")
    amount = str(scammer.get("amount","")); currency = scammer.get("currency","USDT")
    emoji = "🔴" if threat=="high" else ("🟠" if threat=="med" else "🔵")
    label = "СКАМЕР" if threat=="high" else ("ПОДОЗРИТЕЛЬНЫЙ" if threat=="med" else "ОСТОРОЖНО")
    msg = emoji+" НОВЫЙ СКАМЕР В БАЗЕ WTS\n\n"
    msg += "Имя: "+name+("  |  "+username if username else "")+"\n"
    msg += "Категория: "+label+"\n\n"+reason
    if amount: msg += "\n\nУщерб: "+amount+" "+currency
    msg += "\n\nWTS Blacklist"
    sent = 0
    async with httpx.AsyncClient(timeout=5) as c:
        for row in rows:
            try:
                await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": row["tg_id"], "text": msg})
                sent += 1
            except: continue
    return {"ok": True, "sent": sent, "total": len(rows)}

# ── SEND TO GROUP ──
@app.post("/send-to-group")
async def send_to_group(data: dict):
    chat_id = data.get("chat_id"); scammer = data.get("scammer", {})
    if not chat_id: return {"ok": False, "error": "chat_id required"}
    tmap = {"high": "СКАМЕР", "med": "ПОДОЗРИТЕЛЬНЫЙ", "low": "ОСТОРОЖНО"}
    smap = {"active": "Активен - мошенничает", "blocked": "Заблокирован в Telegram"}
    name = scammer.get("name","?"); username = scammer.get("username","")
    threat = tmap.get(scammer.get("threat","high"),"СКАМЕР")
    status = smap.get(scammer.get("status","active"),"Активен")
    reason = scammer.get("reason",""); amount = str(scammer.get("amount",""))
    currency = scammer.get("currency","USDT"); victims = scammer.get("victims",0)
    photo = scammer.get("photo",None); proofs = scammer.get("proofs",[])
    date = scammer.get("date","")
    lines = ["ВНИМАНИЕ - СКАМЕР ОБНАРУЖЕН","",
             "Имя: "+name+("  |  "+username if username else ""),
             "Категория: "+threat, "Статус: "+status]
    if date: lines.append("Дата: "+date)
    lines += ["","Причина:", reason]
    if amount: lines += ["","Ущерб: "+amount+" "+currency]
    if victims: lines.append("Жертв: "+str(victims)+" чел.")
    lines += ["","WTS Blacklist - защита вашего сообщества"]
    msg_text = "\n".join(lines)
    tg = f"https://api.telegram.org/bot{BOT_TOKEN}"
    async with httpx.AsyncClient(timeout=30) as c:
        if photo and "base64" in photo:
            try:
                img = base64.b64decode(photo.split(",",1)[1])
                r = await c.post(tg+"/sendPhoto", data={"chat_id":str(chat_id),"caption":msg_text}, files={"photo":("p.jpg",img,"image/jpeg")})
            except: r = await c.post(tg+"/sendMessage", json={"chat_id":chat_id,"text":msg_text})
        else: r = await c.post(tg+"/sendMessage", json={"chat_id":chat_id,"text":msg_text})
        if not r.json().get("ok"): return {"ok": False, "error": r.json().get("description","Ошибка")}
        valid_proofs = [p for p in proofs if p and "base64" in p][:10]
        if valid_proofs:
            await c.post(tg+"/sendMessage", json={"chat_id":chat_id,"text":"Доказательства ("+str(len(valid_proofs))+" скрин.):"})
            for idx,proof in enumerate(valid_proofs):
                try:
                    img = base64.b64decode(proof.split(",",1)[1])
                    await c.post(tg+"/sendPhoto", data={"chat_id":str(chat_id),"caption":"Скрин "+str(idx+1)}, files={"photo":("p.jpg",img,"image/jpeg")})
                except: continue
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok", "admin_ids": ADMIN_IDS}
