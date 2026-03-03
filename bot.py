import asyncio
import logging
import random
import string
import json
from datetime import datetime, timedelta
from collections import deque

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

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
def ai_ready():
    return len(AI_MEMORY) >= 20

def train_ai(rank, suit, prev, curr):
    AI_MEMORY.appendleft({
        "rank": rank, "suit": suit, "prev": prev, "curr": curr,
        "time": datetime.now().isoformat()
    })

def predict_hand(rank, suit, last_hand=None):
    if not ai_ready():
        return "🧠 الذكاء يحتاج 20 جولة تدريب من الادمن.", None

    hands = ["👥 زوجين", "🔗 متتالية", "🎴 ثلاثة", "♠️ فلش", "🏠 فل هاوس", "🂡 أربعة", "🌟 ستريت فلش"]
    scores = {h: 5 for h in hands}

    now = datetime.now()
    for item in list(AI_MEMORY)[:300]:
        created = datetime.fromisoformat(item["time"])
        days_old = (now - created).days
        time_weight = 4 if days_old <= 3 else 2 if days_old <= 7 else 1

        if item["rank"] == rank and item["suit"] == suit:
            scores[item["curr"]] += 6 * time_weight
        if last_hand and item["prev"] == last_hand:
            scores[item["curr"]] += 4 * time_weight
        scores[item["curr"]] += 1

    total = sum(scores.values())
    percentages = {h: round((scores[h] / total) * 100, 1) for h in hands}
    sorted_hands = sorted(percentages.items(), key=lambda x: x[1], reverse=True)

    high = sorted_hands[0]
    mid = sorted_hands[1]
    low = sorted_hands[-1]

    text = (
        f"🎯 TEXAS AI V8 ULTRA\n\n"
        f"🔥 عالي:\n{high[0]} ({high[1]}%)\n\n"
        f"⚖️ متوسط:\n{mid[0]} ({mid[1]}%)\n\n"
        f"⚠️ منخفض:\n{low[0]} ({low[1]}%)"
    )
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
    return True, "✅ تم تفعيل الاشتراك بنجاح!\nجاهز للعب 🔥"

def get_today_key():
    return datetime.now().strftime("%Y-%m-%d")

# ================= KEYBOARDS =================
def ranks_kb():
    ranks = ["A","K","Q","J","10","9","8","7","6","5","4","3","2"]
    rows = [ranks[i:i+4] for i in range(0, len(ranks), 4)]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=r, callback_data=f"rank_{r}") for r in row] for row in rows
    ])

def suits_kb():
    suits = ["♥️","♦️","♣️","♠️"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=s, callback_data=f"suit_{s}") for s in suits]])

def hands_kb(optional=False):
    hands = ["👥 زوجين", "🔗 متتالية", "🎴 ثلاثة", "♠️ فلش", "🏠 فل هاوس", "🂡 أربعة", "🌟 ستريت فلش"]
    kb = [[InlineKeyboardButton(text=h, callback_data=f"hand_{h}")] for h in hands]
    if optional:
        kb.append([InlineKeyboardButton(text="بدون", callback_data="hand_none")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================= ADMIN =================
@dp.message(Command("addcode"))
async def add_code(message: Message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    days = int(parts[1]) if len(parts) > 1 else 7
    code = generate_code()
    codes[code] = {"used": False, "days": days}
    save_json(CODES_FILE, codes)
    await message.answer(f"كود جديد:\n`{code}`\nالمدة: {days} يوم", parse_mode="Markdown")

@dp.message(Command("train"))
async def train(message: Message):
    if message.from_user.id != ADMIN_ID: return
    user_temp[message.from_user.id] = {"mode": "train"}
    await message.answer("اختر رقم الورقة:", reply_markup=ranks_kb())

@dp.message(Command("stats"))
async def show_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    today_key = get_today_key()
    today = daily_stats.get(today_key, {"total": 0, "correct": 0})
    t_perc = round(today["correct"] / today["total"] * 100, 1) if today["total"] else 0
    all_total = sum(d.get("total", 0) for d in daily_stats.values())
    all_correct = sum(d.get("correct", 0) for d in daily_stats.values())
    o_perc = round(all_correct / all_total * 100, 1) if all_total else 0
    await message.answer(f"📊 إحصائيات TEXAS AI V8\n\n📅 اليوم ({today_key}):\nالتخمينات: {today['total']}\nالصحيحة: {today['correct']}\nالنسبة: {t_perc}%\n\n📈 الإجمالي:\nالتخمينات: {all_total}\nالصحيحة: {all_correct}\nالنسبة: {o_perc}%")

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
    await callback.message.edit_text("الضربة السابقة؟ (اختياري)", reply_markup=hands_kb(optional=True))

@dp.callback_query(lambda c: c.data.startswith("hand_"))
async def choose_hand(callback: CallbackQuery):
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

    if user_id == ADMIN_ID and data.get("mode") == "train_result":
        train_ai(rank, suit, data.get("prev"), chosen)
        await callback.message.edit_text("✅ تم حفظ التدريب\nالذكاء يزيد يومياً 🔥")
        user_temp.pop(user_id, None)
        return

    if user_id == ADMIN_ID and data.get("mode") == "train":
        data["prev"] = chosen
        data["mode"] = "train_result"
        await callback.message.edit_text("شنو كانت النتيجة الفعلية؟", reply_markup=hands_kb())
        return

    if user_id == ADMIN_ID and data.get("mode") == "verify_actual":
        actual = chosen
        predicted_high = data["predicted_high"]
        prev = data["prev"]

        today_key = get_today_key()
        if today_key not in daily_stats:
            daily_stats[today_key] = {"total": 0, "correct": 0}

        daily_stats[today_key]["total"] += 1
        if actual == predicted_high:
            daily_stats[today_key]["correct"] += 1
            status = "✅ التخمين العالي صحيح!"
        else:
            status = f"❌ التخمين العالي كان: {predicted_high}\nالفعلي: {actual}"

        train_ai(rank, suit, prev, actual)
        save_daily_stats()

        await callback.message.edit_text(f"{status}\n\n📊 تم تحديث الإحصائيات")
        user_temp.pop(user_id, None)
        return

    result_text, predicted_high = predict_hand(rank, suit, chosen)

    if user_id == ADMIN_ID and predicted_high:
        user_temp[user_id] = {
            "mode": "verify_actual",
            "predicted_high": predicted_high,
            "rank": rank,
            "suit": suit,
            "prev": chosen
        }
        await callback.message.edit_text(result_text + "\n\n🔍 ما كانت النتيجة الفعلية؟", reply_markup=hands_kb())
        return

    await callback.message.edit_text(result_text)
    user_temp.pop(user_id, None)

# ================= RUN =================
async def main():
    logging.basicConfig(level=logging.INFO)
    load_training()
    asyncio.create_task(auto_save())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
