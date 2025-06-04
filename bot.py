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
import resource

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
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TELEGRAM_FILE_LIMIT = 2 * 1024 * 1024 * 1024  # 2GB
COOKIES_FILE = os.path.join(tempfile.gettempdir(), "cookies.txt")
COOKIES_BASE64 = os.getenv("COOKIES_BASE64", "")
LINK_STORE: dict[str, str] = {}

# Flask app for health checks
app = Flask(__name__)

# Enhanced logging
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
    """Create cookies.txt from base64 environment variable with validation."""
    try:
        logger.info(f"Initializing cookies (COOKIES_BASE64 length: {len(COOKIES_BASE64) if COOKIES_BASE64 else 0})")
        
        if COOKIES_BASE64:
            try:
                decoded = base64.b64decode(COOKIES_BASE64)
                logger.info(f"Decoded cookie size: {len(decoded)} bytes")
                
                cookie_dir = Path(COOKIES_FILE).parent
                cookie_dir.mkdir(parents=True, exist_ok=True)
                
                with open(COOKIES_FILE, "wb") as f:
                    f.write(decoded)
                os.chmod(COOKIES_FILE, 0o600)
                
                # Verify creation
                if Path(COOKIES_FILE).exists():
                    content = Path(COOKIES_FILE).read_text()
                    if "youtube.com" in content or "instagram.com" in content:
                        logger.info(f"Valid cookies file created at {COOKIES_FILE}")
                    else:
                        logger.warning("Cookies file created but doesn't contain expected domains")
                else:
                    logger.error("Failed to create cookies file")
            except Exception as decode_error:
                logger.error(f"Cookie decode error: {str(decode_error)}")
        else:
            logger.warning("No COOKIES_BASE64 provided - some sites may block downloads")
    except Exception as e:
        logger.error(f"Cookie setup failed: {str(e)}", exc_info=True)

def cleanup_temp_files():
    """Clean up residual temporary files"""
    temp_dir = Path(tempfile.gettempdir())
    for item in temp_dir.glob("dl_*"):
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            logger.info(f"Cleaned up temp item: {item}")
        except Exception as e:
            logger.warning(f"Failed to clean {item}: {str(e)}")

def get_ydl_opts():
    """Generate yt-dlp options with cookie handling"""
    opts = {
        "quiet": True,
        "no_warnings": False,
        "extract_flat": False,
        "merge_output_format": "mp4",
    }
    
    if Path(COOKIES_FILE).exists():
        opts["cookiefile"] = COOKIES_FILE
        logger.info("Using cookies file for download")
    else:
        logger.warning("No cookies file available - downloads may be limited")
    
    return opts

def get_formats(url: str):
    """Extract available formats with enhanced error handling"""
    ydl_opts = get_ydl_opts()
    ydl_opts["skip_download"] = True
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Validate formats
            if not info.get('formats'):
                raise ValueError("No formats available - possibly blocked by cookies")
                
            return info
    except Exception as e:
        logger.error(f"Format extraction failed for {url}: {str(e)}")
        raise

def download_format(url: str, fmt: str, out_path: Path):
    """Download with resource monitoring"""
    ydl_opts = get_ydl_opts()
    ydl_opts.update({
        "format": f"{fmt}+bestaudio/best",
        "outtmpl": str(out_path) + ".%(ext)s",
    })
    
    try:
        # Monitor memory usage
        mem_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        mem_end = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        logger.info(f"Download memory usage: {(mem_end - mem_start)/1024:.2f} MB increase")
        
        # Find downloaded file
        for p in out_path.parent.iterdir():
            if p.stem == out_path.name:
                return p
                
        raise FileNotFoundError("Downloaded file not found")
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        raise

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced start command with bot info"""
    help_text = """
🎥 *Video Downloader Bot*

🔹 Send me a link from:
- YouTube (including age-restricted)
- Instagram
- TikTok
- Twitter/X
- 1000+ other sites

🔹 I'll show available resolutions
🔹 Max file size: 2GB

📌 *Privacy*: Your cookies are only used for the download and never stored.
"""
    await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)

async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming links with better validation"""
    url = update.effective_message.text.strip()
    
    if not url.lower().startswith(("http://", "https://")):
        await update.message.reply_text("❌ Please send a valid URL starting with http:// or https://")
        return
    
    msg = await update.message.reply_text("🔍 Analyzing video...")
    
    try:
        info = await asyncio.to_thread(get_formats, url)
        
        # Extract available formats
        video_title = info.get('title', 'Untitled')[:100] + ("..." if len(info.get('title', '')) > 100 else "")
        formats = [f for f in info.get('formats', []) if f.get('vcodec') != 'none']
        
        if not formats:
            await msg.edit_text("❌ No downloadable video formats found")
            return
            
        # Create unique buttons for each resolution
        buttons = []
        seen_resolutions = set()
        
        for f in sorted(formats, key=lambda x: x.get('height', 0), reverse=True):
            res = f.get('height', 0)
            if res > 0 and res not in seen_resolutions:
                seen_resolutions.add(res)
                token = uuid.uuid4().hex[:10]
                LINK_STORE[token] = url
                buttons.append(
                    [InlineKeyboardButton(
                        text=f"{res}p ({f.get('ext', 'mp4')})",
                        callback_data=f"{token}:{f['format_id']}"
                    )]
                )
                
        if not buttons:
            await msg.edit_text("❌ No valid resolutions found")
            return
            
        keyboard = InlineKeyboardMarkup(buttons)
        await msg.edit_text(
            f"📹 *{video_title}*\nSelect resolution:",
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Link handling error: {str(e)}")
        await msg.edit_text(f"❌ Error: {str(e)}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle download requests with progress updates"""
    query = update.callback_query
    await query.answer()
    
    try:
        token, fmt_id = query.data.split(":")
        url = LINK_STORE.pop(token)
    except Exception:
        await query.edit_message_text("⚠️ Session expired. Please send the link again.")
        return
    
    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="dl_"))
    temp_file = temp_dir / "video"
    
    try:
        # Download
        await query.edit_message_text("⬇️ Downloading (this may take a while)...")
        file_path = await asyncio.to_thread(download_format, url, fmt_id, temp_file)
        
        # Size check
        file_size = file_path.stat().st_size
        if file_size > TELEGRAM_FILE_LIMIT:
            raise ValueError(f"File too large ({file_size/1024/1024:.2f}MB > 2GB limit)")
        
        # Upload
        await query.edit_message_text("📤 Uploading to Telegram...")
        await query.message.reply_video(
            video=open(file_path, 'rb'),
            supports_streaming=True,
            filename=file_path.name
        )
        
        await query.delete_message()
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        await query.edit_message_text(f"❌ Download failed: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

async def test_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to verify cookies work"""
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Age-restricted test
    
    try:
        info = await asyncio.to_thread(get_formats, test_url)
        if info.get('age_limit', 0) > 0:
            await update.message.reply_text("✅ Cookies working (accessed age-restricted content)")
        else:
            await update.message.reply_text("⚠️ Cookies loaded but couldn't verify age-restricted access")
    except Exception as e:
        await update.message.reply_text(f"❌ Cookie test failed: {str(e)}")

def main():
    """Main application setup"""
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN environment variable required!")
    
    # Initial setup
    cleanup_temp_files()
    setup_cookies_file()
    
    # Start health check server
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Configure bot
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("testcookies", test_cookies))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Bot starting with configuration:")
    logger.info(f"Cookies enabled: {Path(COOKIES_FILE).exists()}")
    logger.info(f"Temp directory: {tempfile.gettempdir()}")
    
    bot_app.run_polling()

if __name__ == "__main__":
    main()
