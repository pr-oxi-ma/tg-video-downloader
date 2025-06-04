#!/usr/bin/env python3
import asyncio
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
COOKIES_FILE = os.path.join(os.getcwd(), "cookies.txt")  # Persistent storage in Render
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]  # Your Telegram user ID
LINK_STORE: dict[str, str] = {}

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
        "I'll show available resolutions and download your choice!\n\n"
        f"Cookies status: {'✅ Enabled' if has_cookies() else '❌ Disabled'}",
        parse_mode=constants.ParseMode.MARKDOWN,
    )

async def upload_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /uploadcookies command (admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ This command is only available for bot admins")
        return

    await update.message.reply_text(
        "📁 Please upload your cookies.txt file. "
        "This will be used for all YouTube downloads.\n\n"
        "*Note:* This will replace any existing cookies file.",
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def remove_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /removecookies command (admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ This command is only available for bot admins")
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
    """Handler for /cookiesstatus command"""
    status_text = "🔍 *Cookies Status*\n\n"
    if has_cookies():
        status_text += "✅ Cookies are enabled\n"
        status_text += f"📄 File: {COOKIES_FILE}\n"
        status_text += f"📏 Size: {os.path.getsize(COOKIES_FILE)} bytes\n"
    else:
        status_text += "❌ Cookies are disabled\n"
    
    status_text += "\nFor YouTube, cookies are recommended for age-restricted or private content."
    
    await update.message.reply_text(
        status_text,
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when a document is sent (for cookies.txt)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Only bot admins can upload cookies")
        return

    message = update.effective_message
    document = message.document

    if not document.file_name.lower() == "cookies.txt":
        await message.reply_text("❌ Please upload a file named 'cookies.txt'")
        return

    try:
        # Download the file directly to the persistent location
        file = await document.get_file()
        await file.download_to_drive(COOKIES_FILE)
        await message.reply_text(
            "✅ Cookies file saved! It will be used for all YouTube downloads."
        )
    except Exception as e:
        await message.reply_text(f"❌ Error saving cookies: {str(e)}")

async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    url = message.text.strip()

    if not url.lower().startswith(("http://", "https://")):
        await message.reply_text("❌ Please send a valid URL starting with http:// or https://")
        return

    # Check if we're using cookies (for YouTube)
    cookies_note = ""
    if "youtube.com" in url.lower() and has_cookies():
        cookies_note = "\n\nℹ️ Using cookies for YouTube access"

    msg = await message.reply_text(f"🔍 Analyzing video...{cookies_note}")

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
        f"*{video_title}*\nSelect resolution:",
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
    bot_app.add_handler(CommandHandler("uploadcookies", upload_cookies))
    bot_app.add_handler(CommandHandler("removecookies", remove_cookies))
    bot_app.add_handler(CommandHandler("cookiesstatus", cookies_status))
    
    # Message handlers
    bot_app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))
    
    # Callback handler
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting...")
    bot_app.run_polling()

if __name__ == "__main__":
    main()
