#!/usr/bin/env python3
# universal_downloader_bot.py

import asyncio
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

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
TELEGRAM_FILE_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GB

LINK_STORE: dict[str, str] = {}

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_formats(url: str):
    """Extract formats using yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_format(url: str, fmt: str, out_path: Path):
    """Download selected format using yt-dlp."""
    out_tpl = str(out_path) + ".%(ext)s"
    ydl_opts = {
        "quiet": True,
        "outtmpl": out_tpl,
        "format": f"{fmt}+bestaudio/best",
        "merge_output_format": "mp4",
    }
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    for p in out_path.parent.iterdir():
        if p.stem == out_path.name:
            return p
    raise FileNotFoundError("Download succeeded but file not found!")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Social Media Video Downloader*\n"
        "Just send me any public video link (YouTube, TikTok, Insta, …).\n"
        "I'll list every available resolution – pick one and I'll send it!",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    url = message.text.strip()

    if not url.lower().startswith(("http://", "https://")):
        await message.reply_text("❌ Please send a direct video URL.")
        return

    msg = await message.reply_text("🔍 Extracting formats, please wait…")

    try:
        info = await asyncio.to_thread(get_formats, url)
    except Exception as e:
        await msg.edit_text(f"❌ Extraction failed:\n`{e}`", parse_mode="Markdown")
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
        await msg.edit_text("❌ No downloadable video formats found.")
        return

    keyboard = InlineKeyboardMarkup(buttons)
    await msg.edit_text(
        f"*{video_title}*\nSelect a resolution:",
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
        await query.edit_message_text("⚠️ This button is no longer valid. Send the link again.")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="yt_"))
    temp_base = temp_dir / "video"

    await query.edit_message_text("⬇️ Downloading your video…")

    try:
        file_path = await asyncio.to_thread(download_format, url, fmt_id, temp_base)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.edit_message_text(f"❌ Download error:\n`{e}`", parse_mode="Markdown")
        return

    if file_path.stat().st_size > TELEGRAM_FILE_LIMIT:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.edit_message_text(
            "⚠️ This file is larger than Telegram's 2 GB limit. Try a lower resolution."
        )
        return

    await query.edit_message_text("📤 Uploading…")
    try:
        await query.message.reply_video(video=file_path.open("rb"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await query.delete_message()


def main():
    if not BOT_TOKEN or BOT_TOKEN.startswith("PASTE_"):
        raise SystemExit("❌ BOT_TOKEN is not set!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started…")
    app.run_polling()


if __name__ == "__main__":
    main()
