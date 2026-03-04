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
TRAINER_IDS = [1234567890]  # ضع هنا ID المدرّبين

SAUDI_TZ = ZoneInfo("Asia/Riyadh")
SPECIAL_MINUTES = [1,5,6,8,9,16,17,21,23,27,28,29,35,36,41,45,47,51,53,55,57,58,59]
ALERT_BEFORE = 2

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
db_pool = None
user_temp = {}
last_alert_key = None

# ================= GAME HANDS =================
LEFT_HANDS = ["❌ لا شيء","♠️ متتالية من نفس النوع","👥 زوج","🅰️ AA"]
RIGHT_HANDS = ["👥 زوجين","🔗 متتالية","🎴 ثلاثة","🏠 فل هاوس","🂡 أربعة"]

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

# ================= AI =================
async def train_ai(side, rank, suit, prev, result):
    minute = datetime.now(SAUDI_TZ).minute
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO training (side, rank, suit, prev, result, minute) VALUES ($1,$2,$3,$4,$5,$6)",
            side, rank, suit, prev, result, minute
        )

async def predict_hand(side, rank, suit, prev, hands_list):
    scores = {h: 0 for h in hands_list}
    total = 0
    current_minute = datetime.now(SAUDI_TZ).minute
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT rank,suit,prev,result,minute FROM training WHERE side=$1", side)
    for r in rows:
        weight = 0
        if r["rank"] == rank: weight += 3
        if r["suit"] == suit: weight += 3
        if r["prev"] == prev: weight += 5
        if r["minute"] == current_minute and r["result"] in ["🅰️ AA","🂡 أربعة","👥 زوج"]:
            weight += 5
        if weight > 0:
            for res in r["result"].split(","):
                if res in scores:
                    scores[res] += weight
                    total += weight
    if total == 0: return "لا يوجد بيانات",0
    best = max(scores,key=scores.get)
    confidence = int((scores[best]/total)*100)
    return best,confidence

# ================= KEYBOARDS =================
def ranks_kb():
    ranks=["A","K","Q","J","10","9","8","7","6","5","4","3","2"]
    rows=[ranks[i:i+4] for i in range(0,len(ranks),4)]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=r,callback_data=f"rank_{r}") for r in row] for row in rows])

def suits_kb():
    suits=["♥️","♦️","♣️","♠️"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=s,callback_data=f"suit_{s}") for s in suits]])

def hands_kb(prefix,hands_list):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=h,callback_data=f"{prefix}_{h}")] for h in hands_list])

def next_guess_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 التخمين التالي",callback_data="next_guess")]])

def admin_stats_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ تحليل اليسار", callback_data="ai_left")],
        [InlineKeyboardButton(text="➡️ تحليل اليمين", callback_data="ai_right")],
        [InlineKeyboardButton(text="📊 كل البيانات", callback_data="ai_all")]
    ])

# ================= START / HELP =================
@dp.message(CommandStart())
async def start(message:Message):
    if not await check_subscription(message.from_user.id):
        await message.answer("❌ لازم تدخل كود اشتراك\n/code XXXXX")
        return
    await message.answer("اختر رقم الورقة:",reply_markup=ranks_kb())

@dp.message(Command("help"))
async def show_help(message:Message):
    text="""
📋 **دليل استخدام البوت**

1️⃣ تفعيل الاشتراك: /code XXXXX
2️⃣ بدء اللعب: /start
3️⃣ اختيار رقم الورقة والنوع والضربة السابقة
4️⃣ تنبيه الخماسي التلقائي
5️⃣ أوامر الأدمن والمدربين:
- /create_code <عدد الأيام> <اسم الخطة>
- /delete_code <CODE>
- /stats : إحصائيات المشتركين وبيانات التدريب
- /training_stats : عرض عدد الضربات التي تم تدريب البوت عليها
- /ai_stats : تحليل أداء الذكاء
- /reset_training : تصفير بيانات التدريب
- /start لتجاوز الاشتراك
6️⃣ ميزات إضافية: توقع ذكي، واجهة أزرار، اختيار متعدد لليسار
"""
    await message.answer(text)

# ================= CALLBACKS =================
@dp.callback_query(lambda c:c.data.startswith("rank_"))
async def choose_rank(callback:CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id]={"rank":callback.data.split("_")[1]}
    await callback.message.edit_text("اختر النوع:",reply_markup=suits_kb())

@dp.callback_query(lambda c:c.data.startswith("suit_"))
async def choose_suit(callback:CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id]["suit"]=callback.data.split("_")[1]
    await callback.message.edit_text("الضربة السابقة:",reply_markup=hands_kb("prev",RIGHT_HANDS))

@dp.callback_query(lambda c:c.data.startswith("prev_"))
async def handle_prev(callback:CallbackQuery):
    await callback.answer()
    user_id=callback.from_user.id
    if not await check_subscription(user_id):
        await callback.message.edit_text("❌ الاشتراك منتهي")
        return
    prev=callback.data.replace("prev_","")
    data=user_temp.get(user_id)
    left_pred,left_conf=await predict_hand("left",data["rank"],data["suit"],prev,LEFT_HANDS)
    right_pred,right_conf=await predict_hand("right",data["rank"],data["suit"],prev,RIGHT_HANDS)
    await callback.message.edit_text(f"⬅️ يسار: {left_pred} ({left_conf}%)\n➡️ يمين: {right_pred} ({right_conf}%)",reply_markup=next_guess_kb())

@dp.callback_query(lambda c:c.data=="next_guess")
async def next_guess(callback:CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("ابدأ التخمين الجديد:",reply_markup=ranks_kb())

# ================= AI_STATS =================
@dp.message(Command("ai_stats"))
async def ai_stats(message:Message):
    user_id=message.from_user.id
    if user_id != ADMIN_ID and user_id not in TRAINER_IDS:
        await message.answer("❌ هذا الأمر خاص بالأدمن والمدربين فقط")
        return
    await message.answer("اختر القسم الذي تريد تحليله:", reply_markup=admin_stats_kb())

async def analyze_side(side):
    async with db_pool.acquire() as conn:
        data = await conn.fetch("SELECT result FROM training WHERE side=$1",side)
    total=len(data)
    counts={}
    confidence_sum=0
    for row in data:
        results=row["result"].split(",")
        for res in results: counts[res]=counts.get(res,0)+1
        confidence_sum+=100/len(results) if results else 0
    best=max(counts,key=counts.get) if counts else "لا يوجد"
    avg_conf=int(confidence_sum/total) if total>0 else 0
    return f"📊 تحليل أداء الذكاء - {side.capitalize()}\nعدد التخمينات: {total}\nأفضل نتيجة متوقعة: {best}\nمتوسط نسبة الثقة: {avg_conf}%"

@dp.callback_query(lambda c:c.data.startswith("ai_"))
async def ai_callbacks(callback:CallbackQuery):
    await callback.answer()
    side=callback.data.replace("ai_","")
    if side=="all":
        left=await analyze_side("left")
        right=await analyze_side("right")
        text=f"{left}\n{right}"
    else:
        text=await analyze_side(side)
    await callback.message.edit_text(text)

# ================= TRAINING_STATS =================
@dp.message(Command("training_stats"))
async def training_stats(message:Message):
    user_id=message.from_user.id
    if user_id != ADMIN_ID and user_id not in TRAINER_IDS:
        await message.answer("❌ هذا الأمر خاص بالأدمن والمدربين فقط")
        return
    async with db_pool.acquire() as conn:
        left_count = await conn.fetchval("SELECT COUNT(*) FROM training WHERE side='left'")
        right_count = await conn.fetchval("SELECT COUNT(*) FROM training WHERE side='right'")
        total = left_count + right_count
    text=f"""
📊 **إحصائيات التدريب**

✅ إجمالي عدد الضربات المدربة: {total}
⬅️ يسار: {left_count}
➡️ يمين: {right_count}
"""
    await message.answer(text)

# ================= STATS =================
@dp.message(Command("stats"))
async def stats(message:Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        training = await conn.fetchval("SELECT COUNT(*) FROM training")
    await message.answer(f"🇮🇶 Texas Iraq Bot - Stats\n👥 المشتركين: {users}\n🧠 بيانات التدريب: {training}")

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
