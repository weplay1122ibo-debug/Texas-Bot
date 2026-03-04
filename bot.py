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
STRIKES_FILE = "strikes.json"

user_temp = {}
AI_MEMORY = deque(maxlen=50000)
scheduled_strikes = []  # أوقات الضربات

# ================= STORAGE =================
def load_json(file):
    try:
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

users = load_json(USERS_FILE)
codes = load_json(CODES_FILE)
daily_stats = load_json(DAILY_STATS_FILE)
scheduled_strikes = load_json(STRIKES_FILE)

def load_training():
    global AI_MEMORY
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            AI_MEMORY = deque(loaded[:50000], maxlen=50000)
    except:
        AI_MEMORY = deque(maxlen=50000)

def save_training():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(list(AI_MEMORY), f, ensure_ascii=False)

def save_daily_stats():
    save_json(DAILY_STATS_FILE, daily_stats)

async def auto_save():
    while True:
        await asyncio.sleep(180)
        save_training()
        save_daily_stats()

# ================= AI ENGINE (خارق) =================
def get_training_count():
    return len(AI_MEMORY)

def train_ai(rank, suit, prev, curr):
    AI_MEMORY.appendleft({
        "rank": rank, "suit": suit, "prev": prev, "curr": curr,
        "time": datetime.now().isoformat()
    })
    save_training()

def predict_hand(rank, suit, last_hand=None):
    hands = ["👥 زوجين", "🔗 متتالية", "🎴 ثلاثة", "♠️ فلش", "🏠 فل هاوس", "🂡 أربعة", "🌟 ستريت فلش"]
    scores = {h: 3 for h in hands}

    for item in list(AI_MEMORY)[:800]:
        weight = 8 if (datetime.now() - datetime.fromisoformat(item["time"])).days <= 3 else 4
        if item["rank"] == rank and item["suit"] == suit:
            scores[item["curr"]] += 20 * weight
        if last_hand and item["prev"] == last_hand:
            scores[item["curr"]] += 15 * weight
        scores[item["curr"]] += 1

    total = sum(scores.values())
    percentages = {h: round((scores[h] / total) * 100, 1) for h in hands}
    sorted_hands = sorted(percentages.items(), key=lambda x: x[1], reverse=True)

    high = sorted_hands[0]
    text = f"""🎯 TEXAS AI V10 LEGEND

🔥 توقع الفوز: {high[0]} ({high[1]}%)
⚖️ متوسط: {sorted_hands[1][0]} ({sorted_hands[1][1]}%)
⚠️ ضعيف: {sorted_hands[-1][0]} ({sorted_hands[-1][1]}%)"""

    return text, high[0]

# ================= STRIKE SYSTEM =================
async def strike_notifier():
    while True:
        now = datetime.now()
        current_min = now.minute

        for strike in scheduled_strikes[:]:
            if current_min == strike["minute"] - 5:
                msg = f"⚠️ تنبيه مهم!\n\nبعد 5 دقائق ستكون الضربة!\nالوقت: {strike['minute']:02d}:00\nاستعدوا 🔥"
                for uid in list(users.keys()):
                    if check_subscription(int(uid)):
                        try:
                            await bot.send_message(int(uid), msg)
                        except:
                            pass
        await asyncio.sleep(60)

# ================= ADMIN COMMANDS =================
@dp.message(lambda m: m.text and m.text.startswith("/strike"))
async def set_strike(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        minutes = [int(x) for x in parts[1:5]]  # أول 4 أرقام
        strike_type = " ".join(parts[5:]) if len(parts) > 5 else None

        global scheduled_strikes
        scheduled_strikes = [{"minute": m, "type": strike_type} for m in minutes]
        save_json(STRIKES_FILE, scheduled_strikes)

        await message.answer(f"✅ تم جدولة الضربات على: {minutes}\nنوع الضربة: {strike_type or 'غير محدد'}")
    except:
        await message.answer("استخدام: `/strike 15 30 45 00 فلهاوس`")

@dp.message(Command("trainstatus"))
async def train_status(message: Message):
    if message.from_user.id != ADMIN_ID: return
    count = get_training_count()
    perc = min(100, int(count / 50000 * 100))
    await message.answer(f"📊 حالة الذكاء الاصطناعي\nجولات مدربة: {count}\nنسبة الذكاء: {perc}%")

@dp.message(Command("stats"))
async def show_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    today_key = datetime.now().strftime("%Y-%m-%d")
    today = daily_stats.get(today_key, {"total": 0, "correct": 0})
    t_perc = round(today["correct"] / today["total"] * 100, 1) if today["total"] else 0
    all_total = sum(d.get("total", 0) for d in daily_stats.values())
    all_correct = sum(d.get("correct", 0) for d in daily_stats.values())
    o_perc = round(all_correct / all_total * 100, 1) if all_total else 0

    await message.answer(f"📊 إحصائيات TEXAS AI V10\n\n"
                         f"📅 اليوم: {t_perc}%\n"
                         f"📈 الإجمالي: {o_perc}%\n"
                         f"🧠 جولات التدريب: {get_training_count()}")

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

# ================= FLOW =================
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("🔥 TEXAS AI V10\nادخل كود الاشتراك")

@dp.message()
async def handle_text(message: Message):
    user_id = message.from_user.id
    if not check_subscription(user_id):
        ok, msg = activate_code(user_id, message.text.strip())
        await message.answer(msg)
        if ok:
            user_temp[user_id] = {"step": "time"}
            await message.answer("⏰ أدخل الساعة الحالية بالضبط (مثال: 14:30)")
        return

    state = user_temp.get(user_id, {})
    if state.get("step") == "time":
        user_temp[user_id]["time"] = message.text
        user_temp[user_id]["step"] = "rank"
        await message.answer("اختر رقم الورقة:", reply_markup=ranks_kb())
        return

    await message.answer("اختر رقم الورقة:", reply_markup=ranks_kb())

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
    if not data: return

    chosen = callback.data.replace("hand_", "")
    if chosen == "none": chosen = None

    rank = data.get("rank")
    suit = data.get("suit")
    prev = chosen

    result_text, _ = predict_hand(rank, suit, prev)

    await callback.message.edit_text(result_text)
    user_temp.pop(user_id, None)

# ================= WEBHOOK =================
WEBHOOK_PATH = "/webhook"

async def main():
    logging.basicConfig(level=logging.INFO)
    load_training()
    asyncio.create_task(auto_save())
    asyncio.create_task(strike_notifier())

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
