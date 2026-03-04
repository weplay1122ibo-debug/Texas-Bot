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

def train_ai(rank, suit, prev, curr):
    AI_MEMORY.appendleft({
        "rank": rank, "suit": suit, "prev": prev, "curr": curr,
        "time": datetime.now().isoformat()
    })

def predict_hand(rank, suit, last_hand=None):
    hands = ["👥 زوجين", "🔗 متتالية", "🎴 ثلاثة", "♠️ فلش", "🏠 فل هاوس", "🂡 أربعة", "🌟 ستريت فلش"]
    scores = {h: 3 for h in hands}

    now = datetime.now()
    for item in list(AI_MEMORY)[:500]:
        created = datetime.fromisoformat(item["time"])
        days_old = (now - created).days
        time_weight = 5 if days_old <= 3 else 3 if days_old <= 7 else 1

        if item["rank"] == rank and item["suit"] == suit:
            scores[item["curr"]] += 12 * time_weight
        if last_hand and item["prev"] == last_hand:
            scores[item["curr"]] += 8 * time_weight
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

# ================= SUBSCRIPTION & ADMIN =================
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
    return True, "✅ تم التفعيل!\nجاهز للعب فوراً 🔥"

@dp.message(lambda m: m.text and m.text.startswith("/addcode"))
async def add_code(message: Message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    days = int(parts[1]) if len(parts) > 1 else 7
    code = generate_code()
    codes[code] = {"used": False, "days": days}
    save_json(CODES_FILE, codes)
    await message.answer(f"✅ كود جديد تم إنشاؤه!\n\n`{code}`\nالمدة: {days} يوم", parse_mode="Markdown")

@dp.message(Command("users"))
async def show_subscribers(message: Message):
    if message.from_user.id != ADMIN_ID: return
    active = {uid: data for uid, data in users.items() if datetime.fromisoformat(data) > datetime.now()}
    text = f"👥 عدد المشتركين النشطين: **{len(active)}**\n\n"
    for uid, expire in active.items():
        exp = datetime.fromisoformat(expire).strftime("%Y-%m-%d")
        text += f"• `{uid}` → {exp}\n"
    await message.answer(text or "لا يوجد مشتركين حالياً")

@dp.message(Command("revoke"))
async def revoke_subscription(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = int(message.text.split()[1])
        if str(target) in users:
            del users[str(target)]
            save_json(USERS_FILE, users)
            await message.answer(f"✅ تم إنهاء اشتراك `{target}`")
        else:
            await message.answer("❌ المستخدم غير موجود")
    except:
        await message.answer("استخدام: `/revoke 123456789`")

@dp.message(Command("trainstatus"))
async def train_status(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer(f"📊 عدد الجولات المدربة: **{get_training_count()}**")

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

# ================= FLOW =================
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
            await message.answer("اختر رقم الورقة:", reply_markup=ranks_kb())
        return
    await message.answer("اختر رقم الورقة:", reply_markup=ranks_kb())

@dp.callback_query(lambda c: c.data.startswith("rank_"))
async def choose_rank(callback: CallbackQuery):
    await callback.answer()
    user_temp[callback.from_user.id] = user_temp.get(callback.from_user.id, {})
    user_temp[callback.from_user.id]["rank"] = callback.data.split("_")[1]
    await callback.message.edit_text("اختر النوع:", reply_markup=suits_kb())

@dp.callback_query(lambda c: c.data.startswith("suit_"))
async def choose_suit(callback: CallbackQuery):
    await callback.answer()
    data = user_temp.get(callback.from_user.id, {})
    if not data or "rank" not in data:
        await callback.message.edit_text("ابدأ من جديد")
        return
    data["suit"] = callback.data.split("_")[1]
    await callback.message.edit_text("الضربة السابقة؟ (اختياري)", reply_markup=hands_kb())

@dp.callback_query(lambda c: c.data.startswith("hand_"))
async def handle_hand(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    data = user_temp.get(user_id)
    if not data:
        await callback.answer("الجلسة انتهت، ابدأ من جديد", show_alert=True)
        return

    chosen = callback.data.replace("hand_", "")
    if chosen == "none": chosen = None

    rank = data.get("rank")
    suit = data.get("suit")
    if not rank or not suit: return

    # حفظ النتيجة الفعلية
    if data.get("mode") == "verify_actual":
        actual = chosen
        predicted_high = data["predicted_high"]
        prev = data["prev"]

        today_key = datetime.now().strftime("%Y-%m-%d")
        if today_key not in daily_stats:
            daily_stats[today_key] = {"total": 0, "correct": 0}

        daily_stats[today_key]["total"] += 1

        if actual == predicted_high:
            daily_stats[today_key]["correct"] += 1
            status = "🎉 مبروك! توقعك كان ممتاز 🔥\n\nاكتب `تم` للاستمرار"
        else:
            status = f"❌ التخمين كان: {predicted_high}\nالفعلي: {actual}\n\nاكتب `تم` للاستمرار"

        train_ai(rank, suit, prev, actual)
        save_daily_stats()

        await callback.message.edit_text(status)
        user_temp.pop(user_id, None)
        return

    # التوقع + طلب النتيجة
    result_text, predicted_high = predict_hand(rank, suit, chosen)

    user_temp[user_id] = {
        "mode": "verify_actual",
        "predicted_high": predicted_high,
        "rank": rank,
        "suit": suit,
        "prev": chosen
    }

    await callback.message.edit_text(
        result_text + "\n\n🔍 ما كانت النتيجة الفعلية؟",
        reply_markup=hands_kb()
    )

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
