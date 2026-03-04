import asyncio
import logging
import random
import string
import json
import os
from datetime import datetime, timedelta
from collections import deque

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ================= CONFIG =================
API_TOKEN = "8664632562:AAHD6xaPk01W7cfX1zADS8hRwh-mfVW7s4k"
ADMIN_ID = 7717061636

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

DATA_FILE = "training_data.json"
CODES_FILE = "codes.json"
USERS_FILE = "users.json"
DAILY_STATS_FILE = "daily_stats.json"

user_temp = {}
AI_MEMORY = deque(maxlen=20000)

# ================= STORAGE =================
def load_json(file):
    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return {}

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f)

users = load_json(USERS_FILE)
codes = load_json(CODES_FILE)
daily_stats = load_json(DAILY_STATS_FILE)

def load_training():
    global AI_MEMORY
    try:
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            AI_MEMORY = deque(loaded[:20000], maxlen=20000)
    except:
        AI_MEMORY = deque(maxlen=20000)

def save_training():
    with open(DATA_FILE, "w") as f:
        json.dump(list(AI_MEMORY), f)

def save_daily_stats():
    save_json(DAILY_STATS_FILE, daily_stats)

async def auto_save():
    while True:
        await asyncio.sleep(300)
        save_training()
        save_daily_stats()

# ================= AI ENGINE =================
def get_training_count():
    return len(AI_MEMORY)

def train_ai(rank, suit, prev, curr, day=None, hour=None):
    AI_MEMORY.appendleft({
        "rank": rank,
        "suit": suit,
        "prev": prev,
        "curr": curr,
        "day": day,
        "hour": hour,
        "time": datetime.now().isoformat()
    })
    save_training()  # حفظ فوري

def predict_hand(rank, suit, last_hand=None, day=None, hour=None):
    hands = ["👥 زوجين", "🔗 متتالية", "🎴 ثلاثة", "♠️ فلش", "🏠 فل هاوس", "🂡 أربعة", "🌟 ستريت فلش"]
    scores = {h: 3 for h in hands}

    now = datetime.now()
    for item in list(AI_MEMORY)[:600]:
        created = datetime.fromisoformat(item["time"])
        days_old = (now - created).days
        time_weight = 6 if days_old <= 2 else 4 if days_old <= 7 else 1

        if item["rank"] == rank and item["suit"] == suit:
            scores[item["curr"]] += 15 * time_weight
        if last_hand and item["prev"] == last_hand:
            scores[item["curr"]] += 10 * time_weight
        scores[item["curr"]] += 1

    total = sum(scores.values())
    percentages = {h: round((scores[h] / total) * 100, 1) for h in hands}
    sorted_hands = sorted(percentages.items(), key=lambda x: x[1], reverse=True)

    high = sorted_hands[0]

    text = f"""🎯 TEXAS AI V8 ULTRA

🔥 عالي: {high[0]} ({high[1]}%)
⚖️ متوسط: {sorted_hands[1][0]} ({sorted_hands[1][1]}%)
⚠️ منخفض: {sorted_hands[-1][0]} ({sorted_hands[-1][1]}%)"""

    return text, high[0]

# ================= SUBSCRIPTION =================
def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def check_subscription(user_id):
    if str(user_id) not in users:
        return False
    return datetime.fromisoformat(users[str(user_id)]) > datetime.now()

def activate_code(user_id, code):
    if code not in codes or codes[code].get("used"):
        return False, "❌ الكود غير موجود أو مستخدم"
    expire = datetime.now() + timedelta(days=codes[code]["days"])
    users[str(user_id)] = expire.isoformat()
    codes[code]["used"] = True
    save_json(USERS_FILE, users)
    save_json(CODES_FILE, codes)
    return True, "✅ تم التفعيل بنجاح!\nجاهز للعب"

# ================= ADMIN =================
@dp.message(lambda m: m.text and m.text.startswith("/addcode"))
async def add_code(message: Message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    days = int(parts[1]) if len(parts) > 1 else 7
    code = generate_code()
    codes[code] = {"used": False, "days": days}
    save_json(CODES_FILE, codes)
    await message.answer(f"✅ كود جديد:\n`{code}`\nالمدة: {days} يوم", parse_mode="Markdown")

@dp.message(Command("trainstatus"))
async def train_status(message: Message):
    if message.from_user.id != ADMIN_ID: return
    count = get_training_count()
    perc = min(100, int(count / 20000 * 100))
    await message.answer(f"📊 حالة تدريب البوت\nجولات مدربة: {count}\nنسبة الذكاء: {perc}%")

@dp.message(Command("stats"))
async def show_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    today_key = datetime.now().strftime("%Y-%m-%d")
    today = daily_stats.get(today_key, {"total": 0, "correct": 0})
    t_perc = round(today["correct"] / today["total"] * 100, 1) if today["total"] else 0
    all_total = sum(d.get("total", 0) for d in daily_stats.values())
    all_correct = sum(d.get("correct", 0) for d in daily_stats.values())
    o_perc = round(all_correct / all_total * 100, 1) if all_total else 0

    await message.answer(f"📊 إحصائيات\nاليوم: {t_perc}%\nالإجمالي: {o_perc}%\nجولات التدريب: {get_training_count()}")

# ================= KEYBOARDS =================
def ranks_kb():
    ranks = ["A","K","Q","J","10","9","8","7","6","5","4","3","2"]
    rows = [ranks[i:i+4] for i in range(0, len(ranks), 4)]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=r, callback_data=f"rank_{r}") for r in row] for row in rows])

def suits_kb():
    suits = ["♥️","♦️","♣️","♠️"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=s, callback_data=f"suit_{s}") for s in suits]])

def hands_kb():
    hands = ["👥 زوجين", "🔗 متتالية", "🎴 ثلاثة", "♠️ فلش", "🏠 فل هاوس", "🂡 أربعة", "🌟 ستريت فلش"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=h, callback_data=f"hand_{h}")] for h in hands])

def days_kb():
    days = ["السبت", "الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=d, callback_data=f"day_{d}") for d in days]])

# ================= FLOW - التسلسل الجديد =================
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("🔥 TEXAS AI V8\nادخل كود الاشتراك")

@dp.message()
async def handle_text(message: Message):
    user_id = message.from_user.id

    if not check_subscription(user_id):
        ok, msg = activate_code(user_id, message.text.strip())
        await message.answer(msg)
        if ok:
            user_temp[user_id] = {"step": "time"}
            await message.answer("⏰ ما هي الساعة الحالية بالضبط؟ (مثال: 14:30)")
        return

    # تسلسل الخطوات
    state = user_temp.get(user_id, {})

    if state.get("step") == "time":
        user_temp[user_id]["time"] = message.text
        user_temp[user_id]["step"] = "day"
        await message.answer("📅 ما هو اليوم في الأسبوع؟", reply_markup=days_kb())
        return

    await message.answer("اختر رقم الورقة:", reply_markup=ranks_kb())

# Callback Handlers
@dp.callback_query(lambda c: c.data.startswith("day_"))
async def choose_day(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    day = callback.data.replace("day_", "")
    user_temp[user_id]["day"] = day
    user_temp[user_id]["step"] = "rank"
    await callback.message.edit_text("اختر رقم الورقة:", reply_markup=ranks_kb())

@dp.callback_query(lambda c: c.data.startswith("rank_"))
async def choose_rank(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user_temp[user_id]["rank"] = callback.data.split("_")[1]
    user_temp[user_id]["step"] = "suit"
    await callback.message.edit_text("اختر نوع الورقة:", reply_markup=suits_kb())

@dp.callback_query(lambda c: c.data.startswith("suit_"))
async def choose_suit(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user_temp[user_id]["suit"] = callback.data.split("_")[1]
    user_temp[user_id]["step"] = "prev"
    await callback.message.edit_text("الضربة السابقة؟", reply_markup=hands_kb())

@dp.callback_query(lambda c: c.data.startswith("hand_"))
async def handle_hand(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    data = user_temp.get(user_id, {})
    if not data:
        return

    chosen = callback.data.replace("hand_", "")
    if chosen == "none": chosen = None

    rank = data.get("rank")
    suit = data.get("suit")
    prev = chosen
    day = data.get("day")
    hour = data.get("time")

    result_text, _ = predict_hand(rank, suit, prev, day, hour)

    await callback.message.edit_text(result_text)
    user_temp.pop(user_id, None)

# ================= WEBHOOK =================
WEBHOOK_PATH = "/webhook"

async def main():
    logging.basicConfig(level=logging.INFO)
    load_training()
    asyncio.create_task(auto_save())

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
        full_url = webhook_base.rstrip("/") + WEBHOOK_PATH
        await bot.set_webhook(full_url)
        logging.info(f"✅ Webhook set: {full_url}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
