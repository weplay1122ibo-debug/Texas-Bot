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
    mid = sorted_hands[1]
    low = sorted_hands[-1]

    note = "\n\n🧠 الذكاء لسة في طور التدريب (كل ما يدرب الأدمن يصير أدق)" if get_training_count() < 20 else ""

    # كتابة النص بطريقة آمنة 100% (بدون adjacent f-strings)
    text = f"""🎯 TEXAS AI V8 ULTRA

🔥 عالي:
{high[0]} ({high[1]}%)

⚖️ متوسط:
{mid[0]} ({mid[1]}%)

⚠️ منخفض:
{low[0]} ({low[1]}%){note}"""

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
    return True, "✅ تم تفعيل الاشتراك بنجاح!\nجاهز للعب فوراً 🔥"

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

@dp.message(Command("trainstatus"))
async def train_status(message: Message):
    if message.from_user.id != ADMIN_ID: return
    count = get_training_count()
    await message.answer(f"📊 حالة التدريب\n\nعدد الجولات المدربة: **{count}**\n\nكل ما تدرب أكثر = البوت يصير أذكى 🔥")

@dp.message(Command("stats"))
async def show_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    today_key = get_today_key()
    today = daily_stats.get(today_key, {"total": 0, "correct": 0})
    t_perc = round(today["correct"] / today["total"] * 100, 1) if today["total"] else 0
    all_total = sum(d.get("total", 0) for d in daily_stats.values())
    all_correct = sum(d.get("correct", 0
