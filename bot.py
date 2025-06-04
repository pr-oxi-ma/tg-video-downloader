#!/usr/bin/env python3
# universal_downloader_bot.py - Fixed version with proper cookie handling

import asyncio
import base64
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

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
COOKIES_FILE = "cookies.txt"
LINK_STORE: dict[str, str] = {}

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def ensure_cookies_file():
    """Create and validate cookies.txt from environment variable."""
    if os.path.exists(COOKIES_FILE):
        logger.info("Using existing cookies.txt")
        return True
    
    cookies_content = os.getenv("COOKIES_TXT_CONTENT")
    if not cookies_content:
        logger.warning("No cookies.txt or COOKIES_TXT_CONTENT env var found")
        return False

    try:
        # Decode base64 content
        decoded = base64.b64decode(cookies_content).decode("utf-8")
        
        # Validate YouTube cookies
        if ".youtube.com" not in decoded:
            logger.error("Invalid cookies.txt: Missing YouTube domains")
            return False
            
        with open(COOKIES_FILE, "w") as f:
            f.write(decoded)
        logger.info("Created valid cookies.txt from environment variable")
        return True
    except Exception as e:
        logger.error(f"Failed to create cookies.txt: {e}")
        return False

# Initialize cookies at startup
HAS_COOKIES = ensure_cookies_file()

def get_ydl_opts():
    """Shared yt-dlp options with cookie handling."""
    opts = {
        "quiet": True,
        "extract_flat": False,
        "mark_watched": False,
        "no_warnings": False,
    }
    
    if HAS_COOKIES:
        opts.update({
            "cookiefile": COOKIES_FILE,
            "cookiesfrombrowser": ("chrome",)  # Fallback for Chrome/Firefox
        })
    
    return opts

def get_formats(url: str):
    """Extract available formats with proper cookie handling."""
    ydl_opts = get_ydl_opts()
    ydl_opts["skip_download"] = True
    
    with YoutubeDL(ydl_opts) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            raise

def download_format(url: str, fmt: str, out_path: Path):
    """Download video with proper cookie handling."""
    out_tpl = str(out_path) + ".%(ext)s"
    ydl_opts = get_ydl_opts()
    ydl_opts.update({
        "outtmpl": out_tpl,
        "format": f"{fmt}+bestaudio/best",
        "merge_output_format": "mp4",
    })
    
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    for p in out_path.parent.iterdir():
        if p.stem == out_path.name:
            return p
    raise FileNotFoundError("Download succeeded but file not found")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Social Media Video Downloader*\n"
        "Send me a video link (YouTube/TikTok/Insta) and I'll download it!",
        parse_mode=constants.ParseMode.MARKDOWN,
    )

async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    url = message.text.strip()

    if not url.lower().startswith(("http://", "https://")):
        await message.reply_text("❌ Please send a valid URL starting with http:// or https://")
        return

    msg = await message.reply_text("🔍 Extracting formats...")

    try:
        info = await asyncio.to_thread(get_formats, url)
    except Exception as e:
        error_msg = f"❌ Failed to process URL:\n`{str(e)[:200]}`"  # Truncate long errors
        await msg.edit_text(error_msg, parse_mode="Markdown")
        return

    video_title = info.get("title", "video")
    formats = info.get("formats", [])

    # Filter and sort available video formats
    buttons = []
    seen_resolutions = set()
    for f in sorted(formats, key=lambda x: (x.get("height") or 0), reverse=True):
        if f.get("vcodec") == "none":
            continue  # Skip audio-only formats
            
        height = f.get("height", 0)
        if height == 0:
            continue
            
        resolution = f"{height}p"
        if resolution in seen_resolutions:
            continue
            
        seen_resolutions.add(resolution)
        fmt_id = f["format_id"]
        token = uuid.uuid4().hex[:10]
        LINK_STORE[token] = url
        buttons.append([InlineKeyboardButton(resolution, callback_data=f"{token}:{fmt_id}")])

    if not buttons:
        await msg.edit_text("❌ No downloadable video formats found")
        return

    await msg.edit_text(
        f"*{video_title}*\nSelect resolution:",
        parse_mode=constants.ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        token, fmt_id = query.data.split(":")
        url = LINK_STORE.pop(token)
    except Exception:
        await query.edit_message_text("⚠️ Expired request. Please send the link again.")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="yt_"))
    temp_base = temp_dir / "video"

    await query.edit_message_text("⬇️ Downloading...")

    try:
        file_path = await asyncio.to_thread(download_format, url, fmt_id, temp_base)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.edit_message_text(f"❌ Download failed:\n`{str(e)[:200]}`", parse_mode="Markdown")
        return

    if file_path.stat().st_size > TELEGRAM_FILE_LIMIT:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.edit_message_text("⚠️ File exceeds Telegram's 2GB limit. Try lower resolution.")
        return

    await query.edit_message_text("📤 Uploading...")
    try:
        await query.message.reply_video(
            video=open(file_path, "rb"),
            supports_streaming=True
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Upload failed:\n`{str(e)[:200]}`", parse_mode="Markdown")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.delete_message()

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN environment variable missing!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started with %scookies", "" if HAS_COOKIES else "NO ")
    app.run_polling()

if __name__ == "__main__":
    main()
