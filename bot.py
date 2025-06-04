#!/usr/bin/env python3
# Updated with secure cookie handling

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
COOKIES_ENV_VAR = "COOKIES_TXT_BASE64"  # Render environment variable name

LINK_STORE: dict[str, str] = {}

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def init_cookies():
    """Initialize cookies.txt from environment variable"""
    if os.path.exists(COOKIES_FILE):
        logger.info("Using existing cookies.txt")
        return True
        
    cookies_b64 = os.getenv(COOKIES_ENV_VAR)
    if not cookies_b64:
        logger.warning("No cookies provided - some videos may not download")
        return False

    try:
        cookies_data = base64.b64decode(cookies_b64).decode('utf-8')
        with open(COOKIES_FILE, 'w') as f:
            f.write(cookies_data)
        logger.info("Created cookies.txt from environment variable")
        return True
    except Exception as e:
        logger.error(f"Failed to create cookies.txt: {str(e)}")
        return False

# Initialize cookies at startup
COOKIES_AVAILABLE = init_cookies()

def get_ydl_opts():
    """Generate yt-dlp options with cookies if available"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    if COOKIES_AVAILABLE:
        opts.update({
            "cookiefile": COOKIES_FILE,
            "mark_watched": False,
            "geo_bypass": True,
        })
    return opts

async def get_formats(url: str):
    """Get available formats with cookie support"""
    ydl_opts = get_ydl_opts()
    ydl_opts["skip_download"] = True
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            return info
    except Exception as e:
        logger.error(f"Extraction failed: {str(e)}")
        raise

async def download_format(url: str, fmt: str, out_path: Path):
    """Download with cookie support"""
    out_tpl = str(out_path) + ".%(ext)s"
    ydl_opts = get_ydl_opts()
    ydl_opts.update({
        "outtmpl": out_tpl,
        "format": f"{fmt}+bestaudio/best",
        "merge_output_format": "mp4",
    })

    try:
        with YoutubeDL(ydl_opts) as ydl:
            await asyncio.to_thread(ydl.download, [url])
        
        for p in out_path.parent.iterdir():
            if p.stem == out_path.name:
                return p
        raise FileNotFoundError("Downloaded file not found")
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        raise

# [Rest of your original handlers (start, link_handler, button_handler) remain unchanged]
# ...

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable not set")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting with cookie support: %s", COOKIES_AVAILABLE)
    app.run_polling()

if __name__ == "__main__":
    main()
