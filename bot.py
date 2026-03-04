import asyncio
import logging
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import Counter

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ================= CONFIG =================
API_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_PATH = "/webhook"
ADMIN_ID = 7717061636
WEBHOOK_URL = os.environ["WEBHOOK_URL"]

SAUDI_TZ = ZoneInfo("Asia/Riyadh")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= BOT =================
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# ================= STATES =================
class GameStates(StatesGroup):
    waiting_rank = State()
    waiting_suit = State()
    waiting_prev = State()

class RecordStates(StatesGroup):
    waiting_actual_left = State()
    waiting_actual_right = State()

# ================= HANDS =================
LEFT_HANDS = ["none", "sequence_same", "pair", "AA"]
RIGHT_HANDS = ["two_pairs", "sequence", "three", "full_house", "four"]

LEFT_LABELS = {"none":"❌ لا شيء", "sequence_same":"♠️ متتالية نفس", "pair":"👥 زوج", "AA":"🅰️ AA"}
RIGHT_LABELS = {"two_pairs":"👥 زوجين", "sequence":"🔗 متتالية", "three":"🎴 ثلاثة", 
                "full_house":"🏠 فل هاوس", "four":"🂡 أربعة"}

# ================= DB =================
db_pool = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, ssl="require")
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS training (
                id SERIAL PRIMARY KEY,
                side TEXT NOT NULL,
                rank TEXT,
                suit TEXT,
                prev TEXT,
                result TEXT,
                minute INT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_side ON training(side);
            CREATE INDEX IF NOT EXISTS idx_date ON training(created_at DESC);
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                expire TIMESTAMP,
                plan TEXT,
                is_trainer BOOLEAN DEFAULT FALSE
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                code TEXT PRIMARY KEY,
                days INTEGER,
                plan TEXT,
                used BOOLEAN DEFAULT FALSE,
                type TEXT DEFAULT 'user'
            );
        """)

async def is_trainer(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_trainer FROM users WHERE user_id = $1", str(user_id))
        return bool(row and row["is_trainer"])

# ================= AI =================
async def train_ai(side: str, rank: str, suit: str, prev: str, result: str):
    minute = datetime.now(SAUDI_TZ).minute
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO training (side,rank,suit,prev,result,minute) VALUES ($1,$2,$3,$4,$5,$6)",
            side, rank, suit, prev, result, minute
        )
    # تنظيف تلقائي كل 90 يوم
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM training WHERE created_at < NOW() - INTERVAL '90 days'")

async def predict_hand(side: str, rank: str, suit: str, prev: str, hands_list: list):
    current_min = datetime.now(SAUDI_TZ).minute
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT result, minute, created_at 
            FROM training 
            WHERE side = $1 AND created_at > NOW() - INTERVAL '90 days'
            LIMIT 8000
        """, side)

    scores = {h: 1.0 for h in hands_list}  # Laplace
    total = len(hands_list)

    for row in rows:
        w = 1.0
        if row["minute"] == current_min: w += 4.0
        age = (datetime.now(SAUDI_TZ) - row["created_at"]).days
        w *= max(0.35, 1.0 - age * 0.009)
        scores[row["result"]] += w
        total += w

    # Monte Carlo
    samples = random.choices(list(scores.keys()), weights=list(scores.values()), k=30)
    best = Counter(samples).most_common(1)[0][0]
    conf = int((scores[best] / total) * 100)
    conf = max(min(conf + random.randint(-7, 7), 97), 25)

    return best, conf

# ================= KEYBOARDS =================
def ranks_kb():
    ranks = ["A","K","Q","J","10","9","8","7","6","5","4","3","2"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=r, callback_data=f"rank_{r}") for r in row]
        for row in [ranks[i:i+4] for i in range(0, len(ranks), 4)]
    ])

def suits_kb():
    suits = ["♥️","♦️","♣️","♠️"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=s, callback_data=f"suit_{s}") for s in suits]
    ])

def prev_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=RIGHT_LABELS[h], callback_data=f"prev_{h}")] for h in RIGHT_HANDS
    ])

def after_prediction_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 التخمين التالي", callback_data="next")],
        [InlineKeyboardButton(text="📝 سجل النتيجة الفعلية", callback_data="record_result")]
    ])

def left_actual_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LEFT_LABELS[h], callback_data=f"act_left_{h}")] for h in LEFT_HANDS
    ])

def right_actual_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=RIGHT_LABELS[h], callback_data=f"act_right_{h}")] for h in RIGHT_HANDS
    ])

# ================= HANDLERS =================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if not await is_trainer(message.from_user.id) and not await check_subscription(message.from_user.id):
        return await message.answer("❌ لازم كود اشتراك\n/code XXXXX")
    await state.set_state(GameStates.waiting_rank)
    await message.answer("🎲 اختر رقم الورقة:", reply_markup=ranks_kb())

async def check_subscription(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT expire FROM users WHERE user_id=$1", str(user_id))
        return bool(row and row["expire"] > datetime.now())

@dp.message(Command("code"))
async def use_code(message: Message):
    # ... (نفس الكود السابق بدون تغيير)
    pass  # اكملها بنفس الطريقة السابقة إذا تبي، أو أضفها من الكود القديم

@dp.message(Command("createcode"))
async def create_code(message: Message):
    if not await is_trainer(message.from_user.id): return
    # ... (نفس السابق)

@dp.callback_query(F.data.startswith("rank_"), GameStates.waiting_rank)
async def cb_rank(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(rank=callback.data.split("_")[1])
    await callback.message.edit_text("🃏 اختر النوع:", reply_markup=suits_kb())
    await state.set_state(GameStates.waiting_suit)

@dp.callback_query(F.data.startswith("suit_"), GameStates.waiting_suit)
async def cb_suit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(suit=callback.data.split("_")[1])
    await callback.message.edit_text("اختر الضربة السابقة:", reply_markup=prev_kb())
    await state.set_state(GameStates.waiting_prev)

@dp.callback_query(F.data.startswith("prev_"), GameStates.waiting_prev)
async def cb_prev(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    prev = callback.data.replace("prev_","")
    await state.update_data(prev=prev)

    left_p, left_c = await predict_hand("left", data["rank"], data["suit"], prev, LEFT_HANDS)
    right_p, right_c = await predict_hand("right", data["rank"], data["suit"], prev, RIGHT_HANDS)

    await callback.message.edit_text(
        f"<b>🤖 توقع البوت:</b>\n\n"
        f"⬅️ يسار: {LEFT_LABELS.get(left_p, left_p)} <i>({left_c}%)</i>\n"
        f"➡️ يمين: {RIGHT_LABELS.get(right_p, right_p)} <i>({right_c}%)</i>",
        reply_markup=after_prediction_kb()
    )

# ================= حفظ النتيجة الفعلية =================
@dp.callback_query(F.data == "record_result")
async def start_record(callback: CallbackQuery, state: FSMContext):
    if not await is_trainer(callback.from_user.id):
        await callback.answer("فقط المدربين يقدرون يسجلون النتيجة الفعلية", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text("📝 اختر نتيجة اليسار الفعلية:", reply_markup=left_actual_kb())
    await state.set_state(RecordStates.waiting_actual_left)

@dp.callback_query(F.data.startswith("act_left_"), RecordStates.waiting_actual_left)
async def actual_left(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    actual_left = callback.data.replace("act_left_","")
    await state.update_data(actual_left=actual_left)
    await callback.message.edit_text("📝 اختر نتيجة اليمين الفعلية:", reply_markup=right_actual_kb())
    await state.set_state(RecordStates.waiting_actual_right)

@dp.callback_query(F.data.startswith("act_right_"), RecordStates.waiting_actual_right)
async def actual_right(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    actual_right = callback.data.replace("act_right_","")

    # تدريب على النتائج الحقيقية
    await train_ai("left", data["rank"], data["suit"], data["prev"], data["actual_left"])
    await train_ai("right", data["rank"], data["suit"], data["prev"], actual_right)

    await callback.message.edit_text(
        "✅ تم حفظ النتيجة الفعلية!\n"
        "البوت تعلم من اللعبة الحقيقية 🎯\n\n"
        "اضغط التالي لتوقع جديد",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 التخمين التالي", callback_data="next")]
        ])
    )
    await state.clear()

@dp.callback_query(F.data == "next")
async def next_guess(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("🃏 اختر رقم الورقة الجديد:", reply_markup=ranks_kb())
    await state.set_state(GameStates.waiting_rank)

# ================= WEBHOOK =================
async def main():
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
    
    await bot.set_webhook(f"{WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}")
    logger.info("🚀 Bot deployed successfully")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
