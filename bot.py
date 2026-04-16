"""
WTS ScamList Bot — bot.py
Изменения:
  • В группах реагирует ТОЛЬКО на "скам/scam" + упоминание суммы
  • Все алерты отправляет ФОТО (карточка), а не текстом
  • Команды добавления враждебных групп по ID
  • Закрепление записей в базе
  • Полностью доработанный /start и команды
"""

import os
import re
import io
import asyncio
import httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo, InlineQueryResultArticle, InputTextMessageContent,
    ChatMember
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    InlineQueryHandler, ChatMemberHandler, ContextTypes, filters
)
import uuid

# ── КОНФИГ ────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN",   "8650752955:AAH0IiWv9SNYHTbPcEGitj8oRIsLh-EmCGw")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://vjsjgkjsg.github.io/Wtss/")
BACKEND_URL = os.getenv("BACKEND_URL", "https://web-production-acde3.up.railway.app")

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "7810141735")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

APP_NAME  = "WTS ScamList"
BOT_UNAME = "WTSBlaskListBot"

# ── ТРИГГЕРЫ (ТОЛЬКО для групп) ───────────────────────────────────────────────
# Бот реагирует ТОЛЬКО если сообщение содержит:
#   1. слово «скам/scam» или «мошенник» и т.п.   И
#   2. упоминание суммы (цифра + валюта / просто цифра рядом)

SCAM_CORE = [
    "скам","scam","скамер","скамеры","scammer","мошенник","мошенница","мошенники",
    "кинул","кинула","кинули","кидает","кидала","кидок",
    "развёл","развела","развели","разводит",
    "аферист","аферистка","жулик","вор","воровство","кража",
    "обманул","обманула","обманули","обманывает",
]

AMOUNT_RE = re.compile(
    r'(\d[\d\s.,]*\s*(?:usdt|btc|eth|ton|usd|eur|руб|₽|\$|€|тг|usdc|uah))'
    r'|(?:на\s+)?(\d[\d\s]*)\s*(?:долларов|баксов|рублей|монет|крипты|юсдт)',
    re.IGNORECASE
)

def _normalize(text: str) -> str:
    for k, v in {"ё":"е","й":"и","0":"о","3":"з","1":"и","@":"а"}.items():
        text = text.replace(k, v)
    return text.lower()

def _has_scam_word(text: str) -> bool:
    t = _normalize(text)
    words = re.split(r'\W+', t)
    for kw in SCAM_CORE:
        kw_n = _normalize(kw)
        if kw_n in t:
            return True
        for w in words:
            if w and kw_n and (kw_n in w or w in kw_n):
                return True
    return False

def _has_amount(text: str) -> bool:
    return bool(AMOUNT_RE.search(text))

def should_react_in_group(text: str) -> bool:
    """Реагируем в группе ТОЛЬКО если есть и скам-слово И сумма."""
    return _has_scam_word(text) and _has_amount(text)

# ── ГЕНЕРАЦИЯ КАРТОЧКИ ────────────────────────────────────────────────────────

def _make_card(scammer: dict) -> bytes:
    try:
        from generate_card import generate_card_bytes
        return generate_card_bytes(scammer)
    except Exception as e:
        print(f"Card gen error: {e}")
        return None

# ── BACKEND ХЕЛПЕРЫ ───────────────────────────────────────────────────────────

async def _get(path: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{BACKEND_URL}{path}")
            return r.json()
    except Exception as e:
        print(f"GET {path} error: {e}")
        return {}

async def _post(path: str, payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{BACKEND_URL}{path}", json=payload)
            return r.json()
    except Exception as e:
        print(f"POST {path} error: {e}")
        return {}

async def search_blacklist(query: str) -> list:
    data = await _get("/blacklist")
    rows = data.get("blacklist", [])
    q = query.lower().replace("@", "").strip()
    results = []
    seen = set()
    for s in rows:
        sid = s.get("id")
        name  = (s.get("name")  or "").lower()
        uname = (s.get("username") or "").lower().replace("@", "")
        reason = (s.get("reason") or "").lower()
        if q in name or q in uname or q in reason:
            if sid not in seen:
                seen.add(sid)
                results.append(s)
    return results

def format_scammer(s: dict) -> str:
    threat_map = {"high": "🔴 СКАМЕР", "med": "🟠 ПОДОЗРИТЕЛЬНЫЙ", "low": "🔵 ОСТОРОЖНО"}
    status_map = {"active": "🔴 Активен", "blocked": "🔒 Заблокирован"}
    pin_mark = "📌 ЗАКРЕПЛЁН\n" if s.get("pinned") else ""
    lines = [
        f"{pin_mark}⚠️ НАЙДЕН В БАЗЕ {APP_NAME.upper()}",
        "",
        f"👤 {s.get('name','?')}  {s.get('username','')}",
        f"📊 {threat_map.get(s.get('threat','high'), '?')}",
        f"📌 {status_map.get(s.get('status','active'), '?')}",
        "",
        f"📋 {s.get('reason','?')}",
    ]
    if s.get("amount"):
        lines.append(f"💸 Ущерб: {s['amount']} {s.get('currency','USDT')}")
    if s.get("victims"):
        lines.append(f"👥 Жертв: {s['victims']} чел.")
    lines += ["", f"🛡 {APP_NAME} — проверяй перед сделкой"]
    return "\n".join(lines)

# ── КНОПКИ ────────────────────────────────────────────────────────────────────

def kb_private():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🛡 Открыть {APP_NAME}", web_app=WebAppInfo(url=MINIAPP_URL))
    ]])

def kb_group():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🛡 Открыть {APP_NAME}", url=f"https://t.me/{BOT_UNAME}?startapp=1")
    ]])

# ── ОТПРАВКА КАРТОЧКИ ─────────────────────────────────────────────────────────

async def send_card(bot, chat_id, scammer: dict, caption: str, kb=None):
    """Отправляет карточку-фото. Если не получается — текстом."""
    card_bytes = _make_card(scammer)
    kwargs = {"chat_id": chat_id, "parse_mode": "Markdown"}
    if kb:
        kwargs["reply_markup"] = kb
    if card_bytes:
        try:
            await bot.send_photo(
                photo=io.BytesIO(card_bytes),
                caption=caption[:1024],
                **kwargs
            )
            return
        except Exception as e:
            print(f"send_photo error: {e}")
    # fallback
    await bot.send_message(text=caption, **kwargs)

async def reply_card(msg, scammer: dict, caption: str, kb=None):
    """Reply карточкой."""
    card_bytes = _make_card(scammer)
    kwargs = {"parse_mode": "Markdown"}
    if kb:
        kwargs["reply_markup"] = kb
    if card_bytes:
        try:
            await msg.reply_photo(
                photo=io.BytesIO(card_bytes),
                caption=caption[:1024],
                **kwargs
            )
            return
        except Exception as e:
            print(f"reply_photo error: {e}")
    await msg.reply_text(text=caption, **kwargs)

# ── ГРУППЫ ───────────────────────────────────────────────────────────────────

async def register_group(chat_id: int, title: str, hostile: bool = False):
    await _post("/groups/add", {"chat_id": chat_id, "title": title, "hostile": hostile})
    print(f"{'🚨' if hostile else '✅'} Group {'hostile ' if hostile else ''}registered: {title} ({chat_id})")

async def unregister_group(chat_id: int):
    await _post("/groups/remove", {"chat_id": chat_id})
    print(f"Group removed: {chat_id}")

# ── FORWARD ADMINS ───────────────────────────────────────────────────────────

async def forward_to_admins(bot, msg, group_title: str):
    sender = msg.from_user
    if not sender:
        return
    sender_name = (sender.first_name or "") + (" " + sender.last_name if sender.last_name else "")
    sender_username = f"@{sender.username}" if sender.username else f"ID:{sender.id}"
    caption = (
        f"🔔 *Жалоба на рассмотрение*\n"
        f"{'─'*28}\n"
        f"👥 Группа: *{group_title}*\n"
        f"👤 От: [{sender_name}](tg://user?id={sender.id}) {sender_username}\n"
        f"{'─'*28}\n\n"
        f"📋 Сообщение:\n{msg.text or msg.caption or '—'}"
    )
    for admin_id in ADMIN_IDS:
        try:
            if msg.photo:
                await bot.send_photo(chat_id=admin_id, photo=msg.photo[-1].file_id,
                                     caption=caption, parse_mode="Markdown")
            elif msg.document:
                await bot.send_document(chat_id=admin_id, document=msg.document.file_id,
                                        caption=caption, parse_mode="Markdown")
            else:
                await bot.send_message(chat_id=admin_id, text=caption, parse_mode="Markdown")
            if msg.media_group_id:
                await bot.forward_message(chat_id=admin_id, from_chat_id=msg.chat_id,
                                          message_id=msg.message_id)
        except Exception as e:
            print(f"Forward to admin {admin_id} error: {e}")

# ── HANDLERS ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_admin = user.id in ADMIN_IDS
    text = (
        f"🛡 *{APP_NAME}*\n\n"
        "Официальная база данных мошенников.\n\n"
        "Прежде чем провести сделку — проверь контрагента.\n\n"
        "📌 *Команды:*\n"
        "/check @username — проверить пользователя\n"
        "/stats — статистика базы\n"
    )
    if is_admin:
        text += (
            "\n👑 *Команды администратора:*\n"
            "/addhostile -100xxxxxxxxx Название — добавить враждебную группу\n"
            "/listhostile — список враждебных групп\n"
            "/pin ID — закрепить запись в базе\n"
            "/unpin ID — открепить запись\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_private())

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_group = update.effective_chat.type in ("group", "supergroup")
    data = await _get("/blacklist")
    bl = data.get("blacklist", [])
    total    = len(bl)
    scammers = sum(1 for x in bl if x.get("threat") == "high")
    victims  = sum(int(x.get("victims") or 0) for x in bl)
    pinned   = sum(1 for x in bl if x.get("pinned"))
    await update.effective_message.reply_text(
        f"📊 *{APP_NAME}* — статистика:\n\n"
        f"🔴 Всего записей: {total}\n"
        f"⚠️ Скамеров: {scammers}\n"
        f"👥 Жертв: {victims}\n"
        f"📌 Закреплено: {pinned}\n\n"
        f"🛡 Проверяй перед сделкой",
        parse_mode="Markdown",
        reply_markup=kb_group() if is_group else kb_private()
    )

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_group = update.effective_chat.type in ("group", "supergroup")
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.effective_message.reply_text(
            "📌 Укажи ник или имя:\n/check @username\n/check Имя Фамилия"
        )
        return
    await _do_search(update.effective_message, query, is_group=is_group)

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ Нет доступа.")
        return
    data = await _get("/users/all")
    users = data.get("users", [])
    if not users:
        await update.message.reply_text("📭 Пользователей пока нет.")
        return
    role_map = {"admin": "👑", "mod": "🛡️", "user": "👤"}
    lines = [f"👥 *Пользователи {APP_NAME}* — {len(users)} чел.\n"]
    for i, u in enumerate(users[:50], 1):
        icon = role_map.get(u.get("role", "user"), "👤")
        name = u.get("name") or "—"
        uname = u.get("username") or f"ID:{u.get('tg_id','?')}"
        lines.append(f"{i}. {icon} {name} {uname}")
    if len(users) > 50:
        lines.append(f"\n...и ещё {len(users)-50} пользователей")
    text = "\n".join(lines)
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for ch in chunks:
        await update.message.reply_text(ch, parse_mode="Markdown")

# ── ДОБАВЛЕНИЕ ВРАЖДЕБНОЙ ГРУППЫ ─────────────────────────────────────────────

async def cmd_add_hostile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addhostile -100xxxxxxxxx Название группы
    Добавляет враждебную/скамерскую группу в базу по chat_id.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ Нет доступа.")
        return
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "📌 Формат:\n/addhostile -100xxxxxxxxx Название группы\n\n"
            "Если хочешь добавить текущую группу:\n/addhostile here"
        )
        return
    if context.args[0].lower() == "here":
        chat = update.effective_chat
        if chat.type not in ("group", "supergroup"):
            await update.message.reply_text("❌ Эту команду нужно писать в группе.")
            return
        chat_id = chat.id
        title = chat.title or "Группа"
    else:
        raw_id = context.args[0]
        title = " ".join(context.args[1:]) or "Враждебная группа"
        try:
            chat_id = int(raw_id)
        except ValueError:
            await update.message.reply_text("❌ Неверный формат ID. Пример: -1001234567890")
            return

    result = await _post("/groups/add-hostile", {"chat_id": chat_id, "title": title})
    if result.get("ok"):
        await update.message.reply_text(
            f"🚨 Группа добавлена в список враждебных:\n"
            f"*{title}* (`{chat_id}`)",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ Ошибка: {result.get('error','?')}")

async def cmd_list_hostile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список враждебных групп."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ Нет доступа.")
        return
    data = await _get("/groups/hostile")
    groups = data.get("groups", [])
    if not groups:
        await update.message.reply_text("📭 Враждебных групп нет.")
        return
    lines = [f"🚨 *Враждебные группы* ({len(groups)}):\n"]
    for g in groups:
        lines.append(f"• {g.get('title','?')} | `{g.get('chat_id','?')}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_remove_hostile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /rmhostile -100xxxxxxxxx
    Убирает группу из списка враждебных.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ Нет доступа.")
        return
    if not context.args:
        await update.message.reply_text("📌 Формат: /rmhostile -100xxxxxxxxx")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный ID.")
        return
    result = await _post("/groups/remove-hostile", {"chat_id": chat_id})
    if result.get("ok"):
        await update.message.reply_text(f"✅ Группа `{chat_id}` удалена из враждебных.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Ошибка: {result.get('error','?')}")

# ── ЗАКРЕПЛЕНИЕ ЗАПИСЕЙ ───────────────────────────────────────────────────────

async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pin ID — закрепить запись скамера в базе (будет показываться первой).
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ Нет доступа.")
        return
    if not context.args:
        await update.message.reply_text("📌 Формат: /pin ID")
        return
    try:
        entry_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный ID записи.")
        return
    result = await _post(f"/blacklist/{entry_id}/pin", {"pinned": True})
    if result.get("ok"):
        await update.message.reply_text(f"📌 Запись #{entry_id} закреплена. Теперь будет отображаться первой.")
    else:
        await update.message.reply_text(f"❌ Ошибка: {result.get('error','?')}")

async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /unpin ID — открепить запись.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔️ Нет доступа.")
        return
    if not context.args:
        await update.message.reply_text("📌 Формат: /unpin ID")
        return
    try:
        entry_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный ID записи.")
        return
    result = await _post(f"/blacklist/{entry_id}/pin", {"pinned": False})
    if result.get("ok"):
        await update.message.reply_text(f"✅ Запись #{entry_id} откреплена.")
    else:
        await update.message.reply_text(f"❌ Ошибка: {result.get('error','?')}")

# ── ПОИСК ────────────────────────────────────────────────────────────────────

async def _do_search(msg, query: str, is_group: bool = False):
    query_clean = query.lstrip("@").strip()
    if len(query_clean) < 2:
        await msg.reply_text("❌ Слишком короткий запрос.")
        return
    results = await search_blacklist(query_clean)
    kb = kb_group() if is_group else kb_private()

    if not results:
        await msg.reply_text(
            f"✅ *{query}* не найден в базе {APP_NAME}.\n\n"
            "Это хороший знак, но всегда проверяй перед сделкой! 🛡",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    # Сортируем: закреплённые первыми
    results.sort(key=lambda x: (not x.get("pinned"), x.get("id", 0)))

    for s in results[:3]:
        caption = format_scammer(s)
        await reply_card(msg, s, caption, kb)

    if len(results) > 3:
        await msg.reply_text(
            f"📋 ...и ещё {len(results)-3} записей. Открой приложение для полного списка.",
            reply_markup=kb
        )

# ── СООБЩЕНИЯ В ЛИЧКЕ ────────────────────────────────────────────────────────

async def private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("/"):
        return
    await _do_search(update.message, text, is_group=False)

# ── СООБЩЕНИЯ В ГРУППАХ ───────────────────────────────────────────────────────

async def group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    text = msg.text.strip()

    # /check в группе
    if re.match(r'^/check', text, re.IGNORECASE):
        query = re.sub(r'^/check\S*', '', text, flags=re.IGNORECASE).strip()
        if query:
            await _do_search(msg, query, is_group=True)
        else:
            await msg.reply_text("📌 Используй: /check @username или /check Имя")
        return

    # /stats в группе
    if re.match(r'^/stats', text, re.IGNORECASE):
        await cmd_stats(update, context)
        return

    # Реагируем ТОЛЬКО если скам-слово + сумма
    if not should_react_in_group(text):
        return

    # Есть и скам и сумма — пересылаем жалобу администраторам
    group_title = update.effective_chat.title or "Группа"
    await forward_to_admins(context.bot, msg, group_title)

    # Ищем упомянутые @username в тексте
    usernames = re.findall(r'@[\w]{3,}', text)

    if usernames:
        found_any = False
        for uname in usernames[:3]:
            results = await search_blacklist(uname)
            if results:
                found_any = True
                results.sort(key=lambda x: (not x.get("pinned"), x.get("id", 0)))
                for s in results[:2]:
                    caption = f"🚨 *{uname}* — найден в базе {APP_NAME}!\n\n" + format_scammer(s)
                    await reply_card(msg, s, caption, kb_group())
        if not found_any:
            # Есть жалоба со скамом и суммой, но пользователь не в базе
            await msg.reply_text(
                f"🛡 *{APP_NAME}*\n\n"
                "Замечена жалоба с упоминанием мошенничества и суммы.\n"
                "Проверь пользователя вручную:",
                parse_mode="Markdown",
                reply_markup=kb_group()
            )
    else:
        # Скам + сумма, но нет @username
        await msg.reply_text(
            "🚨 Замечена жалоба с упоминанием суммы.\n"
            "Укажи @username чтобы проверить базу: /check @username",
            reply_markup=kb_group()
        )

# ── СТАТУС БОТА В ГРУППАХ ────────────────────────────────────────────────────

async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result:
        return
    chat       = result.chat
    new_status = result.new_chat_member.status

    if new_status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR):
        await register_group(chat.id, chat.title or "Группа")
        try:
            await context.bot.send_message(
                chat.id,
                f"✅ *{APP_NAME}* подключён!\n\n"
                "Слежу за сообщениями. При упоминании скама с суммой — реагирую.\n"
                "/check @username — проверить пользователя",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    elif new_status in (ChatMember.LEFT, ChatMember.BANNED):
        await unregister_group(chat.id)

async def on_new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            chat = update.effective_chat
            await register_group(chat.id, chat.title or "Группа")
            try:
                await update.message.reply_text(
                    f"✅ *{APP_NAME}* подключён!\n"
                    "Читаю сообщения. /check @username — проверить пользователя.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

async def on_left_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.left_chat_member.id == context.bot.id:
        await unregister_group(update.effective_chat.id)

# ── INLINE QUERY ─────────────────────────────────────────────────────────────

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query or len(query) < 2:
        return
    results_bl = await search_blacklist(query)
    answers = []
    for s in results_bl[:5]:
        caption = format_scammer(s)
        answers.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=f"⚠️ {s.get('name','?')} {s.get('username','')}",
                description=s.get("reason","")[:80],
                input_message_content=InputTextMessageContent(message_text=caption, parse_mode="Markdown")
            )
        )
    if not answers:
        answers.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=f"✅ {query} — не в базе",
                description="Пользователь не найден в базе WTS ScamList",
                input_message_content=InputTextMessageContent(
                    message_text=f"✅ *{query}* не найден в базе {APP_NAME}. 🛡",
                    parse_mode="Markdown"
                )
            )
        )
    await update.inline_query.answer(answers, cache_time=0)

# ── POST INIT ────────────────────────────────────────────────────────────────

async def sync_usernames_loop():
    await asyncio.sleep(30)
    while True:
        try:
            print("🔄 Синхронизация юзернеймов...")
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{BACKEND_URL}/blacklist/sync-usernames")
                d = r.json()
            print(f"✅ Sync: обновлено {d.get('updated',0)}, ошибок {d.get('errors',0)}")
        except Exception as e:
            print(f"❌ Sync error: {e}")
        await asyncio.sleep(5 * 60)

async def post_init(app):
    print("Syncing groups from DB...")
    try:
        data = await _get("/groups")
        groups = data.get("groups", [])
        print(f"Found {len(groups)} groups in DB")
        for group in groups:
            chat_id = group["chat_id"]
            try:
                chat = await app.bot.get_chat(chat_id)
                if chat.title != group.get("title"):
                    await register_group(chat_id, chat.title)
            except Exception as e:
                print(f"Group {chat_id} unavailable: {e}")
                await unregister_group(chat_id)
    except Exception as e:
        print(f"Sync error: {e}")
    asyncio.create_task(sync_usernames_loop())

# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Личка
    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("check",       cmd_check))
    app.add_handler(CommandHandler("users",       cmd_users))
    app.add_handler(CommandHandler("pin",         cmd_pin))
    app.add_handler(CommandHandler("unpin",       cmd_unpin))
    app.add_handler(CommandHandler("addhostile",  cmd_add_hostile))
    app.add_handler(CommandHandler("listhostile", cmd_list_hostile))
    app.add_handler(CommandHandler("rmhostile",   cmd_remove_hostile))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        private_message
    ))

    # Группы
    app.add_handler(MessageHandler(
        filters.TEXT & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        group_message
    ))

    # Статус бота
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_chat))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_chat))

    # Inline
    app.add_handler(InlineQueryHandler(inline_query))

    print(f"🤖 {APP_NAME} Bot запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
