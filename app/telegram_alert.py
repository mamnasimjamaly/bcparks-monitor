import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


async def notify(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram is not configured; skipping notification.")
        print(message)
        return

    bot = Bot(BOT_TOKEN)

    await bot.send_message(
        chat_id=CHAT_ID,
        text=message
    )