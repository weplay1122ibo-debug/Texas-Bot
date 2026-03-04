import asyncio
import logging
import os
import asyncpg
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ================= CONFIG =================
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 7717061636
WEBHOOK_PATH = "/webhook"

DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

db_pool = None
user_temp = {}

HANDS = [
    "👥 زوجين",
    "🔗 متتالية",
    "🎴 ثلاثة",
    "♠️ فلش",
    "🏠 فل هاوس",
    "🂡 أربعة",
    "🌟 ستريت فلش"
]

# ================= DATABASE =================
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

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
            expire TIMESTAMP
        )
        """)

# ================= AI ENGINE =================
async def train_ai(side, rank, suit, prev, result):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO training (side, rank, suit, prev, result)
            VALUES ($1,$2,$3,$4,$5)
        """, side, rank, suit, prev, result)

async def predict_hand(side, rank, suit, prev):
    scores = {h: 1 for h in HANDS}

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT rank, suit, prev, result
            FROM training
            WHERE side = $1
            ORDER BY id DESC
            LIMIT 1500
        """, side)

    for r in rows:
        r_rank = r["rank"]
        r_suit = r["suit"]
        r_prev = r["prev"]
        r_result = r["result"]

        if r_rank == rank and r_suit == suit and r_prev == prev:
            scores[r_result] += 25
        elif r_rank == rank and r_prev == prev:
            scores[r_result] += 12
        elif r_prev == prev:
            scores[r_result] += 9
        elif r_rank == rank:
            scores[r_result] += 4
        else:
            scores[r_result] += 1

    return max(scores, key=scores.get)

# ================= SUBSCRIPTION =================
async def check_subscription(user_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT expire FROM users WHERE user_id=$1", str(user_id))
    if not row:
        return False
    return row["expire"] > datetime.now()

async def activate_user(user_id, days=3650):
    expire = datetime.now() + timedelta(days=days)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, expire)
            VALUES ($1,$2)
            ON CONFLICT (user_id)
            DO UPDATE SET expire=$2
        """, str(user_id), expire)

# ================= KEYBOARDS =================
def ranks_kb():
    ranks = ["A","K","Q","J","10","9","8","7","6","5","4","3","2"]
    rows = [ranks[i:i+4] for i in range(0, len(ranks), 4)]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=r, callback_data=f"rank_{r}") for r in row] for row in rows]
    )

def suits_kb():
    suits = ["♥️","♦️","♣️","♠️"]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=s, callback_data=f"suit_{s}") for s in suits]]
    )

def hands_kb(prefix):
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=h, callback_data=f"{prefix}_{h}")] for h in HANDS]
    )

# ================= FLOW =================
@dp.message(CommandStart())
async def start(message: Message):
    if message.from_user.id == ADMIN_ID:
        await activate_user(ADMIN_ID)
    await message.answer("🔥 TEXAS AI CLOUD V10\nاختر رقم الورقة:", reply_markup=ranks_kb())

@dp.callback_query(lambda c: c.data.startswith("rank_"))
async def choose_rank(callback: CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id] = {"rank": callback.data.split("_")[1]}
    await callback.message.edit_text("اختر النوع:", reply_markup=suits_kb())

@dp.callback_query(lambda c: c.data.startswith("suit_"))
async def choose_suit(callback: CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id]["suit"] = callback.data.split("_")[1]
    await callback.message.edit_text("الضربة السابقة؟", reply_markup=hands_kb("prev"))

@dp.callback_query(lambda c: c.data.startswith("prev_"))
async def handle_prev(callback: CallbackQuery):
    await callback.answer()

    user_id = callback.from_user.id
    if not await check_subscription(user_id):
        await callback.message.edit_text("❌ غير مشترك")
        return

    prev = callback.data.replace("prev_", "")
    data = user_temp[user_id]

    left_pred = await predict_hand("left", data["rank"], data["suit"], prev)
    right_pred = await predict_hand("right", data["rank"], data["suit"], prev)

    if user_id == ADMIN_ID:
        user_temp[user_id]["prev"] = prev
        await callback.message.edit_text(
            f"⬅️ يسار: {left_pred}\n➡️ يمين: {right_pred}\n\nادخل نتيجة اليسار:",
            reply_markup=hands_kb("train_left")
        )
    else:
        await callback.message.edit_text(
            f"🔥 TEXAS AI V10 CLOUD\n\n⬅️ يسار: {left_pred}\n➡️ يمين: {right_pred}"
        )

# ================= WEBHOOK =================
async def main():
    logging.basicConfig(level=logging.INFO)

    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp)

    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    webhook_base = os.getenv("WEBHOOK_URL")
    if webhook_base:
        await bot.set_webhook(webhook_base.rstrip("/") + WEBHOOK_PATH)

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
