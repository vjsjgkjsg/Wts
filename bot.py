import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "8274782796:AAFBK4sJpQhtXnIE9IxOMmNhivlM2dXEgp4")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://YOUR_GITHUB_PAGES_URL")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🛡 Открыть WTS Blacklist", web_app=WebAppInfo(url=MINIAPP_URL))]]
    await update.message.reply_text(
        "👋 Добро пожаловать в *WTS Escrow Blacklist*\n\n"
        "🔍 Проверяй пользователей перед сделкой\n"
        "🛡 База проверенных скамеров\n"
        "⚡️ Обновляется администрацией\n\n"
        "Нажми кнопку ниже чтобы открыть базу:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    print("WTS Bot запущен...")
    app.run_polling()
