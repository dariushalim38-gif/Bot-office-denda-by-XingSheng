import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8716960621:AAG7cFdVeb0Tio7lBBMoUSfiH32VpCqjfL8"
logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot hidup! Kirim /test")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Test berhasil!")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
    print("Bot aktif.")
    app.run_polling()

if __name__ == "__main__":
    main()
