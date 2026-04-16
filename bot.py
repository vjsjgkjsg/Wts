"""
WTS ScamList Bot — ФИНАЛЬНАЯ ВЕРСИЯ
• В группах реагирует ТОЛЬКО на скам-слово + сумма
• Все ответы — ФОТО-карточка
• Личка: любой текст = поиск
• Команды только: /start /check /stats
• Автосинхронизация каждые 5 мин
• Жалобы → сохраняются в БД + пересылаются админам
"""
import os, io, re, asyncio, uuid, httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo, InlineQueryResultArticle, InputTextMessageContent, ChatMember
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    InlineQueryHandler, ChatMemberHandler, ContextTypes, filters
)

BOT_TOKEN   = os.getenv("BOT_TOKEN",   "8650752955:AAH0IiWv9SNYHTbPcEGitj8oRIsLh-EmCGw")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://vjsjgkjsg.github.io/Wtss/")
BACKEND_URL = os.getenv("BACKEND_URL", "https://web-production-acde3.up.railway.app")
BOT_UNAME   = "WTSBlaskListBot"
APP_NAME    = "WTS ScamList"
ADMIN_IDS   = [int(x.strip()) for x in os.getenv("ADMIN_IDS","7810141735").split(",") if x.strip().isdigit()]

# ── ТРИГГЕРЫ (только скам + сумма) ───────────────────────────────────────────
SCAM_WORDS = [
    "скам","scam","скамер","скамеры","scammer","мошенник","мошенница","мошенники",
    "кинул","кинула","кинули","кидает","кидала","кидок","кидала",
    "развёл","развела","развели","разводит","развод",
    "аферист","аферистка","жулик","вор","воровство","кража",
    "обманул","обманула","обманули","обманывает","обман",
    "не вернул","не вернула","не отдал","не отдала",
    "не заплатил","не платит","забрал деньги","забрала деньги",
    "взял деньги и пропал","взяла деньги и пропала",
    "fraud","fraudster","шантаж","вымогатель","вымогает",
]
AMOUNT_RE = re.compile(
    r"(\d[\d\s.,]*\s*(?:usdt|usdc|btc|eth|ton|usd|eur|руб|₽|\$|€|тг|uah))"
    r"|(\d[\d\s]{2,})\s*(?:монет|крипты|баксов|долларов|рублей|юсдт)",
    re.IGNORECASE
)

def _has_scam(t): return any(w in t.lower() for w in SCAM_WORDS)
def _has_amount(t): return bool(AMOUNT_RE.search(t))
def should_react(t): return _has_scam(t) and _has_amount(t)

# ── API ───────────────────────────────────────────────────────────────────────
async def _get(path):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            return (await c.get(f"{BACKEND_URL}{path}")).json()
    except Exception as e:
        print(f"GET {path}: {e}"); return {}

async def _post(path, payload):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            return (await c.post(f"{BACKEND_URL}{path}", json=payload)).json()
    except Exception as e:
        print(f"POST {path}: {e}"); return {}

# ── ПОИСК ────────────────────────────────────────────────────────────────────
def _lev(a, b):
    m,n=len(a),len(b); dp=list(range(n+1))
    for i in range(1,m+1):
        prev,dp[0]=dp[0],i
        for j in range(1,n+1):
            prev,dp[j]=dp[j],prev if a[i-1]==b[j-1] else 1+min(dp[j],dp[j-1],prev)
    return dp[n]

def _fuzzy(a,b):
    if not a or not b: return False
    if b in a or a in b: return True
    if len(a)>=5 and len(b)>=4: return _lev(a,b)<=(2 if len(b)>=7 else 1)
    return False

async def search_bl(query):
    data = await _get("/blacklist")
    rows = data.get("blacklist", [])
    q = query.lower().replace("@","").strip()
    if len(q) < 2: return []
    seen, out = set(), []
    for s in rows:
        sid = s.get("id")
        if sid in seen: continue
        nm = (s.get("name") or "").lower()
        un = (s.get("username") or "").lower().replace("@","")
        if q in nm or q in un or _fuzzy(nm,q) or _fuzzy(un,q):
            seen.add(sid); out.append(s)
    out.sort(key=lambda x: (not x.get("pinned"), -(x.get("id") or 0)))
    return out

# ── КАРТОЧКА ─────────────────────────────────────────────────────────────────
def _make_card(s):
    try:
        from generate_card import generate_card_bytes
        return generate_card_bytes(s)
    except Exception as e:
        print(f"Card: {e}"); return None

def _cap(s):
    tm={"high":"🔴 СКАМЕР","med":"🟠 ПОДОЗРИТЕЛЬНЫЙ","low":"🔵 ОСТОРОЖНО"}
    sm={"active":"🔴 Активен","blocked":"🔒 Заблокирован"}
    pin="📌 ЗАКРЕПЛЕНО\n" if s.get("pinned") else ""
    lines=[f"{pin}⚠️ НАЙДЕН В БАЗЕ {APP_NAME.upper()}","",
           f"👤 {s.get('name','?')}  {s.get('username','')}",
           f"📊 {tm.get(s.get('threat','high'),'?')}",
           f"📌 {sm.get(s.get('status','active'),'?')}","",
           f"📋 {s.get('reason','—')}"]
    if s.get("amount"): lines.append(f"💸 Ущерб: {s['amount']} {s.get('currency','USDT')}")
    if s.get("victims"): lines.append(f"👥 Жертв: {s['victims']} чел.")
    lines+=["",f"🛡 {APP_NAME} — проверяй перед сделкой"]
    return "\n".join(lines)

async def send_card(msg, s, kb=None):
    card=_make_card(s); cap=_cap(s)[:1024]; kw={"parse_mode":"Markdown"}
    if kb: kw["reply_markup"]=kb
    try:
        if card: await msg.reply_photo(photo=io.BytesIO(card),caption=cap,**kw)
        else: await msg.reply_text(text=cap,**kw)
    except Exception as e:
        print(f"send_card: {e}")
        try: await msg.reply_text(text=cap,**kw)
        except: pass

async def bot_send_card(bot, chat_id, s, kb=None):
    card=_make_card(s); cap=_cap(s)[:1024]; kw={"parse_mode":"Markdown"}
    if kb: kw["reply_markup"]=kb
    try:
        if card: await bot.send_photo(chat_id=chat_id,photo=io.BytesIO(card),caption=cap,**kw)
        else: await bot.send_message(chat_id=chat_id,text=cap,**kw)
    except Exception as e:
        print(f"bot_send_card: {e}")
        try: await bot.send_message(chat_id=chat_id,text=cap,**kw)
        except: pass

# ── КНОПКИ ───────────────────────────────────────────────────────────────────
def kb_p(): return InlineKeyboardMarkup([[InlineKeyboardButton(f"🛡 Открыть {APP_NAME}",web_app=WebAppInfo(url=MINIAPP_URL))]])
def kb_g(): return InlineKeyboardMarkup([[InlineKeyboardButton(f"🛡 Открыть {APP_NAME}",url=f"https://t.me/{BOT_UNAME}?startapp=1")]])

# ── ПОИСК + ВЫВОД ────────────────────────────────────────────────────────────
async def do_search(msg, query, is_group=False):
    q=query.lstrip("@").strip()
    if len(q)<2: await msg.reply_text("❌ Слишком короткий запрос."); return
    results=await search_bl(q); kb=kb_g() if is_group else kb_p()
    if not results:
        await msg.reply_text(f"✅ *{query}* — не найден в базе {APP_NAME}.\n\nПроверяй перед сделкой! 🛡",
                             parse_mode="Markdown",reply_markup=kb); return
    for s in results[:3]: await send_card(msg,s,kb)
    if len(results)>3: await msg.reply_text(f"📋 Ещё {len(results)-3} записей — открой приложение.",reply_markup=kb)

# ── ПЕРЕСЫЛКА ЖАЛОБ ──────────────────────────────────────────────────────────
async def forward_to_admins(bot, msg, group_title):
    sender=msg.from_user
    if not sender: return
    sn=(sender.first_name or "")+(" "+sender.last_name if sender.last_name else "")
    su=f"@{sender.username}" if sender.username else f"ID:{sender.id}"
    cap=(f"🔔 *Жалоба из группы*\n{'─'*26}\n"
         f"👥 {group_title}\n👤 [{sn}](tg://user?id={sender.id}) {su}\n{'─'*26}\n\n"
         f"📋 {msg.text or '—'}")
    mentioned=re.findall(r'@[\w]{3,}',msg.text or "")
    await _post("/groups/scam-report",{
        "chat_id":msg.chat_id,"chat_title":group_title,
        "sender_id":sender.id,"sender_name":sn,"sender_username":su,
        "message_text":(msg.text or "")[:2000],"mentioned_users":", ".join(mentioned)
    })
    for aid in ADMIN_IDS:
        try:
            if msg.photo: await bot.send_photo(chat_id=aid,photo=msg.photo[-1].file_id,caption=cap,parse_mode="Markdown")
            elif msg.document: await bot.send_document(chat_id=aid,document=msg.document.file_id,caption=cap,parse_mode="Markdown")
            else: await bot.send_message(chat_id=aid,text=cap,parse_mode="Markdown")
        except Exception as e: print(f"fwd admin {aid}: {e}")

# ── ГРУППЫ ───────────────────────────────────────────────────────────────────
async def reg_group(cid,title,hostile=False): await _post("/groups/add",{"chat_id":cid,"title":title,"hostile":hostile})
async def unreg_group(cid): await _post("/groups/remove",{"chat_id":cid})

# ── HANDLERS ─────────────────────────────────────────────────────────────────
async def cmd_start(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text(
        f"🛡 *{APP_NAME}*\n\nОфициальная база мошенников.\n\n"
        "Напиши *@username* или *имя* — я проверю по базе.\n"
        "👇 Или открой базу кнопкой:",
        parse_mode="Markdown",reply_markup=kb_p()
    )

async def cmd_check(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    is_g=upd.effective_chat.type in("group","supergroup")
    q=" ".join(ctx.args).strip() if ctx.args else ""
    if not q:
        await upd.effective_message.reply_text("📌 Пример: `/check @username`",parse_mode="Markdown"); return
    await do_search(upd.effective_message,q,is_group=is_g)

async def cmd_stats(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    is_g=upd.effective_chat.type in("group","supergroup")
    data=await _get("/blacklist"); bl=data.get("blacklist",[])
    total=len(bl); high=sum(1 for x in bl if x.get("threat")=="high")
    victims=sum(int(x.get("victims")or 0) for x in bl)
    await upd.effective_message.reply_text(
        f"📊 *{APP_NAME}*\n\n🔴 Скамеров: {high}\n📋 Записей: {total}\n👥 Жертв: {victims}\n\n🛡 Проверяй перед сделкой",
        parse_mode="Markdown",reply_markup=kb_g() if is_g else kb_p()
    )

async def private_msg(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    await do_search(upd.message,upd.message.text.strip(),is_group=False)

async def group_msg(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    msg=upd.effective_message
    if not msg or not msg.text: return
    text=msg.text.strip()
    # команды в группе
    if re.match(r'^/check',text,re.IGNORECASE):
        q=re.sub(r'^/check\S*\s*','',text,flags=re.IGNORECASE).strip()
        if q: await do_search(msg,q,is_group=True)
        return
    if re.match(r'^/stats',text,re.IGNORECASE):
        await cmd_stats(upd,ctx); return
    # главный триггер
    if not should_react(text): return
    group_title=upd.effective_chat.title or "Группа"
    await forward_to_admins(ctx.bot,msg,group_title)
    usernames=re.findall(r'@[\w]{3,}',text)
    if usernames:
        found=False
        for un in usernames[:3]:
            res=await search_bl(un.lstrip("@"))
            if res:
                found=True
                for s in res[:2]: await send_card(msg,s,kb_g())
        if not found:
            await msg.reply_text(f"🛡 *{APP_NAME}*\n\nЗамечена жалоба. Проверь пользователя:",
                                 parse_mode="Markdown",reply_markup=kb_g())
    else:
        await msg.reply_text("🚨 Замечена жалоба с суммой.\nУкажи @username для проверки:",reply_markup=kb_g())

async def on_my_chat_member(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    r=upd.my_chat_member
    if not r: return
    chat=r.chat; st=r.new_chat_member.status
    if st in(ChatMember.MEMBER,ChatMember.ADMINISTRATOR):
        await reg_group(chat.id,chat.title or "Группа")
        try:
            await ctx.bot.send_message(chat.id,
                f"✅ *{APP_NAME}* подключён!\nРеагирую на жалобы со скамом и суммой.\n/check @username — проверить.",
                parse_mode="Markdown")
        except: pass
    elif st in(ChatMember.LEFT,ChatMember.BANNED):
        await unreg_group(chat.id)

async def on_new_members(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    for m in upd.message.new_chat_members:
        if m.id==ctx.bot.id:
            chat=upd.effective_chat
            await reg_group(chat.id,chat.title or "Группа")
            try: await upd.message.reply_text(f"✅ *{APP_NAME}* подключён! /check @username",parse_mode="Markdown")
            except: pass

async def on_left_chat(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    m=upd.message.left_chat_member
    if m and m.id==ctx.bot.id: await unreg_group(upd.effective_chat.id)

async def inline_q(upd:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=upd.inline_query.query.strip()
    if not q or len(q)<2: return
    results=await search_bl(q); answers=[]
    for s in results[:5]:
        answers.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),title=f"⚠️ {s.get('name','?')} {s.get('username','')}",
            description=(s.get("reason")or "")[:80],
            input_message_content=InputTextMessageContent(message_text=_cap(s),parse_mode="Markdown")
        ))
    if not answers:
        answers.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),title=f"✅ {q} — не найден",description="Пользователь не в базе",
            input_message_content=InputTextMessageContent(
                message_text=f"✅ *{q}* не найден в базе {APP_NAME}. 🛡",parse_mode="Markdown")
        ))
    await upd.inline_query.answer(answers,cache_time=0)

# ── ФОНОВЫЕ ЗАДАЧИ ───────────────────────────────────────────────────────────
async def _sync_loop():
    await asyncio.sleep(30)
    while True:
        try:
            d=await _get("/blacklist/sync-usernames")
            print(f"🔄 Sync: +{d.get('updated',0)} ошибок {d.get('errors',0)}")
        except Exception as e: print(f"sync: {e}")
        await asyncio.sleep(300)

async def post_init(application):
    try:
        data=await _get("/groups"); groups=data.get("groups",[])
        for g in groups:
            cid=g["chat_id"]
            try:
                chat=await application.bot.get_chat(cid)
                if chat.title!=g.get("title"): await reg_group(cid,chat.title)
            except: await unreg_group(cid)
        print(f"✅ Групп: {len(groups)}")
    except Exception as e: print(f"post_init: {e}")
    asyncio.create_task(_sync_loop())

# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    app=(ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build())
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CommandHandler("check",cmd_check))
    app.add_handler(CommandHandler("stats",cmd_stats))
    app.add_handler(MessageHandler(filters.TEXT&filters.ChatType.PRIVATE&~filters.COMMAND,private_msg))
    app.add_handler(MessageHandler(filters.TEXT&(filters.ChatType.GROUP|filters.ChatType.SUPERGROUP),group_msg))
    app.add_handler(ChatMemberHandler(on_my_chat_member,ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS,on_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER,on_left_chat))
    app.add_handler(InlineQueryHandler(inline_q))
    print(f"🤖 {APP_NAME} запущен.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
