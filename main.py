import os
import httpx
import base64
import json
import time
import hashlib
import asyncio
import asyncpg
from typing import Optional, Any, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from telethon import TelegramClient
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsSearch, InputChannel
from starlette.requests import Request as StarletteRequest

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

BOT_TOKEN    = os.getenv("BOT_TOKEN", "8650752955:AAH0IiWv9SNYHTbPcEGitj8oRIsLh-EmCGw")
API_ID       = int(os.getenv("API_ID", "26508724"))
API_HASH     = os.getenv("API_HASH", "2ada38c67ea946fe3be7fdd8e2507366")
DATABASE_URL = os.getenv("DATABASE_URL", "")
TG           = f"https://api.telegram.org/bot{BOT_TOKEN}"


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

# Telethon client
client = TelegramClient("wts_session", API_ID, API_HASH)

# DB pool
db_pool = None


async def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            ssl='require'
        )
    return db_pool


async def _resolve_tg_id(username: str):
    """Try to get Telegram user ID by username. Returns None on any error."""
    try:
        entity = await client.get_entity(f"@{username}")
        return entity.id
    except Exception:
        return None


async def auto_sync_loop():
    """Автоматически синхронизирует имена/ники скамеров каждые 6 часов."""
    await asyncio.sleep(60)  # первый запуск через минуту после старта
    while True:
        try:
            print("⏰ Auto sync: обновляю имена/ники скамеров...")
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, tg_id, username, name FROM blacklist WHERE tg_id IS NOT NULL"
                )
            updated = 0
            errors = 0
            for row in rows:
                tg_id = row["tg_id"]
                if not tg_id:
                    continue
                try:
                    entity = await client.get_entity(int(tg_id))
                    first = getattr(entity, "first_name", "") or ""
                    last  = getattr(entity, "last_name", "") or ""
                    new_name = f"{first} {last}".strip()
                    new_username = f"@{entity.username}" if entity.username else row["username"]
                    if new_name != row["name"] or new_username != row["username"]:
                        async with pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE blacklist SET name=$1, username=$2 WHERE id=$3",
                                new_name, new_username, row["id"]
                            )
                        updated += 1
                        print(f"  ✅ {row['username']} → {new_username} / {new_name}")
                except Exception:
                    errors += 1
                    continue
            print(f"⏰ Auto sync завершён: обновлено {updated}, ошибок {errors}, всего {len(rows)}")
        except Exception as e:
            print(f"⏰ Auto sync error: {e}")
        await asyncio.sleep(6 * 60 * 60)  # следующий запуск через 6 часов


@app.on_event("startup")
async def startup():
    await get_pool()
    await init_db()
    await client.start(bot_token=BOT_TOKEN)
    asyncio.create_task(auto_sync_loop())
    print("Started!")


@app.on_event("shutdown")
async def shutdown():
    global db_pool
    if db_pool:
        await db_pool.close()
    await client.disconnect()


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Users table
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

        # Blacklist table — create with tg_id from the start
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
                added_by BIGINT,
                tg_id BIGINT
            )
        """)

        # ── MIGRATION: add tg_id column if missing (for existing deployments) ──
        col_exists = await conn.fetchval("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name='blacklist' AND column_name='tg_id'
        """)
        if col_exists == 0:
            await conn.execute("ALTER TABLE blacklist ADD COLUMN tg_id BIGINT")
            print("MIGRATION: added tg_id column to blacklist")

        # Groups table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id BIGINT PRIMARY KEY,
                title TEXT,
                hostile BOOLEAN DEFAULT FALSE,
                added_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # Migration: add hostile column if missing
        hostile_col = await conn.fetchval("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name='groups' AND column_name='hostile'
        """)
        if hostile_col == 0:
            await conn.execute("ALTER TABLE groups ADD COLUMN hostile BOOLEAN DEFAULT FALSE")
            print("MIGRATION: added hostile column to groups")

        # Migration: add pinned column to blacklist if missing
        pinned_col = await conn.fetchval("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name='blacklist' AND column_name='pinned'
        """)
        if pinned_col == 0:
            await conn.execute("ALTER TABLE blacklist ADD COLUMN pinned BOOLEAN DEFAULT FALSE")
            print("MIGRATION: added pinned column to blacklist")

        # Moderation log table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS moderation_log (
                id BIGSERIAL PRIMARY KEY,
                action TEXT,
                blacklist_id BIGINT,
                admin_id BIGINT,
                admin_name TEXT,
                target_name TEXT,
                target_username TEXT,
                reason TEXT,
                ts TIMESTAMP DEFAULT NOW()
            )
        """)

        # Appeals table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS appeals (
                id BIGSERIAL PRIMARY KEY,
                blacklist_id BIGINT,
                tg_id BIGINT,
                name TEXT,
                username TEXT,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                ts TIMESTAMP DEFAULT NOW()
            )
        """)

    print("DB initialized!")


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


# ── MODELS ──

class RegisterRequest(BaseModel):
    tg_id: int
    name: str
    username: str
    photo: Optional[str] = None
    pin: str

class LoginRequest(BaseModel):
    tg_id: int
    pin: str

class ChangePinRequest(BaseModel):
    tg_id: int
    old_pin: str
    new_pin: str

class BlacklistEntry(BaseModel):
    id: Optional[int] = None
    name: str
    username: str
    letter: str = "?"
    photo: Optional[Any] = None
    threat: str = "high"
    status: str = "active"
    victims: int = 1
    reason: str
    amount: Optional[Any] = ""
    currency: str = "USDT"
    date_str: str = ""
    date: str = ""
    ts: int = 0
    proofs: List[Any] = []
    added_by: Optional[int] = None
    tg_id: Optional[int] = None
    pinned: bool = False
    is_bought: bool = False

    @field_validator("photo", mode="before")
    @classmethod
    def coerce_photo(cls, v):
        if v is None or v == "" or v == "null":
            return None
        if isinstance(v, str):
            if v.startswith("data:") and len(v) > 800_000:
                return None
            return v
        return None

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, v):
        if v is None:
            return ""
        return str(v)

    @field_validator("proofs", mode="before")
    @classmethod
    def coerce_proofs(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return list(v) if v else []

class GroupRequest(BaseModel):
    chat_id: int
    title: str = "Группа"
    hostile: bool = False
    hostile: bool = False

class GroupRemove(BaseModel):
    chat_id: int


# ── USER ROUTES ──

@app.get("/user/{tg_id}")
async def get_user(tg_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tg_id, name, username, photo, role FROM users WHERE tg_id=$1", tg_id
        )
    if not row:
        return {"exists": False}
    role = "admin" if tg_id in ADMIN_IDS else ("mod" if tg_id in MOD_IDS else row["role"])
    return {
        "exists": True,
        "tg_id": row["tg_id"],
        "name": row["name"],
        "username": row["username"],
        "photo": row["photo"],
        "role": role
    }


@app.get("/user/by-username/{username}")
async def get_user_by_username(username: str):
    username = username.lstrip("@").strip().lower()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tg_id, name, username, photo, role FROM users WHERE LOWER(username)=$1 OR LOWER(username)=$2",
            username, "@" + username
        )
    if not row:
        return {"exists": False}
    role = "admin" if row["tg_id"] in ADMIN_IDS else ("mod" if row["tg_id"] in MOD_IDS else row["role"])
    return {
        "exists": True,
        "tg_id": row["tg_id"],
        "name": row["name"],
        "username": row["username"],
        "photo": row["photo"],
        "role": role
    }


@app.post("/register")
async def register(req: RegisterRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT tg_id FROM users WHERE tg_id=$1", req.tg_id)
        if existing:
            return {"ok": False, "error": "Уже зарегистрирован"}
        role = "admin" if req.tg_id in ADMIN_IDS else ("mod" if req.tg_id in MOD_IDS else "user")
        await conn.execute(
            "INSERT INTO users (tg_id, name, username, photo, role, pin_hash) VALUES ($1,$2,$3,$4,$5,$6)",
            req.tg_id, req.name, req.username, req.photo, role, hash_pin(req.pin)
        )
    return {"ok": True, "role": role}


@app.post("/login")
async def login(req: LoginRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tg_id, name, username, photo, role, pin_hash FROM users WHERE tg_id=$1", req.tg_id
        )
        if not row:
            return {"ok": False, "error": "Пользователь не найден"}
        if row["pin_hash"] != hash_pin(req.pin):
            return {"ok": False, "error": "Неверный PIN"}
        role = "admin" if req.tg_id in ADMIN_IDS else ("mod" if req.tg_id in MOD_IDS else row["role"])
        await conn.execute(
            "UPDATE users SET last_login=NOW(), role=$1 WHERE tg_id=$2", role, req.tg_id
        )
    return {
        "ok": True,
        "tg_id": row["tg_id"],
        "name": row["name"],
        "username": row["username"],
        "photo": row["photo"],
        "role": role
    }


@app.post("/change-pin")
async def change_pin(req: ChangePinRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT pin_hash FROM users WHERE tg_id=$1", req.tg_id)
        if not row:
            return {"ok": False, "error": "Не найден"}
        if row["pin_hash"] != hash_pin(req.old_pin):
            return {"ok": False, "error": "Неверный текущий PIN"}
        await conn.execute(
            "UPDATE users SET pin_hash=$1 WHERE tg_id=$2", hash_pin(req.new_pin), req.tg_id
        )
    return {"ok": True}


@app.get("/update-role")
async def update_role(tg_id: int, role: str = None):
    if not role:
        role = "admin" if tg_id in ADMIN_IDS else ("mod" if tg_id in MOD_IDS else "user")
    if role not in ["admin", "mod", "user"]:
        return {"ok": False, "error": "Invalid role"}
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET role=$1 WHERE tg_id=$2", role, tg_id)
    return {"ok": True, "role": role, "admin_ids": ADMIN_IDS}


@app.get("/users/all")
async def get_all_users():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tg_id, name, username, role FROM users")
    return {
        "ok": True,
        "users": [{"tg_id": r["tg_id"], "name": r["name"], "username": r["username"], "role": r["role"]} for r in rows]
    }


# ── BLACKLIST ROUTES ──

@app.get("/blacklist")
async def get_blacklist():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM blacklist ORDER BY ts DESC")
    result = []
    for row in rows:
        entry = dict(row)
        try:
            entry["proofs"] = json.loads(entry.get("proofs") or "[]")
        except:
            entry["proofs"] = []
        entry["date"] = entry.pop("date_str", "")
        result.append(entry)
    return {"ok": True, "blacklist": result}


@app.post("/blacklist/add")
async def add_to_blacklist(entry: BlacklistEntry):
    import asyncio
    pool = await get_pool()
    ts = entry.ts or int(time.time() * 1000)
    date_str = entry.date_str or entry.date or ""

    # Try to resolve TG ID via Telethon — with timeout so it never hangs
    tg_id_val = entry.tg_id  # use client-supplied value if present
    if not tg_id_val:
        try:
            uname = entry.username.lstrip("@").strip()
            if uname and uname not in ("unknown", ""):
                tg_id_val = await asyncio.wait_for(_resolve_tg_id(uname), timeout=4.0)
        except Exception as e:
            print(f"TG ID resolve failed: {e}")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO blacklist
               (name, username, letter, photo, threat, status, victims,
                reason, amount, currency, date_str, ts, proofs, added_by, tg_id, pinned)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
               RETURNING id""",
            entry.name, entry.username, entry.letter, entry.photo,
            entry.threat, entry.status, entry.victims,
            entry.reason, entry.amount, entry.currency,
            date_str, ts,
            json.dumps(entry.proofs), entry.added_by, tg_id_val, entry.pinned
        )
    new_id = row["id"]

    # Log the action
    try:
        async with pool.acquire() as conn2:
            await conn2.execute(
                """INSERT INTO moderation_log
                   (action, blacklist_id, admin_id, target_name, target_username, reason)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                "add", new_id, entry.added_by,
                entry.name, entry.username, entry.reason
            )
    except Exception as e:
        print(f"Modlog error: {e}")

    return {"ok": True, "id": new_id}


@app.delete("/blacklist/{entry_id}")
async def delete_blacklist(entry_id: int, admin_id: int = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT name, username FROM blacklist WHERE id=$1", entry_id)
        await conn.execute("DELETE FROM blacklist WHERE id=$1", entry_id)
    # Log deletion
    if row:
        try:
            async with pool.acquire() as conn2:
                await conn2.execute(
                    "INSERT INTO moderation_log (action, blacklist_id, admin_id, target_name, target_username) VALUES ($1,$2,$3,$4,$5)",
                    "delete", entry_id, admin_id, row["name"], row["username"]
                )
        except Exception as e:
            print(f"Modlog delete error: {e}")
    return {"ok": True}


@app.put("/blacklist/{entry_id}")
async def update_blacklist(entry_id: int, data: dict):
    allowed = ["reason", "amount", "threat", "status", "victims", "pinned"]
    fields = [f"{k}=${i+1}" for i, (k, v) in enumerate(data.items()) if k in allowed]
    values = [v for k, v in data.items() if k in allowed]
    if not fields:
        return {"ok": False, "error": "No valid fields"}
    values.append(entry_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE blacklist SET {','.join(fields)} WHERE id=${len(values)}", *values
        )
    return {"ok": True}


@app.post("/blacklist/{entry_id}/pin")
async def pin_blacklist(entry_id: int, data: dict):
    """Закрепить / открепить запись."""
    pinned = bool(data.get("pinned", True))
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM blacklist WHERE id=$1", entry_id)
        if not row:
            return {"ok": False, "error": "Not found"}
        await conn.execute("UPDATE blacklist SET pinned=$1 WHERE id=$2", pinned, entry_id)
    return {"ok": True, "pinned": pinned}


# ── GROUP ROUTES ──

@app.post("/groups/add")
async def add_group(req: GroupRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO groups (chat_id, title, hostile)
               VALUES ($1,$2,$3)
               ON CONFLICT (chat_id) DO UPDATE SET title=$2, hostile=$3""",
            req.chat_id, req.title, req.hostile
        )
    return {"ok": True}


@app.post("/groups/add-hostile")
async def add_hostile_group(req: GroupRequest):
    """Добавить враждебную/скамерскую группу."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO groups (chat_id, title, hostile)
               VALUES ($1,$2,TRUE)
               ON CONFLICT (chat_id) DO UPDATE SET title=$2, hostile=TRUE""",
            req.chat_id, req.title
        )
    return {"ok": True}


@app.post("/groups/remove-hostile")
async def remove_hostile_group(req: GroupRemove):
    """Убрать группу из списка враждебных (не удаляет, просто снимает флаг)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE groups SET hostile=FALSE WHERE chat_id=$1", req.chat_id
        )
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
        rows = await conn.fetch(
            "SELECT chat_id, title, hostile FROM groups ORDER BY added_at DESC"
        )
    return {
        "ok": True,
        "groups": [{"chat_id": r["chat_id"], "title": r["title"], "hostile": r["hostile"]} for r in rows]
    }


@app.get("/groups/hostile")
async def get_hostile_groups():
    """Список только враждебных групп."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, title FROM groups WHERE hostile=TRUE ORDER BY added_at DESC"
        )
    return {
        "ok": True,
        "groups": [{"chat_id": r["chat_id"], "title": r["title"]} for r in rows]
    }


# ── LOOKUP ──

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
        photo_b64 = None
        try:
            photo_bytes = await client.download_profile_photo(entity, file=bytes)
            if photo_bytes:
                photo_b64 = "data:image/jpeg;base64," + base64.b64encode(photo_bytes).decode()
        except:
            pass
        return {
            "ok": True,
            "id": entity.id,
            "name": full_name,
            "username": f"@{uname}",
            "photo": photo_b64,
            "letter": full_name[0].upper() if full_name else "?"
        }
    except Exception as e:
        return {
            "ok": True,
            "id": None,
            "name": username,
            "username": f"@{username}",
            "photo": None,
            "letter": username[0].upper(),
            "note": "private"
        }


# ── NOTIFY USERS ──

@app.post("/notify-users")
async def notify_users(data: dict):
    scammer = data.get("scammer", {})
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tg_id FROM users")

    name     = scammer.get("name", "?")
    username = scammer.get("username", "")
    threat   = scammer.get("threat", "high")
    reason   = scammer.get("reason", "")
    amount   = str(scammer.get("amount", ""))
    currency = scammer.get("currency", "USDT")
    emoji    = "🔴" if threat == "high" else ("🟠" if threat == "med" else "🔵")
    label    = "СКАМЕР" if threat == "high" else ("ПОДОЗРИТЕЛЬНЫЙ" if threat == "med" else "ОСТОРОЖНО")

    caption = emoji + " НОВЫЙ СКАМЕР В БАЗЕ WTS\n\n"
    caption += "Имя: " + name + ("  |  " + username if username else "") + "\n"
    caption += "Категория: " + label + "\n\n" + reason
    if amount:
        caption += "\n\nУщерб: " + amount + " " + currency
    caption += "\n\nWTS Blacklist"

    # Генерируем карточку
    card_bytes = None
    try:
        from generate_card import generate_card_bytes
        card_bytes = generate_card_bytes(scammer)
    except Exception as e:
        print(f"Card gen error: {e}")

    sent = 0
    async with httpx.AsyncClient(timeout=10) as c:
        for row in rows:
            try:
                if card_bytes:
                    r = await c.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                        data={"chat_id": row["tg_id"], "caption": caption[:1024]},
                        files={"photo": ("card.png", card_bytes, "image/png")}
                    )
                    if r.json().get("ok"):
                        sent += 1
                        continue
                # fallback текст
                await c.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": row["tg_id"], "text": caption}
                )
                sent += 1
            except Exception:
                continue
    return {"ok": True, "sent": sent, "total": len(rows)}


# ── SEND TO GROUP ──

@app.post("/send-to-group")
async def send_to_group(data: dict):
    chat_id = data.get("chat_id")
    scammer = data.get("scammer", {})
    if not chat_id:
        return {"ok": False, "error": "chat_id required"}

    tmap = {"high": "СКАМЕР", "med": "ПОДОЗРИТЕЛЬНЫЙ", "low": "ОСТОРОЖНО"}
    smap = {"active": "Активен - мошенничает", "blocked": "Заблокирован в Telegram"}

    name     = scammer.get("name", "?")
    username = scammer.get("username", "")
    threat   = tmap.get(scammer.get("threat", "high"), "СКАМЕР")
    status   = smap.get(scammer.get("status", "active"), "Активен")
    reason   = scammer.get("reason", "")
    amount   = str(scammer.get("amount", ""))
    currency = scammer.get("currency", "USDT")
    victims  = scammer.get("victims", 0)
    proofs   = scammer.get("proofs", [])
    date     = scammer.get("date", "")

    caption = f"🚨 ВНИМАНИЕ — СКАМЕР\n\n"
    caption += f"👤 {name}" + (f"  |  {username}" if username else "") + "\n"
    caption += f"Категория: {threat}\n"
    caption += f"Статус: {status}\n"
    if date:
        caption += f"Дата: {date}\n"
    caption += f"\nПричина:\n{reason}"
    if amount:
        caption += f"\n\n💸 Ущерб: {amount} {currency}"
    if victims:
        caption += f"\n👥 Жертв: {victims} чел."
    caption += "\n\nWTS Blacklist — защита вашего сообщества"

    tg = f"https://api.telegram.org/bot{BOT_TOKEN}"
    valid_proofs = [p for p in proofs if p and "base64" in p][:10]

    # Генерируем карточку
    card_bytes = None
    try:
        from generate_card import generate_card_bytes
        card_bytes = generate_card_bytes(scammer)
    except Exception as e:
        print(f"Card gen error: {e}")

    async with httpx.AsyncClient(timeout=60) as c:
        # Сначала отправляем карточку или фото профиля
        photo_sent = False
        if card_bytes:
            try:
                r = await c.post(
                    tg + "/sendPhoto",
                    data={"chat_id": str(chat_id), "caption": caption[:1024]},
                    files={"photo": ("card.png", card_bytes, "image/png")}
                )
                if r.json().get("ok"):
                    photo_sent = True
            except Exception as e:
                print(f"Card send error: {e}")

        if not photo_sent:
            # Попробуем фото из профиля base64
            profile_photo = scammer.get("photo")
            if profile_photo and isinstance(profile_photo, str) and "base64" in profile_photo:
                try:
                    img = base64.b64decode(profile_photo.split(",", 1)[1])
                    r = await c.post(
                        tg + "/sendPhoto",
                        data={"chat_id": str(chat_id), "caption": caption[:1024]},
                        files={"photo": ("photo.jpg", img, "image/jpeg")}
                    )
                    if r.json().get("ok"):
                        photo_sent = True
                except Exception:
                    pass

        if not photo_sent:
            # Скачиваем фото через Telethon по tg_id или username
            try:
                tg_id_val = scammer.get("tg_id")
                uname_val = (scammer.get("username") or "").lstrip("@").strip()
                entity_to_fetch = int(tg_id_val) if tg_id_val else (uname_val if uname_val else None)
                if entity_to_fetch:
                    ph_bytes = await client.download_profile_photo(entity_to_fetch, file=bytes)
                    if ph_bytes:
                        r = await c.post(
                            tg + "/sendPhoto",
                            data={"chat_id": str(chat_id), "caption": caption[:1024]},
                            files={"photo": ("photo.jpg", ph_bytes, "image/jpeg")}
                        )
                        if r.json().get("ok"):
                            photo_sent = True
            except Exception as e:
                print(f"Telethon photo fetch error: {e}")

        if not photo_sent:
            await c.post(tg + "/sendMessage", json={"chat_id": chat_id, "text": caption})

        # Доказательства
        if valid_proofs:
            if len(valid_proofs) == 1:
                try:
                    img = base64.b64decode(valid_proofs[0].split(",", 1)[1])
                    await c.post(
                        tg + "/sendPhoto",
                        data={"chat_id": str(chat_id), "caption": "📎 Доказательство"},
                        files={"photo": ("proof.jpg", img, "image/jpeg")}
                    )
                except Exception:
                    pass
            else:
                try:
                    files = {}
                    media = []
                    for idx, proof in enumerate(valid_proofs):
                        img_bytes = base64.b64decode(proof.split(",", 1)[1])
                        key = f"photo{idx}"
                        files[key] = (f"proof{idx}.jpg", img_bytes, "image/jpeg")
                        item = {"type": "photo", "media": f"attach://{key}"}
                        if idx == 0:
                            item["caption"] = f"📎 Доказательства ({len(valid_proofs)} шт.)"
                        media.append(item)
                    await c.post(
                        tg + "/sendMediaGroup",
                        data={"chat_id": str(chat_id), "media": json.dumps(media)},
                        files=files
                    )
                except Exception as e:
                    print(f"Media group error: {e}")

    return {"ok": True}


@app.post("/send-to-all-groups")
async def send_to_all_groups(data: dict):
    """Разослать предупреждение во все подключённые группы (не враждебные)."""
    scammer = data.get("scammer", {})
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT chat_id FROM groups WHERE hostile=FALSE")

    sent = 0
    for row in rows:
        try:
            result = await send_to_group({"chat_id": row["chat_id"], "scammer": scammer})
            if result.get("ok"):
                sent += 1
        except Exception:
            continue
    return {"ok": True, "sent": sent, "total": len(rows)}


# ── BOT WEBHOOK ──

# 50+ ключевых фраз для поиска скамеров через бота
SCAM_KEYWORDS = [
    "украл", "украла", "украли", "похитил", "присвоил", "стащил", "своровал",
    "кинул", "кинула", "кинули", "обманул", "обманула", "наебал", "развёл",
    "развела", "надул", "ввёл в заблуждение", "не выплатил", "не вернул",
    "не отдал", "долг", "задолжал", "задолжала", "мошенник", "мошенница",
    "мошенники", "скам", "scam", "аферист", "аферистка", "жулик", "кидала",
    "кидок", "ворует", "воровство", "кража", "обман", "обманывает", "врёт",
    "лжёт", "лгал", "врал", "развод", "разводит", "не платит", "не заплатил",
    "фейк", "fake", "лохотрон", "схема", "пирамида", "вымогатель", "шантажист",
    "угрожает", "вымогает", "слился", "исчез", "пропал", "не отвечает",
    "предоплата", "аванс", "залог", "нфт", "nft", "fragment", "фрагмент",
    "купленный username", "крипта", "crypto", "btc", "usdt", "bitcoin",
    "не отдал деньги", "забрал деньги", "пропала сумма", "кинул на деньги",
]

def levenshtein_py(a: str, b: str) -> int:
    m, n = len(a), len(b)
    if m == 0: return n
    if n == 0: return m
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            dp[j] = prev[j-1] if a[i-1] == b[j-1] else 1 + min(dp[j], dp[j-1], prev[j])
    return dp[n]

def fuzzy_match_py(word: str, target: str) -> bool:
    if not word or not target:
        return False
    if target in word or word in target:
        return True
    if len(word) >= 8 and len(target) >= 4:
        return levenshtein_py(word, target) <= 2
    if len(word) >= 5 and len(target) >= 4:
        return levenshtein_py(word, target) <= 1
    return False

def text_matches_keywords(text: str) -> bool:
    text_lower = text.lower()
    words = text_lower.split()
    for kw in SCAM_KEYWORDS:
        kw_lower = kw.lower()
        if kw_lower in text_lower:
            return True
        for w in words:
            if fuzzy_match_py(w, kw_lower):
                return True
    return False

def search_blacklist_fuzzy(db_rows: list, query: str) -> list:
    query = query.lower().replace("@", "").strip()
    results = []
    for row in db_rows:
        name = (row.get("name") or "").lower()
        username = (row.get("username") or "").lower().replace("@", "")
        reason = (row.get("reason") or "").lower()
        if query in name or query in username or query in reason:
            results.append(row)
            continue
        words = query.split()
        for w in words:
            if fuzzy_match_py(w, name) or fuzzy_match_py(w, username):
                results.append(row)
                break
    return results

def format_scammer_msg(s: dict) -> str:
    threat_map = {"high": "🔴 СКАМЕР", "med": "🟠 ПОДОЗРИТЕЛЬНЫЙ", "low": "🔵 ОСТОРОЖНО"}
    status_map = {"active": "🔴 Активен — мошенничает", "blocked": "🔒 Заблокирован в Telegram"}
    lines = [
        f"⚠️ НАЙДЕН В БАЗЕ WTS BLACKLIST",
        "",
        f"👤 Имя: {s.get('name', '?')}",
        f"🔗 Username: {s.get('username', '?')}",
        f"📊 Категория: {threat_map.get(s.get('threat','high'), '?')}",
        f"📌 Статус: {status_map.get(s.get('status','active'), '?')}",
        "",
        f"📋 Причина: {s.get('reason', '?')}",
    ]
    if s.get("amount"):
        lines.append(f"💸 Ущерб: {s['amount']} {s.get('currency','USDT')}")
    if s.get("victims"):
        lines.append(f"👥 Жертв: {s['victims']} чел.")
    if s.get("date"):
        lines.append(f"📅 Дата: {s['date']}")
    lines += ["", "🛡 WTS Blacklist — Проверяй перед сделкой"]
    return "\n".join(lines)

@app.post("/webhook")
async def telegram_webhook(request: StarletteRequest):
    try:
        data = await request.json()
    except Exception:
        return {"ok": True}

    msg = data.get("message") or data.get("channel_post")
    if not msg:
        return {"ok": True}

    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    if not text:
        return {"ok": True}

    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}"

    async def send(chat: int, txt: str, parse_mode: str = None):
        payload = {"chat_id": chat, "text": txt}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{tg_url}/sendMessage", json=payload)

    async def send_card_tg(chat: int, scammer: dict, caption: str):
        """Отправить карточку в чат."""
        card_bytes = None
        try:
            from generate_card import generate_card_bytes
            card_bytes = generate_card_bytes(scammer)
        except Exception:
            pass
        async with httpx.AsyncClient(timeout=30) as c:
            if card_bytes:
                r = await c.post(
                    f"{tg_url}/sendPhoto",
                    data={"chat_id": str(chat), "caption": caption[:1024]},
                    files={"photo": ("card.png", card_bytes, "image/png")}
                )
                if r.json().get("ok"):
                    return
            await c.post(f"{tg_url}/sendMessage", json={"chat_id": chat, "text": caption})

    # /start
    if text.strip() == "/start":
        welcome = (
            "🛡 Добро пожаловать в WTS Blacklist Bot!\n\n"
            "Я помогу проверить пользователя перед сделкой.\n\n"
            "📌 Команды:\n"
            "/check @username — проверить по нику\n"
            "/check имя — поиск по имени\n"
            "/stats — статистика базы\n\n"
            "Или просто напиши @username или имя — я найду в базе."
        )
        await send(chat_id, welcome)
        return {"ok": True}

    # /stats
    if text.strip() == "/stats":
        pool = await get_pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM blacklist")
            scammers = await conn.fetchval("SELECT COUNT(*) FROM blacklist WHERE threat='high'")
            victims = await conn.fetchval("SELECT COALESCE(SUM(victims),0) FROM blacklist")
        await send(chat_id, f"📊 WTS Blacklist статистика:\n\n🔴 Всего: {total}\n⚠️ Скамеров: {scammers}\n👥 Жертв: {victims}")
        return {"ok": True}

    # /check or plain text
    query = text.strip()
    if query.startswith("/check"):
        query = query[6:].strip()
    if not query:
        return {"ok": True}

    # Remove @ and search
    query_clean = query.lstrip("@").strip()
    if len(query_clean) < 2:
        return {"ok": True}

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM blacklist ORDER BY ts DESC")
    db_list = []
    for row in rows:
        e = dict(row)
        try: e["proofs"] = json.loads(e.get("proofs") or "[]")
        except: e["proofs"] = []
        e["date"] = e.pop("date_str", "")
        db_list.append(e)

    results = search_blacklist_fuzzy(db_list, query_clean)

    if not results:
        await send(chat_id,
            f"✅ Пользователь «{query}» не найден в базе WTS Blacklist.\n\n"
            "Это хороший знак, но всегда проверяй перед сделкой! 🛡"
        )
    else:
        # Сортируем: закреплённые первыми
        results.sort(key=lambda x: (not x.get("pinned"), x.get("id", 0)))
        header = f"🚨 Найдено совпадений: {len(results)}\n{'─'*30}\n"
        for s in results[:2]:
            caption = header + format_scammer_msg(s)
            await send_card_tg(chat_id, s, caption)
        if len(results) > 2:
            await send(chat_id, f"...и ещё {len(results)-2} записей. Открой приложение для полного списка.")

    return {"ok": True}


@app.get("/set-webhook")
async def set_webhook(url: str):
    """Call this once to register webhook: /set-webhook?url=https://your-domain.com/webhook"""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            json={"url": url}
        )
    return r.json()



@app.get("/blacklist/sync-usernames")
async def sync_usernames():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, tg_id, username, name FROM blacklist WHERE tg_id IS NOT NULL")

    updated = 0
    errors = 0
    for row in rows:
        tg_id = row["tg_id"]
        if not tg_id:
            continue
        try:
            entity = await client.get_entity(int(tg_id))
            first = getattr(entity, "first_name", "") or ""
            last  = getattr(entity, "last_name", "") or ""
            new_name = f"{first} {last}".strip()
            new_username = f"@{entity.username}" if entity.username else row["username"]
            if new_name != row["name"] or new_username != row["username"]:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE blacklist SET name=$1, username=$2 WHERE id=$3",
                        new_name, new_username, row["id"]
                    )
                updated += 1
                print(f"Updated {row['username']} -> {new_username}")
        except Exception:
            errors += 1
            continue

    return {"ok": True, "updated": updated, "errors": errors, "total": len(rows)}


# ── MODERATION LOG ──

@app.get("/modlog")
async def get_modlog():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM moderation_log ORDER BY ts DESC LIMIT 100"
        )
    return {"ok": True, "log": [dict(r) for r in rows]}


# ── APPEALS ──

class AppealRequest(BaseModel):
    blacklist_id: int
    tg_id: int
    name: str
    username: str = ""
    reason: str

@app.post("/appeals/submit")
async def submit_appeal(req: AppealRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO appeals (blacklist_id, tg_id, name, username, reason)
               VALUES ($1,$2,$3,$4,$5) RETURNING id""",
            req.blacklist_id, req.tg_id, req.name, req.username, req.reason
        )
    return {"ok": True, "id": row["id"]}

@app.get("/appeals")
async def get_appeals():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT a.*, b.name as target_name, b.username as target_username
               FROM appeals a
               LEFT JOIN blacklist b ON a.blacklist_id = b.id
               ORDER BY a.ts DESC"""
        )
    result = []
    for r in rows:
        d = dict(r)
        for k, v in d.items():
            if hasattr(v, 'isoformat'):
                d[k] = v.isoformat()
        result.append(d)
    return {"ok": True, "appeals": result}

@app.post("/appeals/{appeal_id}/resolve")
async def resolve_appeal(appeal_id: int, data: dict):
    status = data.get("status", "rejected")  # approved / rejected
    pool = await get_pool()
    async with pool.acquire() as conn:
        appeal = await conn.fetchrow("SELECT * FROM appeals WHERE id=$1", appeal_id)
        if not appeal:
            return {"ok": False, "error": "Not found"}
        await conn.execute(
            "UPDATE appeals SET status=$1 WHERE id=$2", status, appeal_id
        )
        # If approved — remove from blacklist
        if status == "approved":
            await conn.execute("DELETE FROM blacklist WHERE id=$1", appeal["blacklist_id"])
            try:
                await conn.execute(
                    "INSERT INTO moderation_log (action, blacklist_id, target_name, target_username) VALUES ($1,$2,$3,$4)",
                    "appeal_approved", appeal["blacklist_id"], appeal["name"], appeal["username"]
                )
            except Exception:
                pass
    return {"ok": True}



# ── PARSE CHAT MEMBERS ──

@app.get("/parse-members")
async def parse_members(chat_id: str, limit: int = 200):
    """
    Парсит участников чата/группы через Telethon.
    chat_id: числовой ID или @username группы.
    Возвращает список участников с именем, username, tg_id.
    Также проверяет каждого по blacklist и помечает флагом is_scammer.
    """
    try:
        # Resolve entity
        try:
            chat_id_int = int(chat_id)
            entity = await client.get_entity(chat_id_int)
        except (ValueError, TypeError):
            entity = await client.get_entity(chat_id.strip())

        participants = []
        offset = 0
        step = 100

        while len(participants) < limit:
            chunk = await client(GetParticipantsRequest(
                channel=entity,
                filter=ChannelParticipantsSearch(""),
                offset=offset,
                limit=min(step, limit - len(participants)),
                hash=0
            ))
            if not chunk.users:
                break
            participants.extend(chunk.users)
            offset += len(chunk.users)
            if len(chunk.users) < step:
                break
            await asyncio.sleep(0.5)

        # Load blacklist for cross-check
        pool = await get_pool()
        async with pool.acquire() as conn:
            bl_rows = await conn.fetch("SELECT tg_id, username, name FROM blacklist WHERE tg_id IS NOT NULL")
        bl_ids = {r["tg_id"] for r in bl_rows}
        bl_usernames = {(r["username"] or "").lower().lstrip("@") for r in bl_rows if r["username"]}

        result = []
        for u in participants:
            if u.bot:
                continue
            first = getattr(u, "first_name", "") or ""
            last  = getattr(u, "last_name",  "") or ""
            name  = f"{first} {last}".strip() or "Без имени"
            uname = f"@{u.username}" if u.username else ""
            uname_clean = (u.username or "").lower()
            is_scammer = u.id in bl_ids or uname_clean in bl_usernames
            result.append({
                "tg_id":      u.id,
                "name":       name,
                "username":   uname,
                "is_bot":     bool(u.bot),
                "is_scammer": is_scammer,
            })

        return {"ok": True, "count": len(result), "members": result}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── SCAN GROUPS FOR SCAM REPORTS ──

@app.get("/scan-group-messages")
async def scan_group_messages(chat_id: str, limit: int = 500):
    """
    Сканирует сообщения в группе на предмет жалоб на скамеров.
    Возвращает подозрительные сообщения с именами/никами упомянутых людей.
    """
    try:
        try:
            entity = await client.get_entity(int(chat_id))
        except (ValueError, TypeError):
            entity = await client.get_entity(chat_id.strip())

        found = []
        async for message in client.iter_messages(entity, limit=limit):
            if not message.text:
                continue
            text = message.text
            if not text_matches_keywords(text):
                continue

            # Ищем упомянутые юзернеймы
            import re
            mentioned = re.findall(r"@([A-Za-z0-9_]{4,32})", text)

            sender_name = ""
            sender_username = ""
            try:
                if message.sender:
                    sn = message.sender
                    first = getattr(sn, "first_name", "") or ""
                    last  = getattr(sn, "last_name",  "") or ""
                    sender_name = f"{first} {last}".strip()
                    sender_username = f"@{sn.username}" if getattr(sn, "username", None) else ""
            except Exception:
                pass

            found.append({
                "message_id": message.id,
                "date":       message.date.isoformat() if message.date else "",
                "text":       text[:500],
                "sender_name": sender_name,
                "sender_username": sender_username,
                "mentioned":  [f"@{m}" for m in mentioned],
            })

        return {"ok": True, "count": len(found), "messages": found}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── GET ADMIN GROUPS ──

@app.get("/my-admin-groups")
async def get_admin_groups():
    """
    Возвращает список групп/каналов, где бот является администратором.
    Использует Telethon для получения диалогов.
    """
    try:
        from telethon.tl.types import Channel, Chat
        from telethon.tl.functions.channels import GetFullChannelRequest

        admin_chats = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            is_admin = False

            try:
                if isinstance(entity, Channel):
                    full = await client(GetFullChannelRequest(entity))
                    me = await client.get_me()
                    # Check if bot is admin
                    async for p in client.iter_participants(entity, limit=1, search=""):
                        pass
                    try:
                        from telethon.tl.functions.channels import GetParticipantRequest
                        part = await client(GetParticipantRequest(entity, me))
                        from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator
                        is_admin = isinstance(part.participant, (ChannelParticipantAdmin, ChannelParticipantCreator))
                    except Exception:
                        is_admin = False
                elif isinstance(entity, Chat):
                    me = await client.get_me()
                    from telethon.tl.functions.messages import GetFullChatRequest
                    full = await client(GetFullChatRequest(entity.id))
                    for p in full.full_chat.participants.participants if hasattr(full.full_chat, "participants") else []:
                        from telethon.tl.types import ChatParticipantAdmin, ChatParticipantCreator
                        if getattr(p, "user_id", None) == me.id:
                            is_admin = isinstance(p, (ChatParticipantAdmin, ChatParticipantCreator))
            except Exception:
                is_admin = False

            if is_admin:
                admin_chats.append({
                    "chat_id": entity.id,
                    "title":   dialog.name or "",
                    "type":    "channel" if isinstance(entity, Channel) and entity.broadcast else "group",
                })

        return {"ok": True, "chats": admin_chats}

    except Exception as e:
        return {"ok": False, "error": str(e)}



# ── SCAM REPORTS ──

@app.post("/groups/scam-report")
async def add_scam_report(data: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO scam_reports
               (chat_id,chat_title,sender_id,sender_name,sender_username,message_text,mentioned_users)
               VALUES($1,$2,$3,$4,$5,$6,$7)""",
            data.get("chat_id"), data.get("chat_title",""),
            data.get("sender_id"), data.get("sender_name",""),
            data.get("sender_username",""), data.get("message_text",""),
            data.get("mentioned_users","")
        )
    return {"ok": True}

@app.get("/groups/scam-reports")
async def get_scam_reports():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM scam_reports ORDER BY ts DESC LIMIT 200")
    result = []
    for r in rows:
        d = dict(r)
        for k,v in d.items():
            if hasattr(v,"isoformat"): d[k]=v.isoformat()
        result.append(d)
    return {"ok": True, "reports": result}

@app.delete("/groups/scam-report/{rid}")
async def del_scam_report(rid: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM scam_reports WHERE id=$1", rid)
    return {"ok": True}

# ── CHAT MEMBER PARSING ──

@app.post("/chat/parse-members")
async def parse_chat_members(data: dict):
    chat_ref = data.get("chat","")
    if not chat_ref: return {"ok":False,"error":"chat required"}
    try:
        entity = await asyncio.wait_for(client.get_entity(chat_ref), timeout=10.0)
    except Exception as e:
        return {"ok":False,"error":f"Чат не найден: {e}"}
    all_members = []
    offset = 0
    try:
        while True:
            parts = await client(GetParticipantsRequest(
                channel=entity, filter=ChannelParticipantsSearch(""),
                offset=offset, limit=200, hash=0
            ))
            if not parts.users: break
            for u in parts.users:
                if u.bot or u.deleted: continue
                first=getattr(u,"first_name","") or ""
                last=getattr(u,"last_name","") or ""
                uname=getattr(u,"username","") or ""
                all_members.append({
                    "tg_id":u.id,"name":f"{first} {last}".strip(),
                    "username":f"@{uname}" if uname else ""
                })
            offset+=len(parts.users)
            if len(parts.users)<200: break
            await asyncio.sleep(0.4)
    except Exception as e:
        return {"ok":False,"error":f"Ошибка: {e}","partial":all_members}
    return {"ok":True,"total":len(all_members),"members":all_members}

@app.post("/chat/check-members")
async def check_members_vs_bl(data: dict):
    parse_r = await parse_chat_members(data)
    if not parse_r.get("ok"): return parse_r
    members = parse_r["members"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        bl_rows = await conn.fetch("SELECT * FROM blacklist")
    bl = [dict(r) for r in bl_rows]
    found = []
    for m in members:
        mid=m["tg_id"]; mn=m["name"].lower(); mu=m["username"].lower().replace("@","")
        for s in bl:
            sid=s.get("tg_id"); sn=(s.get("name")or"").lower(); su=(s.get("username")or"").lower().replace("@","")
            if (sid and mid and sid==mid) or (mu and su and mu==su) or (mn and sn and len(mn)>3 and mn==sn):
                found.append({"member":m,"scammer":s}); break
    return {"ok":True,"total_members":len(members),"found_scammers":len(found),"results":found}


# ── HEALTH ──

@app.get("/health")
async def health():
    return {"status": "ok", "admin_ids": ADMIN_IDS}
