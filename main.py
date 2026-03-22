import os
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8274782796:AAFBK4sJpQhtXnIE9IxOMmNhivlM2dXEgp4")
TG = f"https://api.telegram.org/bot{BOT_TOKEN}"


@app.options("/lookup")
async def lookup_options():
    return JSONResponse({}, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })


@app.get("/lookup")
async def lookup(username: str):
    username = username.lstrip("@").strip()
    if not username:
        return {"ok": False, "error": "Р’РІРµРґРё username"}

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{TG}/getChat", params={"chat_id": f"@{username}"})
            data = r.json()
        except Exception as e:
            return {"ok": False, "error": f"РћС€РёР±РєР° СЃРѕРµРґРёРЅРµРЅРёСЏ: {str(e)}"}

        if not data.get("ok"):
            desc = data.get("description", "")
            if "not found" in desc.lower() or "invalid" in desc.lower():
                return {"ok": False, "error": "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ"}
            return {"ok": False, "error": desc or "РћС€РёР±РєР° Telegram"}

        chat = data["result"]
        user_id = chat.get("id")
        first = chat.get("first_name", "")
        last = chat.get("last_name", "")
        uname = chat.get("username", username)
        full_name = f"{first} {last}".strip() or uname

        # Р¤РѕС‚Рѕ РїСЂРѕС„РёР»СЏ
        photo_url = None
        try:
            pr = await client.get(
                f"{TG}/getUserProfilePhotos",
                params={"user_id": user_id, "limit": 1}
            )
            pd = pr.json()
            if pd.get("ok") and pd["result"]["total_count"] > 0:
                file_id = pd["result"]["photos"][0][-1]["file_id"]
                fr = await client.get(f"{TG}/getFile", params={"file_id": file_id})
                fd = fr.json()
                if fd.get("ok"):
                    fp = fd["result"]["file_path"]
                    photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}"
        except Exception:
            pass

        return {
            "ok": True,
            "id": user_id,
            "name": full_name,
            "username": f"@{uname}",
            "photo": photo_url,
            "letter": full_name[0].upper() if full_name else "?"
        }


@app.get("/health")
async def health():
    return {"status": "ok"}
