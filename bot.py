import os
import logging
import subprocess
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(','))) if os.getenv("ADMIN_IDS") else []
OUTPUT_DIR = "downloads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit

# Conversation states
SELECTING_ACTION, PROCESSING_URL, PROCESSING_FILE, SELECTING_QUALITY = range(4)

def get_resolution_choices():
    return {
        "144": "144p",
        "240": "240p",
        "360": "360p",
        "480": "480p",
        "720": "720p (HD)",
        "1080": "1080p (Full HD)",
        "1440": "1440p (2K)",
        "2160": "2160p (4K)",
        "best": "Best available",
        "audio": "Audio only"
    }

def create_quality_keyboard():
    choices = get_resolution_choices()
    keyboard = []
    keys = list(choices.keys())
    for i in range(0, len(keys), 2):
        row = []
        if i < len(keys):
            row.append(InlineKeyboardButton(choices[keys[i]], callback_data=keys[i]))
        if i+1 < len(keys):
            row.append(InlineKeyboardButton(choices[keys[i+1]], callback_data=keys[i+1]))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Send welcome message and prompt for action selection."""
    user = update.effective_user
    if ADMIN_IDS and user.id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Sorry, this bot is private.")
        return ConversationHandler.END
        
    await update.message.reply_text(
        f"Hi {user.first_name}! I can download videos for you.\n\n"
        "Choose an option:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Single Video", callback_data="single")],
            [InlineKeyboardButton("Multiple Videos (from file)", callback_data="multiple")],
        ]),
    )
    return SELECTING_ACTION

async def select_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle action selection."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "single":
        await query.edit_message_text("Send me the video URL:")
        return PROCESSING_URL
    elif query.data == "multiple":
        await query.edit_message_text("Upload a text file with URLs (one per line):")
        return PROCESSING_FILE

async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process single URL input."""
    url = update.message.text.strip()
    if not url.startswith(('http://', 'https://')):
        await update.message.reply_text("Please provide a valid URL starting with http:// or https://")
        return PROCESSING_URL
    
    context.user_data['url'] = url
    await update.message.reply_text(
        "Select video quality:",
        reply_markup=create_quality_keyboard()
    )
    return SELECTING_QUALITY

async def process_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process uploaded file with URLs."""
    if not update.message.document:
        await update.message.reply_text("Please upload a text file.")
        return PROCESSING_FILE
    
    file = await context.bot.get_file(update.message.document)
    file_path = os.path.join(OUTPUT_DIR, f"urls_{update.effective_user.id}.txt")
    await file.download_to_drive(file_path)
    
    try:
        with open(file_path, 'r') as f:
            urls = [line.strip() for line in f if line.strip()]
    except Exception as e:
        await update.message.reply_text(f"Error reading file: {e}")
        return ConversationHandler.END
    
    if not urls:
        await update.message.reply_text("No valid URLs found in the file.")
        return ConversationHandler.END
    
    context.user_data['urls'] = urls
    await update.message.reply_text(
        f"Found {len(urls)} URLs. Select quality for all downloads:",
        reply_markup=create_quality_keyboard()
    )
    return SELECTING_QUALITY

async def download_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the download process."""
    query = update.callback_query
    await query.answer()
    
    quality = query.data
    context.user_data['quality'] = quality
    
    if 'url' in context.user_data:
        # Single download
        url = context.user_data['url']
        await query.edit_message_text(f"⏳ Downloading at {get_resolution_choices()[quality]} quality...")
        success = await perform_download(url, quality, update, context)
        
        if success:
            await query.edit_message_text("✅ Download completed!")
        else:
            await query.edit_message_text("❌ Download failed. Please try again.")
            
    elif 'urls' in context.user_data:
        # Multiple downloads
        urls = context.user_data['urls']
        await query.edit_message_text(f"⏳ Starting {len(urls)} downloads...")
        
        for i, url in enumerate(urls, 1):
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Downloading {i}/{len(urls)}..."
            )
            success = await perform_download(url, quality, update, context, message)
            
            if not success:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Failed to download: {url}"
                )
        
        await query.edit_message_text(f"✅ Completed {len(urls)} downloads!")
    
    return ConversationHandler.END

async def perform_download(url: str, quality: str, update: Update, context: ContextTypes.DEFAULT_TYPE, message=None) -> bool:
    """Perform the actual download using yt-dlp."""
    try:
        Path(OUTPUT_DIR).mkdir(exist_ok=True)
        
        # Base command with cookies if available
        base_cmd = ["yt-dlp"]
        if os.getenv("COOKIES_BASE64"):
            cookies_path = os.path.join(OUTPUT_DIR, "cookies.txt")
            with open(cookies_path, 'wb') as f:
                import base64
                f.write(base64.b64decode(os.getenv("COOKIES_BASE64")))
            base_cmd.extend(["--cookies", cookies_path])
        
        if quality == "audio":
            cmd = base_cmd + [
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "-o", f"{OUTPUT_DIR}/%(title)s.%(ext)s",
                url
            ]
        elif quality == "best":
            cmd = base_cmd + [
                "-f", "bestvideo+bestaudio",
                "--merge-output-format", "mp4",
                "-o", f"{OUTPUT_DIR}/%(title)s.%(ext)s",
                url
            ]
        else:
            cmd = base_cmd + [
                "-f", f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]",
                "--merge-output-format", "mp4",
                "-o", f"{OUTPUT_DIR}/%(title)s.%(ext)s",
                url
            ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        before = set(os.listdir(OUTPUT_DIR))
        await process.communicate()
        after = set(os.listdir(OUTPUT_DIR))
        new_files = after - before
        
        if process.returncode == 0 and new_files:
            downloaded_file = new_files.pop()
            file_path = os.path.join(OUTPUT_DIR, downloaded_file)
            
            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"⚠️ File too large for Telegram ({file_size/1024/1024:.1f}MB). "
                         "Use /start to download smaller versions."
                )
                return True
            
            try:
                if message:
                    await message.delete()
                
                with open(file_path, 'rb') as f:
                    if quality == "audio":
                        await context.bot.send_audio(
                            chat_id=update.effective_chat.id,
                            audio=f,
                            title=downloaded_file
                        )
                    else:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=f,
                            supports_streaming=True
                        )
                return True
            except Exception as e:
                logger.error(f"Error sending file: {e}")
                return True
        else:
            logger.error(f"Download failed for {url}")
            return False
            
    except Exception as e:
        logger.error(f"Error in download process: {e}")
        return False

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel and end the conversation."""
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and send a message to the user."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if update.effective_message:
        await update.effective_message.reply_text("⚠️ An error occurred. Please try again.")

def main() -> None:
    """Run the bot."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set!")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_ACTION: [CallbackQueryHandler(select_action)],
            PROCESSING_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_url)],
            PROCESSING_FILE: [MessageHandler(filters.Document.TXT, process_file)],
            SELECTING_QUALITY: [CallbackQueryHandler(download_media)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)
    
    logger.info("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())