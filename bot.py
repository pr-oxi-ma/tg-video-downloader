#!/usr/bin/env python3
import asyncio
import logging
import os
import shutil
import tempfile
import uuid
import random
from pathlib import Path
from flask import Flask
import threading

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from yt_dlp import YoutubeDL

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
TELEGRAM_FILE_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GB
COOKIES_FILE = os.path.join(os.getcwd(), "cookies.txt")  # Persistent storage in Render
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]  # Your Telegram user ID
LINK_STORE: dict[str, str] = {}

# Funny responses with emojis for non-admins
FUNNY_RESPONSES = [
    "🤖 *BEEP BOOP* Sorry, I only take orders from my creators!",
    "🦸‍♂️ Nice try! But you're not one of the chosen ones!",
    "👀 Oops! Did you say something? I wasn't listening...",
    "🔒 *ACCESS DENIED* This command is for VIPs only!",
    "🧙‍♂️ Abracadabra! Poof! This command disappeared!",
    "🤫 Shhh... this is a secret command for special agents!",
    "🛑 Hold up! You need the magic password for this one!",
    "👾 Error 404: Admin privileges not found!",
    "🕵️‍♂️ This command is classified top secret!",
    "🎩 My circuits detect you're not wearing an admin hat!",
    "🚨 Alert! Unauthorized command attempt detected!",
    "🧐 Interesting... but no, just no.",
    "🤐 My lips are sealed about admin commands!",
    "💂‍♂️ The guards won't let you pass this point!",
    "🦹‍♂️ Villains can't use admin commands!",
    "🧌 This bridge is only for admin trolls!",
    "👽 This command is from another admin-only galaxy!",
    "🏰 The castle gates are closed for non-admins!",
    "🗝️ You need a golden key for this command!",
    "🤷‍♂️ I'd tell you, but then I'd have to... nope!"
]

# Flask app for health checks
app = Flask(__name__)

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

@app.route('/')
def health_check():
    return "Video Downloader Bot is Running", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

def has_cookies() -> bool:
    """Check if cookies file exists and is not empty"""
    return Path(COOKIES_FILE).exists() and os.path.getsize(COOKIES_FILE) > 0

def get_formats(url: str):
    """Extract available formats using yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "cookiefile": COOKIES_FILE if has_cookies() else None,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Error getting formats: {str(e)}")
        raise

def download_format(url: str, fmt: str, out_path: Path):
    """Download the selected format."""
    out_tpl = str(out_path) + ".%(ext)s"
    ydl_opts = {
        "quiet": True,
        "outtmpl": out_tpl,
        "format": f"{fmt}+bestaudio/best",
        "merge_output_format": "mp4",
        "cookiefile": COOKIES_FILE if has_cookies() else None,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        for p in out_path.parent.iterdir():
            if p.stem == out_path.name:
                return p
        raise FileNotFoundError("Downloaded file not found")
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        raise

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n"
        "Send me a video link (YouTube, TikTok, Instagram, etc.)\n"
        "I'll show available resolutions and download your choice!",
        parse_mode=constants.ParseMode.MARKDOWN,
    )

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin help if user is admin, otherwise show funny response"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(random.choice(FUNNY_RESPONSES), parse_mode=constants.ParseMode.MARKDOWN)
        return

    help_text = (
        "🛠 *Admin Commands*\n\n"
        "/upload_cookies - Upload cookies.txt file\n"
        "/remove_cookies - Remove existing cookies\n"
        "/cookies_status - Check cookies status\n\n"
        "🔒 These commands are only available to admins"
    )
    await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)

async def upload_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cookies upload (admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(random.choice(FUNNY_RESPONSES), parse_mode=constants.ParseMode.MARKDOWN)
        return

    await update.message.reply_text(
        "📁 Please upload your cookies.txt file for YouTube authentication.\n"
        "This will be used for age-restricted or private content.",
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def remove_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cookies removal (admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(random.choice(FUNNY_RESPONSES), parse_mode=constants.ParseMode.MARKDOWN)
        return

    if has_cookies():
        try:
            os.remove(COOKIES_FILE)
            await update.message.reply_text("✅ Cookies file has been removed")
        except Exception as e:
            await update.message.reply_text(f"❌ Error removing cookies: {str(e)}")
    else:
        await update.message.reply_text("ℹ️ No cookies file exists to remove")

async def cookies_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cookies status check (admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(random.choice(FUNNY_RESPONSES), parse_mode=constants.ParseMode.MARKDOWN)
        return

    if has_cookies():
        await update.message.reply_text(
            f"✅ Cookies are enabled\n📏 Size: {os.path.getsize(COOKIES_FILE)} bytes",
            parse_mode=constants.ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("❌ Cookies are disabled")

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when a document is sent (for cookies.txt)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    message = update.effective_message
    document = message.document

    if not document.file_name.lower() == "cookies.txt":
        await message.reply_text("⚠️ Please upload a file named 'cookies.txt'")
        return

    try:
        # Download the file directly to the persistent location
        file = await document.get_file()
        await file.download_to_drive(COOKIES_FILE)
        await message.reply_text(
            "✅ Cookies file saved successfully!\n"
            "It will be used for all YouTube downloads.",
            parse_mode=constants.ParseMode.MARKDOWN
        )
    except Exception as e:
        await message.reply_text(f"❌ Error saving cookies: {str(e)}")

async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    url = message.text.strip()

    if not url.lower().startswith(("http://", "https://")):
        await message.reply_text("❌ Please send a valid URL starting with http:// or https://")
        return

    msg = await message.reply_text("🔍 Analyzing video...")

    try:
        info = await asyncio.to_thread(get_formats, url)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{str(e)}`", parse_mode="Markdown")
        return

    video_title = info.get("title") or "video"
    formats = info.get("formats") or []

    buttons = []
    seen_labels = set()
    for f in sorted(formats, key=lambda x: (x.get("height") or 0), reverse=True):
        if f.get("vcodec") == "none":
            continue
        height = f.get("height") or 0
        if height == 0:
            continue
        label = f"{height}p"
        if label in seen_labels:
            continue
        seen_labels.add(label)

        fmt_id = f["format_id"]
        token = uuid.uuid4().hex[:10]
        LINK_STORE[token] = url
        cb_data = f"{token}:{fmt_id}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=cb_data)])

    if not buttons:
        await msg.edit_text("❌ No downloadable formats found")
        return

    keyboard = InlineKeyboardMarkup(buttons)
    await msg.edit_text(
        f"🎬 *{video_title}*\nSelect resolution:",
        parse_mode=constants.ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        token, fmt_id = query.data.split(":")
        url = LINK_STORE.pop(token)
    except Exception:
        await query.edit_message_text("⚠️ Expired. Send the link again.")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="dl_"))
    temp_base = temp_dir / "video"

    await query.edit_message_text("⬇️ Downloading...")

    try:
        file_path = await asyncio.to_thread(download_format, url, fmt_id, temp_base)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.edit_message_text(f"❌ Download failed: `{str(e)}`", parse_mode="Markdown")
        return

    if file_path.stat().st_size > TELEGRAM_FILE_LIMIT:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.edit_message_text("⚠️ File exceeds 2GB limit. Try lower resolution.")
        return

    await query.edit_message_text("📤 Uploading to Telegram...")
    try:
        await query.message.reply_video(video=file_path.open("rb"))
    except Exception as e:
        await query.edit_message_text(f"❌ Upload failed: `{str(e)}`", parse_mode="Markdown")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.delete_message()

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN environment variable missing!")
    if not ADMIN_IDS:
        raise SystemExit("❌ ADMIN_IDS environment variable missing! Set your Telegram user ID")

    # Start Flask server in background
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start Telegram bot
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Command handlers
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("admin_help", admin_help))
    bot_app.add_handler(CommandHandler("upload_cookies", upload_cookies))
    bot_app.add_handler(CommandHandler("remove_cookies", remove_cookies))
    bot_app.add_handler(CommandHandler("cookies_status", cookies_status))
    
    # Message handlers
    bot_app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))
    
    # Callback handler
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting...")
    bot_app.run_polling()

if __name__ == "__main__":
    main()