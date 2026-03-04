import asyncio
import logging
import os
import asyncpg
import secrets
from datetime import datetime, timedelta

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

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
db_pool = None
user_temp = {}

# مطابق للعبة بالصورة
HANDS = [
    "👥 زوج",
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
    expire = datetime.now() + timedelta(days=days)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, expire, plan)
            VALUES ($1,$2,$3)
            ON CONFLICT (user_id)
            DO UPDATE SET expire=$2, plan=$3
        """, str(user_id), expire, plan)

# ================= ADMIN =================
@dp.message(Command("create_code"))
async def create_code(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("استخدم:\n/create_code 30 VIP")
        return

    days = int(parts[1])
    plan = parts[2].upper()
    code = secrets.token_hex(4).upper()

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO codes (code, days, plan)
            VALUES ($1,$2,$3)
        """, code, days, plan)

    await message.answer(f"✅ كود جديد:\n{code}\n\n📅 {days} يوم\n💎 الخطة: {plan}")

@dp.message(Command("stats"))
async def stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        training = await conn.fetchval("SELECT COUNT(*) FROM training")

    await message.answer(
        f"""
🇮🇶 Texas Iraq Bot - Stats

👥 المشتركين: {users}
🧠 بيانات التدريب: {training}
"""
    )

# ================= USE CODE =================
@dp.message(Command("code"))
async def use_code(message: Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("استخدم:\n/code XXXXX")
        return

    code = parts[1].upper()

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT days, plan FROM codes
            WHERE code=$1 AND used=FALSE
        """, code)

        if not row:
            await message.answer("❌ كود غير صالح أو مستخدم")
            return

        await conn.execute("UPDATE codes SET used=TRUE WHERE code=$1", code)

    await activate_user(message.from_user.id, row["days"], row["plan"])
    await message.answer(f"🔥 تم التفعيل\n💎 خطتك: {row['plan']}")

# ================= AI =================
async def train_ai(side, rank, suit, prev, result):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO training (side, rank, suit, prev, result)
            VALUES ($1,$2,$3,$4,$5)
        """, side, rank, suit, prev, result)

async def predict_hand(side, rank, suit, prev):
    scores = {h: 0 for h in HANDS}
    total = 0

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT rank, suit, prev, result
            FROM training
            WHERE side=$1
        """, side)

    for r in rows:
        weight = 0

        if r["rank"] == rank:
            weight += 3
        if r["suit"] == suit:
            weight += 3
        if r["prev"] == prev:
            weight += 5

        if weight > 0:
            if r["result"] in scores:
                scores[r["result"]] += weight
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

def hands_kb(prefix):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=h, callback_data=f"{prefix}_{h}")]
            for h in HANDS
        ]
    )

# ================= FLOW =================
@dp.message(CommandStart())
async def start(message: Message):
    if not await check_subscription(message.from_user.id):
        await message.answer("❌ لازم تدخل كود اشتراك\n/code XXXXX")
        return

    await message.answer("🇮🇶 Texas Iraq Bot\n\nاختر رقم الورقة:", reply_markup=ranks_kb())

@dp.callback_query(lambda c: c.data.startswith("rank_"))
async def choose_rank(callback: CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id] = {"rank": callback.data.split("_")[1]}
    await callback.message.edit_text("اختر النوع:", reply_markup=suits_kb())

@dp.callback_query(lambda c: c.data.startswith("suit_"))
async def choose_suit(callback: CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id]["suit"] = callback.data.split("_")[1]
    await callback.message.edit_text("الضربة السابقة:", reply_markup=hands_kb("prev"))

@dp.callback_query(lambda c: c.data.startswith("prev_"))
async def handle_prev(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    if not await check_subscription(user_id):
        await callback.message.edit_text("❌ الاشتراك منتهي")
        return

    prev = callback.data.replace("prev_", "")
    data = user_temp.get(user_id)

    if not data:
        await callback.message.edit_text("ابدأ من جديد /start")
        return

    left_pred, left_conf = await predict_hand("left", data["rank"], data["suit"], prev)
    right_pred, right_conf = await predict_hand("right", data["rank"], data["suit"], prev)

    if user_id == ADMIN_ID:
        user_temp[user_id]["prev"] = prev
        await callback.message.edit_text(
            f"""
🇮🇶 Texas Iraq Bot

⬅️ يسار: {left_pred} ({left_conf}%)
➡️ يمين: {right_pred} ({right_conf}%)

ادخل نتيجة اليسار:
""",
            reply_markup=hands_kb("train_left")
        )
    else:
        await callback.message.edit_text(
            f"""
🇮🇶 Texas Iraq Bot

⬅️ يسار: {left_pred}
📊 الثقة: {left_conf}%

➡️ يمين: {right_pred}
📊 الثقة: {right_conf}%
"""
        )

@dp.callback_query(lambda c: c.data.startswith("train_left_"))
async def train_left(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    user_temp[ADMIN_ID]["left"] = callback.data.replace("train_left_", "")
    await callback.message.edit_text("ادخل نتيجة اليمين:", reply_markup=hands_kb("train_right"))

@dp.callback_query(lambda c: c.data.startswith("train_right_"))
async def train_right(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    right = callback.data.replace("train_right_", "")
    data = user_temp[ADMIN_ID]

    await train_ai("left", data["rank"], data["suit"], data["prev"], data["left"])
    await train_ai("right", data["rank"], data["suit"], data["prev"], right)

    await callback.message.edit_text("✅ تم تدريب الذكاء بنجاح")
    user_temp.pop(ADMIN_ID, None)

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

    webhook_base = os.environ["WEBHOOK_URL"]
    await bot.set_webhook(webhook_base.rstrip("/") + WEBHOOK_PATH)

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
