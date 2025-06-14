import os
import logging
import tempfile
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    CallbackQueryHandler,
)
import yt_dlp
from functools import wraps

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
COOKIES_FILE = 'cookies.txt'

# Initialize cookies from environment variable if available
if os.getenv('COOKIES_BASE64'):
    try:
        with open(COOKIES_FILE, 'wb') as f:
            f.write(base64.b64decode(os.getenv('COOKIES_BASE64')))
        logging.info("Cookies initialized from environment variable")
    except Exception as e:
        logging.error(f"Failed to initialize cookies: {e}")

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Helper functions
def restricted_to_admins(func):
    """Decorator to restrict access to admins only"""
    @wraps(func)
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            update.message.reply_text("⚠️ This command is restricted to admins only.")
            return
        return func(update, context, *args, **kwargs)
    return wrapped

def get_video_resolutions(url: str) -> list:
    """Get available video resolutions using yt-dlp"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []
            
            formats = info.get('formats', [])
            if not formats:
                if info.get('url'):
                    return [{'format_id': 'best', 'height': 'Best available'}]
                return []
            
            resolutions = {}
            for f in formats:
                if f.get('height'):
                    res = f['height']
                    if res not in resolutions:
                        resolutions[res] = f['format_id']
            
            available_res = [{'height': h, 'format_id': resolutions[h]} for h in sorted(resolutions.keys(), reverse=True)]
            return available_res
            
        except Exception as e:
            logger.error(f"Error getting resolutions: {e}")
            return []

def download_video(url: str, format_id: str = 'best') -> str:
    """Download video using yt-dlp with specified format"""
    temp_dir = tempfile.mkdtemp()
    output_path = os.path.join(temp_dir, '%(title)s.%(ext)s')
    
    ydl_opts = {
        'format': format_id,
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
    }
    
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info)
            return downloaded_file
    except Exception as e:
        logger.error(f"Error downloading video: {e}")
        raise

# Command handlers
def start(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /start is issued."""
    update.message.reply_text(
        "👋 Hi! Send me a link to a video from supported social media platforms "
        "(YouTube, Instagram, Twitter, TikTok, etc.) and I'll download it for you."
    )

def help_command(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /help is issued."""
    update.message.reply_text(
        "📝 How to use this bot:\n\n"
        "1. Send me a link to a video from supported platforms\n"
        "2. I'll show you available resolutions (if any)\n"
        "3. Select your preferred quality\n"
        "4. I'll download and send you the video\n\n"
        "Supported platforms include: YouTube, Instagram, Twitter, TikTok, Facebook, and many more."
    )

@restricted_to_admins
def admin_help(update: Update, context: CallbackContext) -> None:
    """Send admin help message"""
    update.message.reply_text(
        "🛠 Admin Commands:\n\n"
        "/upload_cookies - Upload cookies.txt file\n"
        "/delete_cookies - Delete current cookies file\n"
        "/cookies_status - Check if cookies file exists\n\n"
        "Note: Cookies help with age-restricted or private content."
    )

@restricted_to_admins
def upload_cookies(update: Update, context: CallbackContext) -> None:
    """Handle cookies file upload"""
    if not update.message.document:
        update.message.reply_text("Please upload the cookies.txt file as a document.")
        return
    
    file = context.bot.get_file(update.message.document.file_id)
    file.download(COOKIES_FILE)
    update.message.reply_text("✅ Cookies file uploaded successfully.")

@restricted_to_admins
def delete_cookies(update: Update, context: CallbackContext) -> None:
    """Delete cookies file"""
    if os.path.exists(COOKIES_FILE):
        os.remove(COOKIES_FILE)
        update.message.reply_text("✅ Cookies file deleted successfully.")
    else:
        update.message.reply_text("No cookies file exists.")

@restricted_to_admins
def cookies_status(update: Update, context: CallbackContext) -> None:
    """Check cookies file status"""
    if os.path.exists(COOKIES_FILE):
        file_size = os.path.getsize(COOKIES_FILE)
        update.message.reply_text(f"✅ Cookies file exists ({file_size} bytes).")
    else:
        update.message.reply_text("❌ No cookies file found.")

def handle_video_url(update: Update, context: CallbackContext) -> None:
    """Handle video URL message"""
    url = update.message.text
    
    if not (url.startswith('http://') or url.startswith('https://')):
        update.message.reply_text("Please send a valid URL starting with http:// or https://")
        return
    
    update.message.reply_text("🔍 Checking available resolutions...")
    
    resolutions = get_video_resolutions(url)
    if not resolutions:
        update.message.reply_text("⚠️ Could not get video information. The link might be invalid or the site might not be supported.")
        return
    
    keyboard = []
    for res in resolutions:
        height = res['height']
        format_id = res['format_id']
        if height == 'Best available':
            text = "⬇️ Download (Best Quality)"
        else:
            text = f"⬇️ Download ({height}p)"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"download_{format_id}_{url}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("📊 Available resolutions:", reply_markup=reply_markup)

def button_callback(update: Update, context: CallbackContext) -> None:
    """Handle button callbacks"""
    query = update.callback_query
    query.answer()
    
    if query.data.startswith('download_'):
        _, format_id, url = query.data.split('_', 2)
        url = url.replace('_', '/')
        
        query.edit_message_text(text=f"⏳ Downloading video (format: {format_id})...")
        
        try:
            video_path = download_video(url, format_id)
            
            with open(video_path, 'rb') as video_file:
                context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=video_file,
                    caption="Here's your downloaded video!",
                    supports_streaming=True
                )
            
            os.remove(video_path)
            os.rmdir(os.path.dirname(video_path))
            
        except Exception as e:
            logger.error(f"Error in download: {e}")
            query.edit_message_text(text="❌ Failed to download the video. Please try again later.")

def error_handler(update: Update, context: CallbackContext) -> None:
    """Log errors"""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    
    if update and update.effective_message:
        update.effective_message.reply_text(
            "⚠️ An error occurred while processing your request. Please try again later."
        )

def main() -> None:
    """Start the bot."""
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher

    # Register command handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("admin", admin_help))
    dispatcher.add_handler(CommandHandler("upload_cookies", upload_cookies))
    dispatcher.add_handler(CommandHandler("delete_cookies", delete_cookies))
    dispatcher.add_handler(CommandHandler("cookies_status", cookies_status))

    # Register message handlers
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_video_url))
    
    # Register callback query handler
    dispatcher.add_handler(CallbackQueryHandler(button_callback))

    # Register error handler
    dispatcher.add_error_handler(error_handler)

    # Start the Bot
    updater.start_polling()
    logger.info("Bot started and polling...")
    updater.idle()

if __name__ == '__main__':
    main()