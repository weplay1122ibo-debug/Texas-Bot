import asyncio
import logging
import os
import asyncpg
import secrets
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ================= CONFIG =================
API_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_PATH = "/webhook"
ADMIN_ID = 7717061636
TRAINER_IDS = []  # ضع هنا ID المدرّبين

SAUDI_TZ = ZoneInfo("Asia/Riyadh")
SPECIAL_MINUTES = [1,5,6,8,9,16,17,21,23,27,28,29,35,36,41,45,47,51,53,55,57,58,59]
ALERT_BEFORE = 2

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
db_pool = None
user_temp = {}
last_alert_key = None

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
            used BOOLEAN DEFAULT FALSE
        )
        """)

# ================= SUBSCRIPTION =================
async def check_subscription(user_id):
    if user_id == ADMIN_ID or user_id in TRAINER_IDS:
        return True
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT expire FROM users WHERE user_id=$1", str(user_id))
    return row and row["expire"] > datetime.now()

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
        target_minute = (now.minute + ALERT_BEFORE) % 60
        alert_key = f"{now.hour}:{target_minute}"
        if target_minute in SPECIAL_MINUTES and alert_key != last_alert_key:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT user_id FROM users WHERE expire > NOW()")
            for row in rows:
                try:
                    await bot.send_message(int(row["user_id"]),
                        f"⏰ تنبيه ذكي\nينصح بلعب خماسي خلال {ALERT_BEFORE} دقيقة 🔥\n🎯 الدقيقة المستهدفة: {target_minute}")
                except: pass
            last_alert_key = alert_key
            await asyncio.sleep(60)
        else:
            await asyncio.sleep(20)

# ================= AI + Monte Carlo =================
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

# ================= START / HELP =================
@dp.message(CommandStart())
async def start(message:Message):
    if not await check_subscription(message.from_user.id):
        await message.answer("❌ لازم تدخل كود اشتراك\n/code XXXXX")
        return
    await message.answer("اختر رقم الورقة:",reply_markup=ranks_kb())

# ================= CALLBACKS =================
@dp.callback_query(lambda c:c.data.startswith("rank_"))
async def choose_rank(callback:CallbackQuery):
    try:
        await callback.answer()
        user_temp[callback.from_user.id]={"rank":callback.data.split("_")[1]}
        await callback.message.edit_text("اختر النوع:",reply_markup=suits_kb())
    except Exception as e:
        logging.error(f"Error choose_rank: {e}")

@dp.callback_query(lambda c:c.data.startswith("suit_"))
async def choose_suit(callback:CallbackQuery):
    try:
        await callback.answer()
        user_temp[callback.from_user.id]["suit"]=callback.data.split("_")[1]
        await callback.message.edit_text("اختر الضربة السابقة:", reply_markup=prev_hands_kb())
    except Exception as e:
        logging.error(f"Error choose_suit: {e}")

@dp.callback_query(lambda c:c.data.startswith("prev_"))
async def handle_prev(callback:CallbackQuery):
    try:
        await callback.answer()
        user_id = callback.from_user.id
        if not await check_subscription(user_id):
            await callback.message.edit_text("❌ الاشتراك منتهي")
            return

        prev = callback.data.replace("prev_","")
        data = user_temp.get(user_id)
        if not data:
            await callback.message.edit_text("ابدأ من جديد /start")
            return

        user_temp[user_id]["prev"] = prev

        left_pred, left_conf = await predict_hand("left", data["rank"], data["suit"], prev, LEFT_HANDS)
        right_pred, right_conf = await predict_hand("right", data["rank"], data["suit"], prev, RIGHT_HANDS)

        await callback.message.edit_text(
            f"⬅️ يسار: {LEFT_HANDS_LABELS.get(left_pred,left_pred)} ({left_conf}%)\n"
            f"➡️ يمين: {RIGHT_HANDS_LABELS.get(right_pred,right_pred)} ({right_conf}%)",
            reply_markup=next_guess_kb()
        )
    except Exception as e:
        logging.error(f"Error handle_prev: {e}")
        await callback.message.answer("حدث خطأ، حاول مرة أخرى /start")

@dp.callback_query(lambda c:c.data=="next_guess")
async def next_guess(callback:CallbackQuery):
    try:
        await callback.answer()
        user_temp.pop(callback.from_user.id, None)
        await callback.message.edit_text("ابدأ التخمين الجديد:", reply_markup=ranks_kb())
    except Exception as e:
        logging.error(f"Error next_guess: {e}")
        await callback.message.answer("حدث خطأ، حاول مرة أخرى /start")

# ================= WEBHOOK =================
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    asyncio.create_task(send_alerts())
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
    await bot.set_webhook(webhook_base.rstrip("/")+WEBHOOK_PATH)
    await asyncio.Event().wait()

if __name__=="__main__":
    asyncio.run(main())
