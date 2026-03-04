import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ================== CONFIG ==================
API_TOKEN = "PUT_YOUR_TOKEN"
ADMIN_ID = 7717061636

WEBHOOK_PATH = "/webhook"
DB_FILE = "/var/data/texas_ai_v10.db"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

user_temp = {}

# ================== DATABASE ==================
def init_db():
    os.makedirs("/var/data", exist_ok=True)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS training (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        side TEXT,
        rank TEXT,
        suit TEXT,
        prev TEXT,
        result TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        expire DATETIME
    )
    """)

    conn.commit()
    conn.close()

# ================== AI ENGINE V10 ==================
HANDS = [
    "👥 زوجين",
    "🔗 متتالية",
    "🎴 ثلاثة",
    "♠️ فلش",
    "🏠 فل هاوس",
    "🂡 أربعة",
    "🌟 ستريت فلش"
]

def train_ai(side, rank, suit, prev, result):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        INSERT INTO training (side, rank, suit, prev, result)
        VALUES (?, ?, ?, ?, ?)
    """, (side, rank, suit, prev, result))

    conn.commit()
    conn.close()

def predict_hand(side, rank, suit, prev):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    scores = {h: 1 for h in HANDS}

    c.execute("""
        SELECT rank, suit, prev, result
        FROM training
        WHERE side = ?
        ORDER BY id DESC
        LIMIT 1500
    """, (side,))

    rows = c.fetchall()
    conn.close()

    for r_rank, r_suit, r_prev, r_result in rows:

        # تطابق كامل
        if r_rank == rank and r_suit == suit and r_prev == prev:
            scores[r_result] += 25

        # rank + prev
        elif r_rank == rank and r_prev == prev:
            scores[r_result] += 12

        # prev فقط (Markov effect)
        elif r_prev == prev:
            scores[r_result] += 9

        # rank فقط
        elif r_rank == rank:
            scores[r_result] += 4

        else:
            scores[r_result] += 1

    sorted_hands = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_hands[0][0]

# ================== SUBSCRIPTION ==================
def check_subscription(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT expire FROM users WHERE user_id = ?", (str(user_id),))
    row = c.fetchone()
    conn.close()

    if not row:
        return False

    return datetime.fromisoformat(row[0]) > datetime.now()

def activate_user(user_id, days=30):
    expire = datetime.now() + timedelta(days=days)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("REPLACE INTO users (user_id, expire) VALUES (?, ?)",
              (str(user_id), expire.isoformat()))
    conn.commit()
    conn.close()

# ================== KEYBOARDS ==================
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

# ================== FLOW ==================
@dp.message(CommandStart())
async def start(message: Message):
    if message.from_user.id == ADMIN_ID:
        activate_user(ADMIN_ID, 3650)
    await message.answer("🔥 TEXAS AI V10\nاختر رقم الورقة:", reply_markup=ranks_kb())

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
    if not check_subscription(user_id):
        await callback.message.edit_text("❌ غير مشترك")
        return

    prev = callback.data.replace("prev_", "")
    data = user_temp.get(user_id)

    rank = data["rank"]
    suit = data["suit"]

    left_pred = predict_hand("left", rank, suit, prev)
    right_pred = predict_hand("right", rank, suit, prev)

    if user_id == ADMIN_ID:
        user_temp[user_id] = {
            "mode": "train",
            "rank": rank,
            "suit": suit,
            "prev": prev
        }

        await callback.message.edit_text(
            f"🎯 توقعات V10\n\n⬅️ يسار: {left_pred}\n➡️ يمين: {right_pred}\n\nادخل نتيجة اليسار:",
            reply_markup=hands_kb("train_left")
        )
    else:
        await callback.message.edit_text(
            f"""🔥 TEXAS AI V10 PRO

⬅️ يسار: {left_pred}
➡️ يمين: {right_pred}

💎 ذكاء متطور - تدريب حصري"""
        )

@dp.callback_query(lambda c: c.data.startswith("train_left_"))
async def train_left(callback: CallbackQuery):
    await callback.answer()
    user_temp[ADMIN_ID]["left"] = callback.data.replace("train_left_", "")
    await callback.message.edit_text("ادخل نتيجة اليمين:",
                                     reply_markup=hands_kb("train_right"))

@dp.callback_query(lambda c: c.data.startswith("train_right_"))
async def train_right(callback: CallbackQuery):
    await callback.answer()

    right_result = callback.data.replace("train_right_", "")
    data = user_temp[ADMIN_ID]

    train_ai("left", data["rank"], data["suit"], data["prev"], data["left"])
    train_ai("right", data["rank"], data["suit"], data["prev"], right_result)

    await callback.message.edit_text("✅ تم تدريب الذكاء بنجاح 🔥")

    user_temp.pop(ADMIN_ID, None)

# ================== WEBHOOK ==================
async def main():
    logging.basicConfig(level=logging.INFO)

    init_db()

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
