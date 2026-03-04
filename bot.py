import asyncio
import logging
import os
import asyncpg
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ================= CONFIG =================
API_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_PATH = "/webhook"
ADMIN_ID = 7717061636

SAUDI_TZ = ZoneInfo("Asia/Riyadh")

SPECIAL_MINUTES = [
    1,5,6,8,9,16,17,21,23,27,28,29,
    35,36,41,45,47,51,53,55,57,58,59
]

ALERT_BEFORE = 2  # عدد الدقائق قبل الدقيقة المستهدفة

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
db_pool = None
user_temp = {}
last_alert_key = None

# ================= GAME HANDS =================

LEFT_HANDS = [
    "❌ لا شيء",
    "♠️ متتالية من نفس النوع",
    "👥 زوج",
    "🅰️ AA"
]

RIGHT_HANDS = [
    "👥 زوجين",
    "🔗 متتالية",
    "🎴 ثلاثة",
    "🏠 فل هاوس",
    "🂡 أربعة"
]

# ================= DATABASE =================

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, ssl="require")

    async with db_pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS training (
            id SERIAL PRIMARY KEY,
            side TEXT,
            rank TEXT,
            suit TEXT,
            prev TEXT,
            result TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            expire TIMESTAMP,
            plan TEXT
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS codes (
            code TEXT PRIMARY KEY,
            days INTEGER,
            plan TEXT,
            used BOOLEAN DEFAULT FALSE
        )
        """)

# ================= SUBSCRIPTION =================

async def check_subscription(user_id):
    if user_id == ADMIN_ID:
        return True

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT expire FROM users WHERE user_id=$1",
            str(user_id)
        )

    if not row:
        return False

    return row["expire"] > datetime.now()

async def activate_user(user_id, days, plan):
    async with db_pool.acquire() as conn:
        expire = datetime.now() + timedelta(days=days)

        await conn.execute("""
            INSERT INTO users (user_id, expire, plan)
            VALUES ($1,$2,$3)
            ON CONFLICT (user_id)
            DO UPDATE SET expire=$2, plan=$3
        """, str(user_id), expire, plan)

# ================= ALERT SYSTEM =================

async def send_alerts():
    global last_alert_key

    while True:
        now = datetime.now(SAUDI_TZ)
        current_minute = now.minute
        current_hour = now.hour

        target_minute = (current_minute + ALERT_BEFORE) % 60
        alert_key = f"{current_hour}:{target_minute}"

        if target_minute in SPECIAL_MINUTES and alert_key != last_alert_key:

            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT user_id FROM users WHERE expire > NOW()"
                )

            for row in rows:
                try:
                    await bot.send_message(
                        int(row["user_id"]),
                        f"""⏰ تنبيه ذكي

ينصح بلعب خماسي خلال {ALERT_BEFORE} دقيقة 🔥

🎯 الدقيقة المستهدفة:
{target_minute}

استعد الآن..."""
                    )
                except:
                    pass

            last_alert_key = alert_key
            await asyncio.sleep(60)
        else:
            await asyncio.sleep(20)

# ================= AI =================

async def train_ai(side, rank, suit, prev, result):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO training (side, rank, suit, prev, result)
            VALUES ($1,$2,$3,$4,$5)
        """, side, rank, suit, prev, result)

async def predict_hand(side, rank, suit, prev, hands_list):
    scores = {h: 0 for h in hands_list}
    total = 0

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT rank, suit, prev, result FROM training WHERE side=$1",
            side
        )

    for r in rows:
        weight = 0

        if r["rank"] == rank:
            weight += 3
        if r["suit"] == suit:
            weight += 3
        if r["prev"] == prev:
            weight += 5

        if weight > 0:
            results = r["result"].split(",")
            for res in results:
                if res in scores:
                    scores[res] += weight
                    total += weight

    if total == 0:
        return "لا يوجد بيانات", 0

    best = max(scores, key=scores.get)
    confidence = int((scores[best] / total) * 100)

    return best, confidence

# ================= KEYBOARDS =================

def ranks_kb():
    ranks = ["A","K","Q","J","10","9","8","7","6","5","4","3","2"]
    rows = [ranks[i:i+4] for i in range(0, len(ranks), 4)]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=r, callback_data=f"rank_{r}") for r in row]
            for row in rows
        ]
    )

def suits_kb():
    suits = ["♥️","♦️","♣️","♠️"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=s, callback_data=f"suit_{s}") for s in suits]
        ]
    )

def hands_kb(prefix, hands_list):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=h, callback_data=f"{prefix}_{h}")]
            for h in hands_list
        ]
    )

# ================= GAME FLOW =================

@dp.message(CommandStart())
async def start(message: Message):
    if not await check_subscription(message.from_user.id):
        await message.answer("❌ لازم تدخل كود اشتراك\n/code XXXXX")
        return

    await message.answer("اختر رقم الورقة:", reply_markup=ranks_kb())

@dp.callback_query(lambda c: c.data.startswith("rank_"))
async def choose_rank(callback: CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id] = {"rank": callback.data.split("_")[1]}
    await callback.message.edit_text("اختر النوع:", reply_markup=suits_kb())

@dp.callback_query(lambda c: c.data.startswith("suit_"))
async def choose_suit(callback: CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id]["suit"] = callback.data.split("_")[1]
    await callback.message.edit_text(
        "الضربة السابقة:",
        reply_markup=hands_kb("prev", RIGHT_HANDS)
    )

# ================= WEBHOOK =================

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()

    asyncio.create_task(send_alerts())

    await bot.delete_webhook(drop_pending_updates=True)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp)

    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    webhook_base = os.environ["WEBHOOK_URL"]
    await bot.set_webhook(webhook_base.rstrip("/") + WEBHOOK_PATH)

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
