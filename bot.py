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
COOKIES_FILE = "cookies.txt"  # Changed to local directory
COOKIES_BASE64 = os.getenv("COOKIES_BASE64") or ""
LINK_STORE: dict[str, str] = {}
MAX_RETRIES = 3

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

def setup_cookies_file():
    """Create cookies.txt from base64 environment variable."""
    try:
        if COOKIES_BASE64:
            cookies_data = base64.b64decode(COOKIES_BASE64).decode('utf-8')
            if not cookies_data.strip():
                logger.warning("Cookies data is empty!")
                return False
            
            with open(COOKIES_FILE, "w") as f:
                f.write(cookies_data)
            logger.info(f"Cookies file created at {Path(COOKIES_FILE).absolute()}")
            return True
        else:
            logger.warning("No COOKIES_BASE64 provided - some content may require login")
            return False
    except Exception as e:
        logger.error(f"Failed to setup cookies: {str(e)}")
        return False

def get_formats(url: str):
    """Extract available formats using yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise ValueError("No video info found")
                return info
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                raise
            logger.warning(f"Attempt {attempt + 1} failed, retrying... Error: {str(e)}")
            time.sleep(1)

def download_format(url: str, fmt: str, out_path: Path):
    """Download the selected format."""
    out_tpl = str(out_path) + ".%(ext)s"
    ydl_opts = {
        "quiet": True,
        "outtmpl": out_tpl,
        "format": f"{fmt}+bestaudio/best",
        "merge_output_format": "mp4",
        "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
        "retries": MAX_RETRIES,
        "fragment_retries": MAX_RETRIES,
        "extractor_retries": MAX_RETRIES,
    }
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        for ext in ['mp4', 'mkv', 'webm']:
            possible_file = out_path.with_suffix(f'.{ext}')
            if possible_file.exists():
                return possible_file
        raise FileNotFoundError("Downloaded file not found")
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        raise

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n"
        "Send me a video link (YouTube, TikTok, Instagram, etc.)\n"
        "I'll show available resolutions and download your choice!\n\n"
        "⚠️ Note: Some videos may require cookies for age-restricted content",
        parse_mode=constants.ParseMode.MARKDOWN,
    )

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
        await msg.edit_text(f"❌ Error analyzing video:\n`{str(e)}`\n\nTry again or check if the URL is correct.", parse_mode="Markdown")
        return

    video_title = info.get("title") or "video"
    formats = info.get("formats") or []

    buttons = []
    seen_labels = set()
    
    # Add best quality option first
    token = uuid.uuid4().hex[:10]
    LINK_STORE[token] = url
    buttons.append([InlineKeyboardButton(text="✨ Best Quality", callback_data=f"{token}:best")])
    
    # Add other resolutions
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

    if len(buttons) == 1:  # Only "Best Quality" button
        buttons.append([InlineKeyboardButton(text="Default Quality", callback_data=f"{token}:default")])

    keyboard = InlineKeyboardMarkup(buttons)
    await msg.edit_text(
        f"📹 *{video_title[:100]}*...\nSelect quality:",
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

    await query.edit_message_text("⬇️ Downloading... (This may take a while)")

    try:
        file_path = await asyncio.to_thread(download_format, url, fmt_id, temp_base)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.edit_message_text(
            f"❌ Download failed:\n`{str(e)}`\n\n"
            "Possible reasons:\n"
            "- Video is too long\n"
            "- Private/age-restricted content\n"
            "- Server issues\n"
            "Try a different quality or link.",
            parse_mode="Markdown"
        )
        return

    file_size = file_path.stat().st_size
    if file_size > TELEGRAM_FILE_LIMIT:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.edit_message_text(
            f"⚠️ File too large ({file_size/1024/1024:.1f}MB > {TELEGRAM_FILE_LIMIT/1024/1024:.1f}MB limit).\n"
            "Try a lower resolution or use a shorter video."
        )
        return

    await query.edit_message_text(f"📤 Uploading ({file_size/1024/1024:.1f}MB)...")
    try:
        await query.message.reply_video(
            video=file_path.open("rb"),
            supports_streaming=True,
            caption=f"Downloaded from {url}",
            read_timeout=300,
            write_timeout=300,
            connect_timeout=300
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Upload failed:\n`{str(e)}`", parse_mode="Markdown")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.delete_message()

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN environment variable missing!")

    if not setup_cookies_file():
        logger.warning("Running without cookies - some content may not be available")

    # Start Flask server in background
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start Telegram bot
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting...")
    bot_app.run_polling()

if __name__ == "__main__":
    main()
