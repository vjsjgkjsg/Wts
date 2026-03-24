import os
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN   = os.getenv("BOT_TOKEN", "8274782796:AAFBK4sJpQhtXnIE9IxOMmNhivlM2dXEgp4")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://vjsjgkjsg.github.io/Wtss/")
BACKEND_URL = os.getenv("BACKEND_URL", "https://web-production-acde3.up.railway.app")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🛡 Открыть WTS Blacklist", web_app=WebAppInfo(url=MINIAPP_URL))]]
    await update.message.reply_text(
        "👋 Добро пожаловать в *WTS Blacklist*\n\n"
        "🔍 Проверяй пользователей перед сделкой\n"
        "🛡 База проверенных скамеров\n"
        "⚡️ Обновляется администрацией\n\n"
        "Нажми кнопку ниже чтобы открыть базу:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def on_new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Бот добавлен в группу — сохраняем"""
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            chat = update.effective_chat
            await register_group(chat.id, chat.title or "Группа")
            await update.message.reply_text(
                "✅ *WTS Blacklist* подключён!\n\n"
                "Теперь администраторы могут отправлять сюда уведомления о скамерах.",
                parse_mode="Markdown"
            )

async def on_left_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Бот удалён из группы"""
    if update.message.left_chat_member.id == context.bot.id:
        await unregister_group(update.effective_chat.id)

async def register_group(chat_id: int, title: str):
    try:
        async with httpx.AsyncClient() as c:
            await c.post(f"{BACKEND_URL}/groups/add",
                json={"chat_id": chat_id, "title": title}, timeout=5)
    except Exception as e:
        print(f"Register group error: {e}")

async def unregister_group(chat_id: int):
    try:
        async with httpx.AsyncClient() as c:
            await c.post(f"{BACKEND_URL}/groups/remove",
                json={"chat_id": chat_id}, timeout=5)
    except Exception as e:
        print(f"Unregister group error: {e}")

async def notify_all_users(scammer: dict):
    """Send notification to all registered users about new scammer"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BACKEND_URL}/users/all")
            data = r.json()
            users = data.get("users", [])
    except Exception as e:
        print(f"Failed to get users: {e}")
        return

    name     = scammer.get("name", "Неизвестный")
    username = scammer.get("username", "")
    threat   = scammer.get("threat", "high")
    reason   = scammer.get("reason", "")
    amount   = scammer.get("amount", "")
    currency = scammer.get("currency", "USDT")

    emoji = "🔴" if threat == "high" else "🟠" if threat == "med" else "🔵"
    label = "СКАМЕР" if threat == "high" else "ПОДОЗРИТЕЛЬНЫЙ" if threat == "med" else "ОСТОРОЖНО"

    lines = [
        emoji + " НОВЫЙ СКАМЕР В БАЗЕ WTS",
        "",
        "Имя: " + name + ("  |  " + username if username else ""),
        "Категория: " + label,
        "",
        reason,
    ]
    if amount:
        lines.append("")
        lines.append("Ущерб: " + str(amount) + " " + currency)
    lines += ["", "WTS Blacklist - будь осторожен при сделках"]
    msg = "\n".join(lines)

    keyboard = [[InlineKeyboardButton("🔍 Открыть базу", web_app=WebAppInfo(url=MINIAPP_URL))]]
    markup = InlineKeyboardMarkup(keyboard)

    sent = 0
    for user in users:
        tg_id = user.get("tg_id")
        if not tg_id:
            continue
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": tg_id,
                        "text": msg,
                        "parse_mode": "Markdown",
                        "reply_markup": markup.to_dict()
                    }
                )
            sent += 1
        except Exception:
            continue
    print(f"Notified {sent}/{len(users)} users")

async def auto_sync_usernames():
    """Every hour check and update usernames of scammers in DB"""
    import asyncio
    while True:
        await asyncio.sleep(3600)  # every hour
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{BACKEND_URL}/blacklist/sync-usernames")
                d = r.json()
                print(f"Username sync: updated {d.get('updated',0)}, errors {d.get('errors',0)}")
        except Exception as e:
            print(f"Username sync error: {e}")

async def sync_groups(app):
    """On startup - verify all saved groups still have the bot"""
    print("Syncing groups from DB...")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BACKEND_URL}/groups")
            data = r.json()
            groups = data.get("groups", [])
        
        print(f"Found {len(groups)} groups in DB, verifying...")
        for group in groups:
            chat_id = group["chat_id"]
            try:
                # Check if bot is still in the group
                chat = await app.bot.get_chat(chat_id)
                # Update title if changed
                if chat.title != group["title"]:
                    await register_group(chat_id, chat.title)
                    print(f"Updated group title: {chat.title}")
                else:
                    print(f"Group OK: {group['title']}")
            except Exception as e:
                # Bot was removed from group - clean up
                print(f"Group {chat_id} unavailable, removing: {e}")
                await unregister_group(chat_id)
    except Exception as e:
        print(f"Sync error: {e}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(sync_groups).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_chat))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_chat))
    print("🤖 WTS Bot запущен...")
    import asyncio
    loop = asyncio.get_event_loop()
    loop.create_task(auto_sync_usernames())
    app.run_polling()
