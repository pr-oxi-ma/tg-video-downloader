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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ParseMode
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
    CallbackContext,
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

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 *Video Downloader Bot*\n"
        "Send me a video link (YouTube, TikTok, Instagram, etc.)\n"
        "I'll show available resolutions and download your choice!",
        parse_mode=ParseMode.MARKDOWN,
    )

def admin_help(update: Update, context: CallbackContext):
    """Show admin help if user is admin, otherwise show funny response"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text(random.choice(FUNNY_RESPONSES), parse_mode=ParseMode.MARKDOWN)
        return

    help_text = (
        "🛠 *Admin Commands*\n\n"
        "*/upload_cookies* - Upload cookies.txt file\n"
        "*/remove_cookies* - Remove existing cookies\n"
        "*/cookies_status* - Check cookies status\n\n"
        "🔒 *These commands are only available to admins*"
    )
    update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

def upload_cookies(update: Update, context: CallbackContext):
    """Handle cookies upload (admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text(random.choice(FUNNY_RESPONSES), parse_mode=ParseMode.MARKDOWN)
        return

    update.message.reply_text(
        "📁 Please upload your cookies.txt file for YouTube authentication.\n"
        "This will be used for age-restricted or private content.",
        parse_mode=ParseMode.MARKDOWN
    )

def remove_cookies(update: Update, context: CallbackContext):
    """Handle cookies removal (admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text(random.choice(FUNNY_RESPONSES), parse_mode=ParseMode.MARKDOWN)
        return

    if has_cookies():
        try:
            os.remove(COOKIES_FILE)
            update.message.reply_text("✅ Cookies file has been removed")
        except Exception as e:
            update.message.reply_text(f"❌ Error removing cookies: {str(e)}")
    else:
        update.message.reply_text("ℹ️ No cookies file exists to remove")

def cookies_status(update: Update, context: CallbackContext):
    """Handle cookies status check (admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text(random.choice(FUNNY_RESPONSES), parse_mode=ParseMode.MARKDOWN)
        return

    if has_cookies():
        update.message.reply_text(
            f"✅ Cookies are enabled\n📏 Size: {os.path.getsize(COOKIES_FILE)} bytes",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        update.message.reply_text("❌ Cookies are disabled")

def document_handler(update: Update, context: CallbackContext):
    """Handle when a document is sent (for cookies.txt)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    message = update.effective_message
    document = message.document

    if not document.file_name.lower() == "cookies.txt":
        message.reply_text("⚠️ Please upload a file named 'cookies.txt'")
        return

    try:
        # Download the file directly to the persistent location
        file = document.get_file()
        file.download(COOKIES_FILE)
        message.reply_text(
            "✅ Cookies file saved successfully!\n"
            "It will be used for all YouTube downloads.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        message.reply_text(f"❌ Error saving cookies: {str(e)}")

def link_handler(update: Update, context: CallbackContext):
    message = update.effective_message
    url = message.text.strip()

    if not url.lower().startswith(("http://", "https://")):
        message.reply_text("❌ Please send a valid URL starting with http:// or https://")
        return

    msg = message.reply_text("🔍 Analyzing video...")

    try:
        info = get_formats(url)
    except Exception as e:
        msg.edit_text(f"❌ Error: `{str(e)}`", parse_mode="Markdown")
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
        msg.edit_text("❌ No downloadable formats found")
        return

    keyboard = InlineKeyboardMarkup(buttons)
    msg.edit_text(
        f"🎬 *{video_title}*\nSelect resolution:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    try:
        token, fmt_id = query.data.split(":")
        url = LINK_STORE.pop(token)
    except Exception:
        query.edit_message_text("⚠️ Expired. Send the link again.")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="dl_"))
    temp_base = temp_dir / "video"

    query.edit_message_text("⬇️ Downloading...")

    try:
        file_path = download_format(url, fmt_id, temp_base)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        query.edit_message_text(f"❌ Download failed: `{str(e)}`", parse_mode="Markdown")
        return

    if file_path.stat().st_size > TELEGRAM_FILE_LIMIT:
        shutil.rmtree(temp_dir, ignore_errors=True)
        query.edit_message_text("⚠️ File exceeds 2GB limit. Try lower resolution.")
        return

    query.edit_message_text("📤 Uploading to Telegram...")
    try:
        query.message.reply_video(video=open(file_path, "rb"))
    except Exception as e:
        query.edit_message_text(f"❌ Upload failed: `{str(e)}`", parse_mode="Markdown")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        query.delete_message()

def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Update {update} caused error {context.error}")

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
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Command handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("admin", admin_help))
    dp.add_handler(CommandHandler("upload_cookies", upload_cookies))
    dp.add_handler(CommandHandler("remove_cookies", remove_cookies))
    dp.add_handler(CommandHandler("cookies_status", cookies_status))
    
    # Message handlers
    dp.add_handler(MessageHandler(Filters.document, document_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, link_handler))
    
    # Callback handler
    dp.add_handler(CallbackQueryHandler(button_handler))
    
    # Error handler
    dp.add_error_handler(error_handler)

    logger.info("Bot starting...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
