import asyncio, os, asyncpg, secrets, random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ────────────────────────────────────────────────
# التصحيح المهم جدًا لـ aiogram على Render (parse_mode)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ================= CONFIG =================
API_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_PATH = "/webhook"
ADMIN_ID = 7717061636
TRAINER_IDS = []

SAUDI_TZ = ZoneInfo("Asia/Riyadh")

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()
db_pool = None
user_temp = {}

# باقي الكود كامل بدون تغيير (من GAME HANDS إلى النهاية)
# ... (انسخ كل باقي الكود اللي عندك هنا)

# في النهاية تأكد من وجود:
if __name__ == "__main__":
    asyncio.run(main())
