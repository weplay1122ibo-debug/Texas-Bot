import asyncio, os, asyncpg, secrets, random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ================= CONFIG =================
API_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_PATH = "/webhook"
ADMIN_ID = 7717061636
TRAINER_IDS = []

SAUDI_TZ = ZoneInfo("Asia/Riyadh")

# ✅ التصليح المهم لـ Render
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()
db_pool = None
user_temp = {}

# ================= GAME HANDS =================
LEFT_HANDS = ["none","sequence_same","pair","AA"]
LEFT_HANDS_LABELS = {"none":"❌ لا شيء","sequence_same":"♠️ متتالية من نفس النوع","pair":"👥 زوج","AA":"🅰️ AA"}

RIGHT_HANDS = ["two_pairs","sequence","three","full_house","four"]
RIGHT_HANDS_LABELS = {"two_pairs":"👥 زوجين","sequence":"🔗 متتالية","three":"🎴 ثلاثة","full_house":"🏠 فل هاوس","four":"🂡 أربعة"}

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
            minute INT,
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
            used BOOLEAN DEFAULT FALSE,
            type TEXT DEFAULT 'user'
        )
        """)

# ================= SUBSCRIPTION =================
async def check_subscription(user_id):
    if user_id == ADMIN_ID or user_id in TRAINER_IDS:
        return True
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT expire FROM users WHERE user_id=$1", str(user_id))
    return row and row["expire"] > datetime.now()

async def activate_user(user_id, days, plan, type="user"):
    async with db_pool.acquire() as conn:
        expire = datetime.now() + timedelta(days=days)
        await conn.execute("""
        INSERT INTO users (user_id, expire, plan)
        VALUES ($1,$2,$3)
        ON CONFLICT (user_id)
        DO UPDATE SET expire=$2, plan=$3
        """, str(user_id), expire, plan)
        if type=="trainer":
            TRAINER_IDS.append(user_id)

# ================= AI =================
async def train_ai(side, rank, suit, prev, result):
    minute = datetime.now(SAUDI_TZ).minute
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO training (side, rank, suit, prev, result, minute) VALUES ($1,$2,$3,$4,$5,$6)",
            side, rank, suit, prev, result, minute
        )

async def predict_hand(side, rank, suit, prev, hands_list):
    scores = {h:0 for h in hands_list}
    total = 0
    current_minute = datetime.now(SAUDI_TZ).minute

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT rank,suit,prev,result,minute FROM training WHERE side=$1", side)

    for r in rows:
        weight = 0
        if r["rank"]==rank: weight+=3
        if r["suit"]==suit: weight+=3
        if r["prev"]==prev: weight+=5
        if r["minute"]==current_minute and r["result"] in ["AA","four","pair"]:
            weight+=5
        if weight>0:
            for res in r["result"].split(","):
                if res in scores:
                    scores[res]+=weight
                    total+=weight

    if total==0:
        best=random.choice(hands_list)
        confidence=random.randint(30,60)
        return best, confidence

    probabilities={h:(scores[h]/total) for h in hands_list}
    rand_val=random.random()
    cumulative=0
    for h,p in probabilities.items():
        cumulative+=p
        if rand_val<=cumulative:
            best=h
            break
    if random.random()<0.1:
        best=random.choice(hands_list)
    confidence=int(probabilities.get(best,0)*100)
    confidence=max(min(confidence+random.randint(-5,5),100),10)
    return best,confidence

# ================= KEYBOARDS =================
def ranks_kb():
    ranks=["A","K","Q","J","10","9","8","7","6","5","4","3","2"]
    rows=[ranks[i:i+4] for i in range(0,len(ranks),4)]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=r,callback_data=f"rank_{r}") for r in row] for row in rows]
    )

def suits_kb():
    suits=["♥️","♦️","♣️","♠️"]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=s,callback_data=f"suit_{s}") for s in suits]]
    )

def prev_hands_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=RIGHT_HANDS_LABELS[h],callback_data=f"prev_{h}")] for h in RIGHT_HANDS]
    )

def next_guess_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 التخمين التالي",callback_data="next_guess")]]
    )

# ================= HANDLERS =================
@dp.message(CommandStart())
async def start(message: Message):
    if not await check_subscription(message.from_user.id):
        await message.answer("❌ لازم تدخل كود اشتراك\n/code XXXXX")
        return
    mode = user_temp.get(message.from_user.id, {}).get("mode","guess_only")
    await message.answer(
        "🧠 وضع التدريب مفعل" if mode=="training" else "🎲 التخمين العادي",
        reply_markup=ranks_kb()
    )

@dp.message(Command("code"))
async def use_code(message: Message):
    parts = message.text.split()
    if len(parts)!=2:
        await message.answer("استخدم:\n/code XXXXX")
        return
    code = parts[1].upper()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT days,plan,type,used FROM codes WHERE code=$1", code)
        if not row or row["used"]:
            await message.answer("❌ كود غير صالح أو مستخدم")
            return
        await conn.execute("UPDATE codes SET used=TRUE WHERE code=$1", code)
        await activate_user(message.from_user.id, row["days"], row["plan"], type=row["type"])
        msg = f"🔥 تم التفعيل\n💎 خطتك: {row['plan']}"
        if row["type"]=="trainer":
            msg = f"🔥 تم تفعيلك كمدرب\n💎 خطتك: {row['plan']}"
        await message.answer(msg)

# باقي الـ handlers (admin, callbacks) كما هي في الكود الأصلي
# (انسخها من الكود الأصلي اللي عندك بدون تغيير)

# ================= WEBHOOK =================
async def main():
    import logging
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    app=web.Application()
    SimpleRequestHandler(dispatcher=dp,bot=bot).register(app,path=WEBHOOK_PATH)
    setup_application(app,dp)
    port=int(os.getenv("PORT",8080))
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,"0.0.0.0",port)
    await site.start()
    webhook_base=os.environ["WEBHOOK_URL"]
    await bot.set_webhook(f"{webhook_base.rstrip('/')}{WEBHOOK_PATH}")
    await asyncio.Event().wait()

if __name__=="__main__":
    asyncio.run(main())
