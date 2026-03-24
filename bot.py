import os
import re
import httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo,
    InlineQueryResultArticle, InputTextMessageContent
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    InlineQueryHandler, ContextTypes, filters
)
import uuid

BOT_TOKEN   = os.getenv("BOT_TOKEN",   "8274782796:AAFBK4sJpQhtXnIE9IxOMmNhivlM2dXEgp4")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://vjsjgkjsg.github.io/Wtss/")
BACKEND_URL = os.getenv("BACKEND_URL", "https://web-production-acde3.up.railway.app")

# ── ТВОЙ TELEGRAM ID (куда слать подозрительные сообщения) ──
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "7810141735"))

# Слова-триггеры для мониторинга групп
SCAM_KEYWORDS = [
    "скам", "скамер", "кидал", "кинул", "мошенник", "мошенничество",
    "развод", "обман", "обманул", "обули", "кидок", "кидос",
    "scam", "scammer", "fraud", "cheat",
    "не отдал", "забрал деньги", "взял деньги", "не вернул",
    "потерял деньги", "украл", "взломал"
]

# Паттерны для суммы ущерба
AMOUNT_PATTERNS = [
    r'\b(\d[\d\s,\.]*)\s*(usdt|usdc|btc|eth|ton|рублей|руб|руб\.|usd|\$|€)',
    r'\b(\d[\d\s,\.]+)(к|тыс|k)\b',
    r'\b(\d{4,})\b'
]


# ══════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════

def extract_amount(text: str) -> str:
    text_lower = text.lower()
    for pattern in AMOUNT_PATTERNS:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def detect_scam(text: str):
    text_lower = text.lower()
    found = [kw for kw in SCAM_KEYWORDS if kw in text_lower]
    return bool(found), found


# ══════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════

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


# ══════════════════════════════════════
#  ГРУППЫ
# ══════════════════════════════════════

async def on_new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            chat = update.effective_chat
            await register_group(chat.id, chat.title or "Группа")
            await update.message.reply_text(
                "✅ *WTS Blacklist* подключён!\n\n"
                "Теперь администраторы могут отправлять сюда уведомления о скамерах.\n"
                "🔍 Я слежу за сообщениями — если кто-то упомянет скама или мошенника, "
                "администрация WTS получит уведомление.",
                parse_mode="Markdown"
            )


async def on_left_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


# ══════════════════════════════════════
#  МОНИТОРИНГ ГРУПП
# ══════════════════════════════════════

async def monitor_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # Только группы/супергруппы
    if msg.chat.type not in ("group", "supergroup"):
        return

    raw_text = msg.text or msg.caption or ""

    # Нужно либо текст со скам-словами, либо фото + скам-слово в подписи
    is_scam, matched_words = detect_scam(raw_text)

    # Если нет текста и нет фото — пропускаем
    if not is_scam and not msg.photo:
        return
    if not is_scam:
        return

    sender = msg.from_user
    if not sender:
        return

    sender_name     = sender.full_name or "Неизвестный"
    sender_username = f"@{sender.username}" if sender.username else f"[ID: {sender.id}]"
    chat_title      = msg.chat.title or "Группа"
    chat_id         = msg.chat_id
    amount          = extract_amount(raw_text)

    # ── Строим алерт ──
    alert_lines = [
        "🚨 *ПОДОЗРИТЕЛЬНОЕ СООБЩЕНИЕ В ГРУППЕ*",
        "",
        f"📍 *Группа:* {chat_title}",
        f"👤 *От:* {sender_name} ({sender_username})",
        f"🆔 *User ID:* `{sender.id}`",
        "",
        f"🔍 *Триггер-слова:* `{', '.join(matched_words)}`",
    ]
    if amount:
        alert_lines.append(f"💸 *Сумма в сообщении:* `{amount}`")
    if msg.photo:
        alert_lines.append("📎 *В сообщении есть скриншот(ы)*")
    if raw_text:
        short = raw_text[:600]
        alert_lines += ["", "📝 *Текст:*", f"```\n{short}\n```"]

    # Ссылка на сообщение (только для супергрупп с username или public)
    msg_link = ""
    try:
        if msg.chat.username:
            msg_link = f"https://t.me/{msg.chat.username}/{msg.message_id}"
        else:
            cid = str(chat_id).replace("-100", "")
            msg_link = f"https://t.me/c/{cid}/{msg.message_id}"
        alert_lines.append(f"\n🔗 [Перейти к сообщению]({msg_link})")
    except Exception:
        pass

    alert_text = "\n".join(alert_lines)

    try:
        keyboard = [[
            InlineKeyboardButton("🛡 Добавить в базу WTS", web_app=WebAppInfo(url=MINIAPP_URL))
        ]]
        await context.bot.send_message(
            ADMIN_TG_ID,
            alert_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

        # Пересылаем оригинал (с фото если есть)
        try:
            await context.bot.forward_message(
                chat_id=ADMIN_TG_ID,
                from_chat_id=chat_id,
                message_id=msg.message_id
            )
        except Exception as fwd_err:
            print(f"Forward failed: {fwd_err}")
            # Fallback: шлём фото вручную
            if msg.photo:
                try:
                    await context.bot.send_photo(
                        ADMIN_TG_ID,
                        msg.photo[-1].file_id,
                        caption=f"📎 Скриншот из группы «{chat_title}» от {sender_username}"
                    )
                except Exception:
                    pass

        print(f"[MONITOR] Scam in '{chat_title}' from {sender_username}: {matched_words} | amount={amount}")

    except Exception as e:
        print(f"[MONITOR] Alert send error: {e}")


# ══════════════════════════════════════
#  NOTIFY ALL USERS (вызывается из main.py)
# ══════════════════════════════════════

async def notify_all_users(scammer: dict):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BACKEND_URL}/users/all")
            users = r.json().get("users", [])
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
        emoji + " НОВЫЙ СКАМЕР В БАЗЕ WTS", "",
        "Имя: " + name + ("  |  " + username if username else ""),
        "Категория: " + label, "",
        reason,
    ]
    if amount:
        lines += ["", "Ущерб: " + str(amount) + " " + currency]
    lines += ["", "🛡 Проверяй перед сделкой: @WTSBlaskListBot"]
    msg_text = "\n".join(lines)

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
                        "text": msg_text,
                        "reply_markup": markup.to_dict()
                    }
                )
            sent += 1
        except Exception:
            continue
    print(f"Notified {sent}/{len(users)} users")


# ══════════════════════════════════════
#  СИНХРОНИЗАЦИЯ ГРУПП ПРИ СТАРТЕ
# ══════════════════════════════════════

async def sync_groups(app):
    print("Syncing groups from DB...")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BACKEND_URL}/groups")
            groups = r.json().get("groups", [])

        print(f"Found {len(groups)} groups in DB, verifying...")
        for group in groups:
            chat_id = group["chat_id"]
            try:
                chat = await app.bot.get_chat(chat_id)
                if chat.title != group["title"]:
                    await register_group(chat_id, chat.title)
                    print(f"Updated group title: {chat.title}")
                else:
                    print(f"Group OK: {group['title']}")
            except Exception as e:
                print(f"Group {chat_id} unavailable, removing: {e}")
                await unregister_group(chat_id)
    except Exception as e:
        print(f"Sync error: {e}")


# ══════════════════════════════════════
#  INLINE QUERY
# ══════════════════════════════════════

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query:
        return
    result = InlineQueryResultArticle(
        id=str(uuid.uuid4()),
        title="📤 Отправить предупреждение",
        description=query[:100] + "..." if len(query) > 100 else query,
        input_message_content=InputTextMessageContent(message_text=query)
    )
    await update.inline_query.answer([result], cache_time=0)


# ══════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════

if __name__ == "__main__":
    app = (ApplicationBuilder()
           .token(BOT_TOKEN)
           .post_init(sync_groups)
           .build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_chat))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_chat))
    app.add_handler(InlineQueryHandler(inline_query))

    # Мониторинг: текст + фото с подписями в группах
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION | filters.PHOTO) & filters.ChatType.GROUPS,
        monitor_messages
    ))

    print("🤖 WTS Bot запущен с мониторингом групп...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
