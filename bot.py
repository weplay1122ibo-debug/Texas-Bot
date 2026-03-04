import asyncio
import logging
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from aiogram import Bot, Dispatcher, F
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

bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())  # غيّر لـ RedisStorage في الإنتاج

# ================= STATES =================
class GameStates(StatesGroup):
    waiting_rank = State()
    waiting_suit = State()
    waiting_prev = State()

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
        # Training
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
        
        # Users
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                expire TIMESTAMP,
                plan TEXT,
                is_trainer BOOLEAN DEFAULT FALSE
            );
        """)
        
        # Codes
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                code TEXT PRIMARY KEY,
                days INTEGER,
                plan TEXT,
                used BOOLEAN DEFAULT FALSE,
                type TEXT DEFAULT 'user'
            );
        """)

async def check_subscription(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT expire, is_trainer FROM users WHERE user_id = $1", str(user_id)
        )
        return bool(row and (row["is_trainer"] or (row["expire"] and row["expire"] > datetime.now())))

async def activate_user(user_id: int, days: int, plan: str, is_trainer: bool = False):
    expire = datetime.now() + timedelta(days=days)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, expire, plan, is_trainer)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE 
            SET expire = EXCLUDED.expire, plan = EXCLUDED.plan, is_trainer = EXCLUDED.is_trainer
        """, str(user_id), expire, plan, is_trainer)

# ================= AI (الأقوى) =================
async def train_ai(side: str, rank: str, suit: str, prev: str, result: str):
    minute = datetime.now(SAUDI_TZ).minute
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO training (side, rank, suit, prev, result, minute) VALUES ($1,$2,$3,$4,$5,$6)",
            side, rank, suit, prev, result, minute
        )
    # تنظيف تلقائي
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM training WHERE created_at < NOW() - INTERVAL '90 days'")

async def predict_hand(side: str, rank: str, suit: str, prev: str, hands_list: list):
    current_min = datetime.now(SAUDI_TZ).minute
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT result, minute 
            FROM training 
            WHERE side = $1 AND created_at > NOW() - INTERVAL '90 days'
            LIMIT 7000
        """, side)

    scores = {h: 1.0 for h in hands_list}  # Laplace smoothing
    total = len(hands_list)

    for row in rows:
        w = 1.0
        if row["minute"] == current_min:
            w += 3.5
        if row["result"] in ("AA", "four", "full_house"):
            w += 2.5
        age = (datetime.now(SAUDI_TZ) - row["created_at"]).days if "created_at" in row else 0
        w *= max(0.4, 1.0 - age * 0.008)
        scores[row["result"]] += w
        total += w

    # Monte Carlo sampling
    samples = random.choices(list(scores.keys()), weights=list(scores.values()), k=20)
    from collections import Counter
    best = Counter(samples).most_common(1)[0][0]
    conf = int((scores[best] / total) * 100)
    conf = max(min(conf + random.randint(-8, 8), 98), 28)

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

def next_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 التخمين التالي", callback_data="next")]
    ])

# ================= HANDLERS =================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if not await check_subscription(message.from_user.id):
        return await message.answer("❌ لازم كود اشتراك\n/code XXXXX")
    await state.set_state(GameStates.waiting_rank)
    await message.answer("🎲 اختر رقم الورقة:", reply_markup=ranks_kb())

@dp.message(Command("code"))
async def use_code(message: Message):
    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer("✅ /code XXXXX")
    code = parts[1].upper()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT days,plan,type FROM codes WHERE code=$1 AND NOT used", code)
        if not row:
            return await message.answer("❌ كود غير صالح")
        await conn.execute("UPDATE codes SET used=TRUE WHERE code=$1", code)
        is_tr = row["type"] == "trainer"
        await activate_user(message.from_user.id, row["days"], row["plan"], is_tr)
        await message.answer("🔥 تم التفعيل!" if not is_tr else "🧠 تم تفعيلك كمدرب!")

@dp.message(Command("createcode"))
async def create_code(message: Message):
    if not await is_trainer(message.from_user.id):
        return
    try:
        _, days, plan, *t = message.text.split()
        is_tr = any(x.lower() in ("trainer","t","مدرب") for x in t)
        code = f"BOT{random.randint(100000,999999)}"
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO codes (code,days,plan,type) VALUES ($1,$2,$3,$4)",
                               code, int(days), plan, "trainer" if is_tr else "user")
        await message.answer(f"✅ كود جديد:\n<code>{code}</code>\n{int(days)} يوم - {'مدرب' if is_tr else 'مستخدم'}")
    except:
        await message.answer("استخدام: /createcode 30 premium [trainer]")

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
    
    left_p, left_c = await predict_hand("left", data["rank"], data["suit"], prev, LEFT_HANDS)
    right_p, right_c = await predict_hand("right", data["rank"], data["suit"], prev, RIGHT_HANDS)
    
    # تدريب تلقائي للمدربين
    if await is_trainer(callback.from_user.id):
        await train_ai("left", data["rank"], data["suit"], prev, left_p)
        await train_ai("right", data["rank"], data["suit"], prev, right_p)
    
    await callback.message.edit_text(
        f"<b>🤖 توقع البوت:</b>\n\n"
        f"⬅️ يسار: {LEFT_LABELS.get(left_p, left_p)} <i>({left_c}%)</i>\n"
        f"➡️ يمين: {RIGHT_LABELS.get(right_p, right_p)} <i>({right_c}%)</i>",
        reply_markup=next_kb()
    )
    await state.clear()

@dp.callback_query(F.data == "next")
async def next_guess(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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
    logger.info("🚀 Bot started with webhook")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
