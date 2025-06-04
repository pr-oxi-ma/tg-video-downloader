#!/usr/bin/env python3
import asyncio
import base64
import logging
import os
import shutil
import tempfile
import uuid
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
DEFAULT_COOKIES_FILE = "/tmp/cookies.txt"  # Default cookies file path
COOKIES_BASE64 = os.getenv("COOKIES_BASE64") or ""
LINK_STORE: dict[str, dict] = {}  # Stores both URL and optional cookies path
USER_COOKIES: dict[int, str] = {}  # Stores user-specific cookies files

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

def setup_default_cookies_file():
    """Create default cookies.txt from base64 environment variable."""
    try:
        if COOKIES_BASE64:
            Path(DEFAULT_COOKIES_FILE).parent.mkdir(parents=True, exist_ok=True)
            with open(DEFAULT_COOKIES_FILE, "wb") as f:
                f.write(base64.b64decode(COOKIES_BASE64))
            logger.info(f"Created default cookies file at {DEFAULT_COOKIES_FILE}")
        elif not Path(DEFAULT_COOKIES_FILE).exists():
            logger.warning("No default cookies.txt file found and COOKIES_BASE64 not provided")
    except Exception as e:
        logger.error(f"Failed to setup default cookies: {str(e)}")

def get_cookies_for_user(user_id: int) -> str | None:
    """Get cookies file path for a specific user, or default if available."""
    if user_id in USER_COOKIES:
        return USER_COOKIES[user_id]
    if Path(DEFAULT_COOKIES_FILE).exists():
        return DEFAULT_COOKIES_FILE
    return None

def get_formats(url: str, cookies_file: str | None = None):
    """Extract available formats using yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "cookiefile": cookies_file if cookies_file and Path(cookies_file).exists() else None,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Error getting formats: {str(e)}")
        raise

def download_format(url: str, fmt: str, out_path: Path, cookies_file: str | None = None):
    """Download the selected format."""
    out_tpl = str(out_path) + ".%(ext)s"
    ydl_opts = {
        "quiet": True,
        "outtmpl": out_tpl,
        "format": f"{fmt}+bestaudio/best",
        "merge_output_format": "mp4",
        "cookiefile": cookies_file if cookies_file and Path(cookies_file).exists() else None,
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
    user = update.effective_user
    text = (
        "👋 *Video Downloader Bot*\n"
        "Send me a video link (YouTube, TikTok, Instagram, etc.)\n\n"
        "For YouTube, you can upload your *cookies.txt* file first "
        "(from your browser) to access age-restricted/private content.\n\n"
        "Commands:\n"
        "/setcookies - Upload your cookies.txt file\n"
        "/removecookies - Remove your stored cookies\n"
        "/status - Show current cookies status"
    )
    
    await update.message.reply_text(
        text,
        parse_mode=constants.ParseMode.MARKDOWN,
    )

async def set_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /setcookies command"""
    await update.message.reply_text(
        "📁 Please upload your cookies.txt file. "
        "This will be used for your future YouTube downloads.\n\n"
        "*Note:* Your cookies will be stored securely and only used for your requests.",
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def remove_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /removecookies command"""
    user_id = update.effective_user.id
    if user_id in USER_COOKIES:
        try:
            os.remove(USER_COOKIES[user_id])
            del USER_COOKIES[user_id]
            await update.message.reply_text("✅ Your cookies have been removed.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error removing cookies: {str(e)}")
    else:
        await update.message.reply_text("ℹ️ You don't have any cookies stored.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /status command"""
    user_id = update.effective_user.id
    has_user_cookies = user_id in USER_COOKIES
    has_default_cookies = Path(DEFAULT_COOKIES_FILE).exists()
    
    text = "🔍 *Current Cookies Status*\n\n"
    if has_user_cookies:
        text += "✅ You have your own cookies file set\n"
    else:
        text += "ℹ️ You don't have your own cookies file set\n"
    
    if has_default_cookies:
        text += "\nℹ️ A default cookies file is available (will be used if you don't have your own)"
    else:
        text += "\nℹ️ No default cookies file is available"
    
    text += "\n\nFor YouTube, cookies are recommended for age-restricted or private content."
    
    await update.message.reply_text(
        text,
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when a document is sent (for cookies.txt)"""
    message = update.effective_message
    user_id = message.from_user.id
    document = message.document

    if not document.file_name.lower() == "cookies.txt":
        await message.reply_text("❌ Please upload a file named 'cookies.txt'")
        return

    # Create user-specific directory for cookies
    user_dir = Path(f"/tmp/user_{user_id}")
    user_dir.mkdir(exist_ok=True)
    cookies_path = user_dir / "cookies.txt"

    # Download the file
    try:
        file = await document.get_file()
        await file.download_to_drive(cookies_path)
        USER_COOKIES[user_id] = str(cookies_path)
        await message.reply_text(
            "✅ Cookies file saved! It will be used for your YouTube downloads.\n"
            "You can now send video links."
        )
    except Exception as e:
        await message.reply_text(f"❌ Error saving cookies: {str(e)}")

async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user_id = message.from_user.id
    url = message.text.strip()

    if not url.lower().startswith(("http://", "https://")):
        await message.reply_text("❌ Please send a valid URL starting with http:// or https://")
        return

    # Get user's cookies file if available
    cookies_file = get_cookies_for_user(user_id)
    if cookies_file and "youtube.com" in url.lower():
        cookies_note = "\n\nℹ️ Using your cookies for YouTube access"
    else:
        cookies_note = ""

    msg = await message.reply_text(f"🔍 Analyzing video...{cookies_note}")

    try:
        info = await asyncio.to_thread(get_formats, url, cookies_file)
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
        LINK_STORE[token] = {"url": url, "cookies": cookies_file}
        cb_data = f"{token}:{fmt_id}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=cb_data)])

    if not buttons:
        await msg.edit_text("❌ No downloadable formats found")
        return

    keyboard = InlineKeyboardMarkup(buttons)
    await msg.edit_text(
        f"*{video_title}*\nSelect resolution:",
        parse_mode=constants.ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        token, fmt_id = query.data.split(":")
        link_info = LINK_STORE.pop(token)
        url = link_info["url"]
        cookies_file = link_info["cookies"]
    except Exception:
        await query.edit_message_text("⚠️ Expired. Send the link again.")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="dl_"))
    temp_base = temp_dir / "video"

    await query.edit_message_text("⬇️ Downloading...")

    try:
        file_path = await asyncio.to_thread(
            download_format, url, fmt_id, temp_base, cookies_file
        )
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

    setup_default_cookies_file()

    # Start Flask server in background
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start Telegram bot
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Command handlers
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("setcookies", set_cookies))
    bot_app.add_handler(CommandHandler("removecookies", remove_cookies))
    bot_app.add_handler(CommandHandler("status", status))
    
    # Message handlers
    bot_app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))
    
    # Callback handler
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting...")
    bot_app.run_polling()

if __name__ == "__main__":
    main()