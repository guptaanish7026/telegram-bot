# bot.py
import os
import random
import string
import json
import base64
import logging
import asyncio
import requests
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from motor.motor_asyncio import AsyncIOMotorClient

# ---------- Environment Variables ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Anish_Gupta:Anish_Gupta@studyonemanagerbot.bnbknvf.mongodb.net/?appName=StudyOneManagerBot")

KEY_API_URL = os.getenv("KEY_API_URL", "https://study-one-access.vercel.app/access/token=Anuj%40%23%E2%82%B9_%26123")
ANALYTICS_API = os.getenv("ANALYTICS_API", "https://study-one-access.vercel.app/analytics?token=Anuj%40%23%E2%82%B9_%26123")
CLEAR_API = os.getenv("CLEAR_API", "https://study-one-access.vercel.app/clear?token=Anuj%40%23%E2%82%B9_%26123")
MEDIA_API_URL = os.getenv("MEDIA_API_URL", "https://study-one-access.vercel.app/media")
SHORTNER_URL = os.getenv("SHORTNER_URL", "https://vplink.in/api")
SHORTNER_API = os.getenv("SHORTNER_API", "6351aaa9474766bb1f8575ba24a9897c2761985f")

# ---------- MongoDB Initialization ----------
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.telegram_bot
users_col = db.users
tokens_col = db.tokens
apps_col = db.apps
channels_col = db.channels
settings_col = db.settings

# ---------- In-Memory Cache ----------
users = {}
tokens = {}
apps = []
channels = []
settings = {
    "key_validity": 24,
    "shortner_url": SHORTNER_URL,
    "shortner_api": SHORTNER_API,
    "tutorial_url": "",
}

# ---------- Conversation states ----------
(
    STATE_KEY_DEVICE_ID,
    STATE_ADMIN_ADD_APP_NAME,
    STATE_ADMIN_ADD_APP_URL,
    STATE_ADMIN_EDIT_SETTING_VALUE,
    STATE_ADMIN_KEY_VALIDITY,
    STATE_PREMIUM_NAME,
    STATE_PREMIUM_DEVICE,
    STATE_PREMIUM_VALIDITY,
    STATE_FJ_ADD_NAME,
    STATE_FJ_ADD_ID,
    STATE_FJ_ADD_URL,
    STATE_FJ_EDIT_NEWID,
    STATE_FJ_EDIT_NEWURL,
    STATE_BROADCAST,
    STATE_TUTORIAL_URL,
) = range(15)

# ---------- Logging ----------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Health Server ----------
def run_health_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Health server listening on port {port}")
    server.serve_forever()

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

# ---------- Database Functions ----------
async def load_data_from_db():
    global users, tokens, apps, channels, settings
    logger.info("Loading data from MongoDB...")
    try:
        async for doc in users_col.find({}):
            users[doc["_id"]] = {"first_name": doc["first_name"], "points": doc["points"]}
        async for doc in tokens_col.find({}):
            tokens[doc["_id"]] = {"user_id": doc["user_id"], "used": doc["used"]}
        async for doc in apps_col.find({}):
            apps.append({"name": doc["name"], "url": doc["url"]})
        async for doc in channels_col.find({}):
            channels.append({"id": doc["id"], "url": doc["url"], "name": doc["name"]})
        settings_doc = await settings_col.find_one({"_id": "settings"})
        if settings_doc:
            settings.update(settings_doc["data"])
        else:
            await settings_col.insert_one({"_id": "settings", "data": settings})
        logger.info(f"Loaded {len(users)} users, {len(tokens)} tokens, {len(apps)} apps, {len(channels)} channels")
    except Exception as e:
        logger.error(f"Failed to load data from MongoDB: {e}")

async def save_user(user_id, data):
    await users_col.update_one({"_id": user_id}, {"$set": data}, upsert=True)

async def save_token(token, data):
    await tokens_col.update_one({"_id": token}, {"$set": data}, upsert=True)

async def save_apps():
    await apps_col.delete_many({})
    if apps:
        await apps_col.insert_many(apps)

async def save_channels():
    await channels_col.delete_many({})
    if channels:
        await channels_col.insert_many(channels)

async def save_settings():
    await settings_col.update_one({"_id": "settings"}, {"$set": {"data": settings}}, upsert=True)

# ---------- Helper Functions ----------
def shorten_url(long_url: str) -> str:
    try:
        api_url = f"{settings['shortner_url']}?api={settings['shortner_api']}&url={requests.utils.quote(long_url)}"
        resp = requests.get(api_url, timeout=10)
        data = resp.json()
        return data.get("shortenedUrl", long_url)
    except Exception:
        return long_url

async def check_joined(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(ch["id"], user_id)
            if member.status in ["left", "kicked", "banned"]:
                return False
        except Exception:
            return False
    return True

def join_keyboard() -> InlineKeyboardMarkup:
    kb = []
    for ch in channels:
        kb.append([InlineKeyboardButton(f"🔗 Join {ch['name']}", url=ch["url"])])
    kb.append([InlineKeyboardButton("✅ I've Joined", callback_data="check_join")])
    return InlineKeyboardMarkup(kb)

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Points", callback_data="menu_points"),
         InlineKeyboardButton("🔑 Key", callback_data="menu_key")],
        [InlineKeyboardButton("💬 Support", url="https://t.me/StudyOne_Support_Bot"),
         InlineKeyboardButton("📱 Apps", callback_data="menu_apps")],
    ])

def points_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Get Points", callback_data="points_get")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")],
    ])

def key_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Get Key", callback_data="key_get")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")],
    ])

def apps_keyboard() -> InlineKeyboardMarkup:
    kb = []
    for app in apps:
        kb.append([InlineKeyboardButton(f"📲 {app['name']}", url=app["url"])])
    kb.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb)

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Apps", callback_data="admin_apps"),
         InlineKeyboardButton("🔗 Shortner", callback_data="admin_shortner")],
        [InlineKeyboardButton("🔑 Key", callback_data="admin_key"),
         InlineKeyboardButton("📊 Analytics", callback_data="admin_analytics")],
        [InlineKeyboardButton("🔒 Force Join", callback_data="admin_forcejoin"),
         InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")],
    ])

def admin_back_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]])

def generate_token(length=8) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def format_key_response(raw: str) -> str:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            parts = []
            if data.get("success") is not None:
                parts.append(f"✅ Success: {data['success']}")
            if "key" in data:
                parts.append(f"🔑 Key: <code>{data['key']}</code>")
            if "validity" in data:
                parts.append(f"⏳ Validity: {data['validity']} hours")
            if "name" in data:
                parts.append(f"👤 Name: {data['name']}")
            if "deviceId" in data:
                parts.append(f"📱 Device ID: {data['deviceId']}")
            if "message" in data:
                parts.append(f"ℹ️ {data['message']}")
            return "\n".join(parts)
        return raw
    except Exception:
        return raw

def get_file_type_from_url(url: str) -> str:
    ext = url.split("?")[0].split(".")[-1].lower()
    if ext in ["mp4", "m3u8", "avi", "mov", "mkv", "flv", "wmv"]:
        return "video"
    elif ext in ["jpg", "jpeg", "png", "gif", "bmp", "webp"]:
        return "image"
    elif ext == "pdf":
        return "pdf"
    else:
        return "other"

async def process_media(update: Update, context: ContextTypes.DEFAULT_TYPE, url_token: str, waiting_msg):
    try:
        resp = requests.get(f"{MEDIA_API_URL}?urltoken={url_token}&token=Anuj%40%23%E2%82%B9_%26123", timeout=10)
        data = resp.json()
    except Exception as e:
        logger.error(f"Media API error: {e}")
        await waiting_msg.edit_text("❌ Media is not found in database.")
        return

    if not data.get("success", True) or "error" in data:
        await waiting_msg.edit_text("❌ Media is not found in database.")
        return

    media_url = data.get("url")
    if not media_url:
        await waiting_msg.edit_text("❌ Media is not found in database.")
        return

    file_type = get_file_type_from_url(media_url)
    if file_type == "video":
        encoded = base64.b64encode(media_url.encode()).decode()
        player_url = f"https://study-one-stream.base44.app/player?data={encoded}"
        button = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Play Video", url=player_url)]])
        await waiting_msg.edit_text("🎬 Your media is ready.", reply_markup=button)
    elif file_type == "image":
        button = InlineKeyboardMarkup([[InlineKeyboardButton("🖼️ View Image", url=media_url)]])
        await waiting_msg.edit_text("🖼️ Your media is ready.", reply_markup=button)
    elif file_type == "pdf":
        button = InlineKeyboardMarkup([[InlineKeyboardButton("📄 View PDF", url=media_url)]])
        await waiting_msg.edit_text("📄 Your media is ready.", reply_markup=button)
    else:
        button = InlineKeyboardMarkup([[InlineKeyboardButton("📁 View File", url=media_url)]])
        await waiting_msg.edit_text("📁 Your media is ready.", reply_markup=button)

# ---------- /start handler ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    raw_text = update.message.text.strip()

    # Media deep link
    if raw_text.startswith("/start url_token_"):
        parts = raw_text.split()
        if len(parts) > 1:
            param = parts[1]
            if param.startswith("url_token_"):
                url_token = param[len("url_token_"):]
                if channels and not await check_joined(context, user_id):
                    await update.message.reply_text("🚫 Please join all channels first.", reply_markup=join_keyboard())
                    return
                waiting_msg = await update.message.reply_text("⏳ Getting Your Media, please wait...")
                await process_media(update, context, url_token, waiting_msg)
                return

    # Referral deep link
    has_ref = raw_text.startswith("/start ref_")
    ref_user_id_str = None
    token = None
    if has_ref:
        parts = raw_text.split()
        if len(parts) > 1:
            ref_param = parts[1]
            if ref_param.startswith("ref_"):
                ref_parts = ref_param[4:].split("_", 1)
                if len(ref_parts) == 2:
                    ref_user_id_str, token = ref_parts

    # Force join check
    if channels:
        if not await check_joined(context, user_id):
            msg = "🚫 You must join all our official channels to use the bot.\nPlease join the channels below and then click ✅ I've Joined."
            await update.message.reply_text(msg, reply_markup=join_keyboard())
            return

    # Register or welcome back
    if user_id not in users:
        users[user_id] = {"first_name": first_name, "points": 5}
        await save_user(user_id, {"first_name": first_name, "points": 5})
        welcome = f"👋 Welcome to <b>StudyOne Manager Bot</b>, {first_name}!\n🎁 You have received <b>5 free points</b>.\nUse them wisely! Choose an option below:"
        await update.message.reply_text(welcome, parse_mode="HTML", reply_markup=main_menu_keyboard())
    else:
        users[user_id]["first_name"] = first_name
        await save_user(user_id, {"first_name": first_name})
        await update.message.reply_text(f"👋 Welcome back, {first_name}!", reply_markup=main_menu_keyboard())

    # Process referral
    if has_ref and ref_user_id_str and token:
        try:
            ref_user_id = int(ref_user_id_str)
        except ValueError:
            ref_user_id = None
        if ref_user_id and token in tokens:
            tkn_data = tokens[token]
            if not tkn_data["used"] and tkn_data["user_id"] == ref_user_id and ref_user_id == user_id:
                tkn_data["used"] = True
                await save_token(token, {"user_id": ref_user_id, "used": True})
                users[user_id]["points"] += 5
                await save_user(user_id, {"points": users[user_id]["points"]})
                await update.message.reply_text("🎉 <b>+5 points</b> added to your balance!", parse_mode="HTML")
            else:
                await update.message.reply_text("❌ Link already used or invalid.")
        else:
            await update.message.reply_text("❌ Link already used or invalid.")

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await check_joined(context, user_id):
        await query.message.delete()
        first_name = query.from_user.first_name
        if user_id not in users:
            users[user_id] = {"first_name": first_name, "points": 5}
            await save_user(user_id, {"first_name": first_name, "points": 5})
            await context.bot.send_message(
                user_id,
                f"👋 Welcome to <b>StudyOne Manager Bot</b>, {first_name}!\n🎁 You have received <b>5 free points</b>.\nChoose an option below:",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
        else:
            users[user_id]["first_name"] = first_name
            await save_user(user_id, {"first_name": first_name})
            await context.bot.send_message(user_id, f"👋 Welcome back, {first_name}!", reply_markup=main_menu_keyboard())
    else:
        await query.answer("❌ You haven't joined all channels yet. Please join them first.", show_alert=True)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📋 Main Menu:", reply_markup=main_menu_keyboard())

async def points_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    points = users.get(user_id, {}).get("points", 0)
    text = f"⭐ Your Balance: <b>{points} points</b>\n\nWhat would you like to do?"
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=points_menu_keyboard())

async def points_get_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    token = generate_token()
    tokens[token] = {"user_id": user_id, "used": False}
    await save_token(token, {"user_id": user_id, "used": False})
    deep_link = f"https://t.me/{context.bot.username}?start=ref_{user_id}_{token}"
    short = shorten_url(deep_link)

    if settings.get("tutorial_url"):
        tutorial_btn = InlineKeyboardButton("🎬 Tutorial", url=settings["tutorial_url"])
    else:
        tutorial_btn = InlineKeyboardButton("🎬 Tutorial", callback_data="tutorial_not_set")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Claim Link", url=short)],
        [tutorial_btn],
        [InlineKeyboardButton("⬅️ Back", callback_data="points_back")],
    ])
    text = "🎁 Earn <b>5 points</b> by completing a simple task.\nClick the Link button to claim your reward.\nNeed help? Watch the tutorial."
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def tutorial_not_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⚠️ Tutorial URL is not set yet. Please contact admin.", show_alert=True)

async def points_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await points_menu_callback(update, context)

# ---------- Key system ----------
async def key_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔑 Key System:", reply_markup=key_menu_keyboard())

async def key_get_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    points = users.get(user_id, {}).get("points", 0)
    if points < 5:
        text = f"❌ <b>Insufficient Balance!</b>\nYou need at least 5 points to generate a key.\nYour balance: {points} points.\nEarn more points by clicking below."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Get Points", callback_data="points_get")],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        return
    context.user_data["awaiting_device"] = True
    text = "📱 Please send your <b>Device ID</b>.\n\n<i>🔹 How to get your Device ID?\nVisit our site, copy the ID and paste it here.</i>"
    await query.message.reply_text(text, parse_mode="HTML")
    return STATE_KEY_DEVICE_ID

async def receive_device_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    device_id = update.message.text.strip()
    user = users.get(user_id)
    if not user:
        await update.message.reply_text("⚠️ You are not registered. Use /start first.")
        return ConversationHandler.END

    validity = settings["key_validity"]
    try:
        url = f"{KEY_API_URL}/validity={validity}?name={requests.utils.quote(user['first_name'])}&deviceId={requests.utils.quote(device_id)}"
        resp = requests.get(url, timeout=15)
        raw = resp.text
        formatted = format_key_response(raw)
    except Exception as e:
        formatted = f"❌ Error: {e}"

    users[user_id]["points"] = max(0, user["points"] - 5)
    await save_user(user_id, {"points": users[user_id]["points"]})
    await update.message.reply_text(
        f"✅ <b>Key Generated!</b>\n\n{formatted}\n\nYour new balance: {users[user_id]['points']} points.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

async def key_conv_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Key generation cancelled.")
    return ConversationHandler.END

# ---------- Apps ----------
async def apps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not apps:
        await query.edit_message_text("📭 No apps have been added yet.", reply_markup=apps_keyboard())
    else:
        await query.edit_message_text("📱 Available Apps:", reply_markup=apps_keyboard())

# ---------- Admin panel ----------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("🔧 <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_keyboard())

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔧 <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_keyboard())

# ---------- Admin Apps Management ----------
async def admin_apps_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add App", callback_data="admin_add_app")],
        [InlineKeyboardButton("➖ Remove App", callback_data="admin_remove_app")],
        [InlineKeyboardButton("📃 List Apps", callback_data="admin_list_apps")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")],
    ])
    await query.edit_message_text("📦 <b>Apps Management</b>", parse_mode="HTML", reply_markup=kb)

async def admin_add_app_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📝 Enter the app name:")
    return STATE_ADMIN_ADD_APP_NAME

async def admin_add_app_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["app_name"] = update.message.text
    await update.message.reply_text("🔗 Now send the app URL:")
    return STATE_ADMIN_ADD_APP_URL

async def admin_add_app_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data.pop("app_name")
    apps.append({"name": name, "url": update.message.text})
    await save_apps()
    await update.message.reply_text(f"✅ App <b>{name}</b> added successfully.", parse_mode="HTML", reply_markup=admin_back_panel_kb())
    return ConversationHandler.END

async def admin_remove_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not apps:
        await query.answer("No apps to remove.", show_alert=True)
        return
    kb = [[InlineKeyboardButton(a["name"], callback_data=f"delapp_{i}")] for i, a in enumerate(apps)]
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="admin_apps")])
    await query.edit_message_text("🗑️ Select app to remove:", reply_markup=InlineKeyboardMarkup(kb))

async def admin_del_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    removed = apps.pop(idx)
    await save_apps()
    await query.edit_message_text(f"❌ Removed <b>{removed['name']}</b>.", parse_mode="HTML", reply_markup=admin_back_panel_kb())

async def admin_list_apps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if apps:
        text = "\n".join(f"• {a['name']} → {a['url']}" for a in apps)
    else:
        text = "No apps."
    await query.edit_message_text(text, reply_markup=admin_back_panel_kb())

# ---------- Admin Shortner Settings ----------
async def admin_shortner_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Shortner URL", callback_data="edit_shortner_url")],
        [InlineKeyboardButton("🔑 API Key", callback_data="edit_shortner_api")],
        [InlineKeyboardButton("🎬 Tutorial URL", callback_data="set_tutorial_url")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")],
    ])
    await query.edit_message_text("⚙️ <b>Link Shortner Settings</b>", parse_mode="HTML", reply_markup=kb)

async def edit_shortner_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split("_", 2)[2]  # edit_shortner_url -> "url" or "api"
    context.user_data["setting_field"] = field
    current = settings.get(field, "")
    example = "https://your-shortner.com/api" if "url" in field else "abc123apikey"
    text = f"🔧 Current value: <code>{current}</code>\n\nSend the new {field} (e.g. <code>{example}</code>):"
    await query.message.reply_text(text, parse_mode="HTML")
    return STATE_ADMIN_EDIT_SETTING_VALUE

async def edit_shortner_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.pop("setting_field")
    settings[field] = update.message.text.strip()
    await save_settings()
    await update.message.reply_text(f"✅ <b>{field}</b> updated successfully.", parse_mode="HTML", reply_markup=admin_back_panel_kb())
    return ConversationHandler.END

async def set_tutorial_url_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🎬 Send the tutorial URL:")
    return STATE_TUTORIAL_URL

async def set_tutorial_url_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings["tutorial_url"] = update.message.text.strip()
    await save_settings()
    await update.message.reply_text("✅ Tutorial URL saved.", reply_markup=admin_back_panel_kb())
    return ConversationHandler.END

# ---------- Admin Key Settings ----------
async def admin_key_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ Key Validity", callback_data="key_validity")],
        [InlineKeyboardButton("✨ Premium Key", callback_data="premium_key_menu")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")],
    ])
    await query.edit_message_text("🔑 <b>Key Settings</b>", parse_mode="HTML", reply_markup=kb)

async def set_key_validity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("⏳ Send the default key validity in hours (integer):")
    return STATE_ADMIN_KEY_VALIDITY

async def receive_key_validity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        hours = int(update.message.text)
        settings["key_validity"] = hours
        await save_settings()
        await update.message.reply_text(f"✅ Key validity set to <b>{hours} hours</b>.", parse_mode="HTML", reply_markup=admin_back_panel_kb())
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Cancelled.", reply_markup=admin_back_panel_kb())
    return ConversationHandler.END

async def premium_key_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("👤 Enter user's name for premium key:")
    return STATE_PREMIUM_NAME

async def premium_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pname"] = update.message.text
    await update.message.reply_text("📱 Send Device ID:")
    return STATE_PREMIUM_DEVICE

async def premium_device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pdevice"] = update.message.text
    await update.message.reply_text("⏳ Send validity in hours:")
    return STATE_PREMIUM_VALIDITY

async def premium_validity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        validity = int(update.message.text)
        name = context.user_data.pop("pname")
        device = context.user_data.pop("pdevice")
        url = f"{KEY_API_URL}/validity={validity}?name={requests.utils.quote(name)}&deviceId={requests.utils.quote(device)}"
        raw = requests.get(url, timeout=15).text
        formatted = format_key_response(raw)
        await update.message.reply_text(f"✨ <b>Premium Key:</b>\n{formatted}", parse_mode="HTML", reply_markup=admin_back_panel_kb())
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}", reply_markup=admin_back_panel_kb())
    return ConversationHandler.END

# ---------- Admin Analytics ----------
async def admin_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        resp = requests.get(ANALYTICS_API, timeout=10)
        info = resp.json()
        total_keys = info.get("total_keys_generated", "N/A")
        total_proxy = info.get("total_proxy_calls", "N/A")
        recent = info.get("recent_activity", [])[:5]
        text = f"📊 <b>Analytics</b>\n\n🔑 Keys generated: {total_keys}\n🌐 Proxy calls: {total_proxy}\n\n📌 Recent:\n" + "\n".join(f"• {r}" for r in recent)
    except Exception as e:
        text = f"❌ Error fetching analytics: {e}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 Clear Data", callback_data="admin_clear")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def admin_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        resp = requests.get(CLEAR_API, timeout=10)
        await query.edit_message_text(f"🧹 {resp.text}", reply_markup=admin_back_panel_kb())
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {e}", reply_markup=admin_back_panel_kb())

# ---------- Admin Force Join ----------
async def admin_forcejoin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Channel", callback_data="fj_add")],
        [InlineKeyboardButton("✏️ Edit Channel", callback_data="fj_edit")],
        [InlineKeyboardButton("➖ Remove Channel", callback_data="fj_remove")],
        [InlineKeyboardButton("📃 List Channels", callback_data="fj_list")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")],
    ])
    await query.edit_message_text("🔒 <b>Force Join Management</b>", parse_mode="HTML", reply_markup=kb)

async def fj_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📛 Enter channel name (e.g. <b>News</b>):", parse_mode="HTML")
    return STATE_FJ_ADD_NAME

async def fj_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fj_name"] = update.message.text
    await update.message.reply_text("🆔 Send channel ID (e.g. -1001234567890):")
    return STATE_FJ_ADD_ID

async def fj_add_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ch_id = int(update.message.text)
        context.user_data["fj_id"] = str(ch_id)
        await update.message.reply_text("🔗 Send channel invite link:")
        return STATE_FJ_ADD_URL
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Cancelled.", reply_markup=admin_back_panel_kb())
        return ConversationHandler.END

async def fj_add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data.pop("fj_name")
    ch_id = context.user_data.pop("fj_id")
    channels.append({"id": ch_id, "url": update.message.text, "name": name})
    await save_channels()
    await update.message.reply_text(f"✅ Channel <b>{name}</b> added.", parse_mode="HTML", reply_markup=admin_back_panel_kb())
    return ConversationHandler.END

async def fj_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not channels:
        await query.answer("No channels to edit.", show_alert=True)
        return
    kb = [[InlineKeyboardButton(ch["name"], callback_data=f"editch_{i}")] for i, ch in enumerate(channels)]
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="admin_forcejoin")])
    await query.edit_message_text("✏️ Select channel to edit:", reply_markup=InlineKeyboardMarkup(kb))

async def fj_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    context.user_data["edit_idx"] = idx
    await query.message.reply_text("🆔 Send new channel ID (or /skip):")
    return STATE_FJ_EDIT_NEWID

async def fj_edit_newid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "/skip":
        try:
            new_id = str(int(update.message.text))
            idx = context.user_data["edit_idx"]
            channels[idx]["id"] = new_id
        except ValueError:
            await update.message.reply_text("❌ Invalid ID. Cancelled.", reply_markup=admin_back_panel_kb())
            return ConversationHandler.END
    await update.message.reply_text("🔗 Send new URL (or /skip):")
    return STATE_FJ_EDIT_NEWURL

async def fj_edit_newurl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "/skip":
        idx = context.user_data.pop("edit_idx")
        channels[idx]["url"] = update.message.text
    else:
        context.user_data.pop("edit_idx", None)
    await save_channels()
    await update.message.reply_text("✅ Channel updated.", reply_markup=admin_back_panel_kb())
    return ConversationHandler.END

async def fj_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not channels:
        await query.answer("No channels to remove.", show_alert=True)
        return
    kb = [[InlineKeyboardButton(ch["name"], callback_data=f"delch_{i}")] for i, ch in enumerate(channels)]
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="admin_forcejoin")])
    await query.edit_message_text("🗑️ Select channel to remove:", reply_markup=InlineKeyboardMarkup(kb))

async def fj_del_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    removed = channels.pop(idx)
    await save_channels()
    await query.edit_message_text(f"❌ Removed <b>{removed['name']}</b>.", parse_mode="HTML", reply_markup=admin_back_panel_kb())

async def fj_list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if channels:
        text = "\n".join(f"• <b>{ch['name']}</b> (ID: {ch['id']})" for ch in channels)
    else:
        text = "No channels."
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=admin_back_panel_kb())

# ---------- Admin Broadcast ----------
async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📢 Send the content to broadcast (text, photo, video).")
    return STATE_BROADCAST

async def admin_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sent = 0
    for uid in users:
        try:
            await context.bot.copy_message(uid, update.effective_chat.id, update.message.message_id)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to <b>{sent}</b> users.", parse_mode="HTML", reply_markup=admin_back_panel_kb())
    return ConversationHandler.END

# ---------- Main Entry Point (Fixed for Python 3.14) ----------
def main():
    # Create and set a new event loop for the main thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Start health server in background thread (doesn't need asyncio)
    Thread(target=run_health_server, daemon=True).start()
    
    # Load data synchronously using the loop
    loop.run_until_complete(load_data_from_db())
    
    # Build the application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    
    # Callback query handlers
    application.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(points_menu_callback, pattern="^menu_points$"))
    application.add_handler(CallbackQueryHandler(points_get_callback, pattern="^points_get$"))
    application.add_handler(CallbackQueryHandler(tutorial_not_set_callback, pattern="^tutorial_not_set$"))
    application.add_handler(CallbackQueryHandler(points_back_callback, pattern="^points_back$"))
    application.add_handler(CallbackQueryHandler(key_menu_callback, pattern="^menu_key$"))
    application.add_handler(CallbackQueryHandler(apps_callback, pattern="^menu_apps$"))
    application.add_handler(CallbackQueryHandler(admin_panel_callback, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_apps_menu, pattern="^admin_apps$"))
    application.add_handler(CallbackQueryHandler(admin_list_apps, pattern="^admin_list_apps$"))
    application.add_handler(CallbackQueryHandler(admin_remove_app, pattern="^admin_remove_app$"))
    application.add_handler(CallbackQueryHandler(admin_del_app, pattern="^delapp_"))
    application.add_handler(CallbackQueryHandler(admin_shortner_menu, pattern="^admin_shortner$"))
    application.add_handler(CallbackQueryHandler(admin_key_menu, pattern="^admin_key$"))
    application.add_handler(CallbackQueryHandler(admin_analytics, pattern="^admin_analytics$"))
    application.add_handler(CallbackQueryHandler(admin_clear, pattern="^admin_clear$"))
    application.add_handler(CallbackQueryHandler(admin_forcejoin_menu, pattern="^admin_forcejoin$"))
    application.add_handler(CallbackQueryHandler(fj_edit_start, pattern="^fj_edit$"))
    application.add_handler(CallbackQueryHandler(fj_edit_select, pattern="^editch_"))
    application.add_handler(CallbackQueryHandler(fj_remove, pattern="^fj_remove$"))
    application.add_handler(CallbackQueryHandler(fj_del_channel, pattern="^delch_"))
    application.add_handler(CallbackQueryHandler(fj_list_channels, pattern="^fj_list$"))
    
    # Conversation handlers
    conv_key = ConversationHandler(
        entry_points=[CallbackQueryHandler(key_get_callback, pattern="^key_get$")],
        states={STATE_KEY_DEVICE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_device_id)]},
        fallbacks=[MessageHandler(filters.COMMAND, key_conv_fallback)],
    )
    application.add_handler(conv_key)
    
    conv_add_app = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_app_start, pattern="^admin_add_app$")],
        states={
            STATE_ADMIN_ADD_APP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_app_name)],
            STATE_ADMIN_ADD_APP_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_app_url)],
        },
        fallbacks=[],
    )
    application.add_handler(conv_add_app)
    
    conv_edit_shortner = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_shortner_start, pattern="^edit_shortner_url$"),
            CallbackQueryHandler(edit_shortner_start, pattern="^edit_shortner_api$"),
        ],
        states={STATE_ADMIN_EDIT_SETTING_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_shortner_value)]},
        fallbacks=[],
    )
    application.add_handler(conv_edit_shortner)
    
    conv_key_validity = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_key_validity, pattern="^key_validity$")],
        states={STATE_ADMIN_KEY_VALIDITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_key_validity)]},
        fallbacks=[],
    )
    application.add_handler(conv_key_validity)
    
    conv_premium = ConversationHandler(
        entry_points=[CallbackQueryHandler(premium_key_start, pattern="^premium_key_menu$")],
        states={
            STATE_PREMIUM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, premium_name)],
            STATE_PREMIUM_DEVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, premium_device)],
            STATE_PREMIUM_VALIDITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, premium_validity)],
        },
        fallbacks=[],
    )
    application.add_handler(conv_premium)
    
    conv_fj_add = ConversationHandler(
        entry_points=[CallbackQueryHandler(fj_add_start, pattern="^fj_add$")],
        states={
            STATE_FJ_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, fj_add_name)],
            STATE_FJ_ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, fj_add_id)],
            STATE_FJ_ADD_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, fj_add_url)],
        },
        fallbacks=[],
    )
    application.add_handler(conv_fj_add)
    
    conv_fj_edit = ConversationHandler(
        entry_points=[CallbackQueryHandler(fj_edit_select, pattern="^editch_")],
        states={
            STATE_FJ_EDIT_NEWID: [MessageHandler(filters.TEXT & ~filters.COMMAND, fj_edit_newid)],
            STATE_FJ_EDIT_NEWURL: [MessageHandler(filters.TEXT & ~filters.COMMAND, fj_edit_newurl)],
        },
        fallbacks=[],
    )
    application.add_handler(conv_fj_edit)
    
    conv_broadcast = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={STATE_BROADCAST: [MessageHandler(filters.ALL, admin_broadcast_content)]},
        fallbacks=[],
    )
    application.add_handler(conv_broadcast)
    
    conv_tutorial = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_tutorial_url_start, pattern="^set_tutorial_url$")],
        states={STATE_TUTORIAL_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_tutorial_url_value)]},
        fallbacks=[],
    )
    application.add_handler(conv_tutorial)
    
    # Start polling
    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
