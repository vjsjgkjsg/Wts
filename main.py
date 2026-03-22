import os
import asyncio
import base64
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from telethon import TelegramClient
from telethon.tl.types import UserProfilePhoto
import io

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8274782796:AAFBK4sJpQhtXnIE9IxOMmNhivlM2dXEgp4")
API_ID    = int(os.getenv("API_ID", "26508724"))
API_HASH  = os.getenv("API_HASH", "2ada38c67ea946fe3be7fdd8e2507366")

# Глобальный клиент
client = TelegramClient("wts_session", API_ID, API_HASH)

@app.on_event("startup")
async def startup():
    await client.start(bot_token=BOT_TOKEN)
    print("Telethon client started!")

@app.on_event("shutdown")
async def shutdown():
    await client.disconnect()


@app.get("/lookup")
async def lookup(username: str):
    username = username.lstrip("@").strip()
    if not username:
        return {"ok": False, "error": "Введи username"}

    try:
        entity = await client.get_entity(f"@{username}")

        first = getattr(entity, "first_name", "") or ""
        last  = getattr(entity, "last_name", "")  or ""
        uname = getattr(entity, "username", username) or username
        full_name = f"{first} {last}".strip() or uname
        user_id = entity.id

        # Скачиваем фото профиля
        photo_b64 = None
        try:
            photo_bytes = await client.download_profile_photo(entity, file=bytes)
            if photo_bytes:
                photo_b64 = "data:image/jpeg;base64," + base64.b64encode(photo_bytes).decode()
        except Exception:
            pass

        return {
            "ok": True,
            "id": user_id,
            "name": full_name,
            "username": f"@{uname}",
            "photo": photo_b64,
            "letter": full_name[0].upper() if full_name else "?"
        }

    except Exception as e:
        err = str(e)
        if "Cannot find any entity" in err or "No user" in err:
            return {"ok": False, "error": "Пользователь не найден"}
        return {"ok": False, "error": "Ошибка поиска"}


@app.get("/health")
async def health():
    return {"status": "ok"}
