import logging
import os
from datetime import datetime
import json
import speech_recognition as sr
from pydub import AudioSegment
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from dateparser import parse as date_parse
from pathlib import Path
import sqlite3
import asyncio

class Config:
    TOKEN = os.getenv('TOKEN', '8106464780:AAHsDB8gDtrG9Ls_LKGVCgjCGoNDPOj-fpo')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    TEMP_DIR = Path("temp")
    DB_PATH = Path("bot_data.db")

class ReminderExtractor:
    def __init__(self):
        self.reminder_triggers = {
            'remind me to',
            'set a reminder for',
            'remember to',
            'don\'t forget to',
            'remind me about',
            'remind me to do',
            'class',
            'assignment',
            'submission',
            'meeting',
            'deadline',
            'reminder',
            'important',
            'urgent',
            'money',
            'attention'
        }
    
    def extract_reminders_from_text(self, text: str) -> list[tuple[str, str]]:
        """Extract reminder text and associated dates from transcribed text."""
        reminders = []
        sentences = text.lower().split('.')
        
        for sentence in sentences:
            for trigger in self.reminder_triggers:
                if trigger in sentence:
                    # Extract the part after the trigger
                    reminder_text = sentence.split(trigger)[1].strip()
                    
                    # Look for date patterns
                    date_matches = date_parse(reminder_text, settings={
                        'PREFER_DATES_FROM': 'future',
                        'RELATIVE_BASE': datetime.now()
                    })
                    
                    if date_matches:
                        # Remove the date part from reminder text
                        date_str = date_matches.isoformat()
                        reminder_text = reminder_text.replace(str(date_matches), '').strip()
                        reminders.append((reminder_text, date_str))
                    
        return reminders

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, Config.LOG_LEVEL),
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

async def setup_database():
    """Setup in-memory SQLite database."""
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reminder_text TEXT,
            reminder_date TEXT,
            created_at TEXT
        )
    ''')
    
    conn.commit()
    return conn

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "Hi! I'm your Voice Reminder Bot. I can:\n"
        "1. Create reminders from voice messages\n"
        "2. Set reminders using commands\n"
        "3. Manage your reminders\n\n"
        "Try sending a voice message saying 'remind me to call mom tomorrow'!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "🤖 Available commands:\n\n"
        "Voice Messages:\n"
        "• Send a voice message saying something like:\n"
        "  'remind me to call mom tomorrow'\n"
        "  'set a reminder for meeting at 3pm'\n\n"
        "Commands:\n"
        "• /add_reminder <date> <message>\n"
        "• /remove_reminder <id>\n"
        "• /reminders - list all reminders\n\n"
        "Example:\n"
        "/add_reminder tomorrow Submit assignment"
    )

async def process_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process voice messages and extract reminders."""
    status_message = await update.message.reply_text("Processing your voice message...")
    
    try:
        # Download voice message
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        voice_path = Config.TEMP_DIR / f"{update.message.voice.file_id}.ogg"
        Config.TEMP_DIR.mkdir(exist_ok=True)
        await voice_file.download_to_drive(str(voice_path))
        
        # Convert to WAV
        wav_path = voice_path.with_suffix('.wav')
        AudioSegment.from_ogg(str(voice_path)).export(str(wav_path), format="wav")
        
        # Transcribe
        recognizer = sr.Recognizer()
        with sr.AudioFile(str(wav_path)) as source:
            audio = recognizer.record(source)
            text = recognizer.recognize_google(audio)
        
        # Extract reminders
        reminder_extractor = ReminderExtractor()
        reminders = reminder_extractor.extract_reminders_from_text(text)
        
        response = ["📝 Transcription:", text]
        
        if reminders:
            response.append("\n🔔 Creating reminders:")
            cursor = context.application.bot_data['db_conn'].cursor()
            
            for reminder_text, reminder_date in reminders:
                # Insert reminder into database
                cursor.execute('''
                    INSERT INTO reminders 
                    (user_id, reminder_text, reminder_date, created_at)
                    VALUES (?, ?, ?, ?)
                ''', (
                    update.effective_user.id,
                    reminder_text,
                    reminder_date,
                    datetime.now().isoformat()
                ))
                
                parsed_date = datetime.fromisoformat(reminder_date)
                response.append(
                    f"✅ Set reminder: {reminder_text} for "
                    f"{parsed_date.strftime('%Y-%m-%d %H:%M')}"
                )
            
            context.application.bot_data['db_conn'].commit()
        else:
            response.append("\n❌ No reminders found in the message")
        
        await status_message.edit_text("\n".join(response))
        
        # Cleanup
        voice_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        
    except Exception as e:
        logger.error(f"Error processing voice message: {str(e)}", exc_info=True)
        await status_message.edit_text("Sorry, an error occurred while processing your voice message.")

async def add_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a reminder."""
    try:
        if len(context.args) < 2:
            await update.message.reply_text(
                "Please use the format: /add_reminder <date> <message>\n"
                "Examples:\n"
                "/add_reminder tomorrow Submit assignment\n"
                "/add_reminder 'next monday' Team meeting"
            )
            return

        date_str = context.args[0]
        message = ' '.join(context.args[1:])
        parsed_date = date_parse(date_str)
        
        if not parsed_date:
            await update.message.reply_text("❌ Could not understand the date format. Please try again.")
            return
        
        cursor = context.application.bot_data['db_conn'].cursor()
        cursor.execute('''
            INSERT INTO reminders (user_id, reminder_text, reminder_date, created_at)
            VALUES (?, ?, ?, ?)
        ''', (
            update.effective_user.id,
            message,
            parsed_date.isoformat(),
            datetime.now().isoformat()
        ))
        context.application.bot_data['db_conn'].commit()
        
        await update.message.reply_text(
            f"✅ Reminder set for {parsed_date.strftime('%Y-%m-%d %H:%M')}:\n{message}"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error setting reminder: {str(e)}")


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Check for due reminders."""
    try:
        cursor = context.application.bot_data['db_conn'].cursor()
        cursor.execute('''
            SELECT id, user_id, reminder_text, reminder_date
            FROM reminders
            WHERE reminder_date <= ?
        ''', (datetime.now().isoformat(),))
        
        for reminder_id, user_id, text, date in cursor.fetchall():
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⏰ Reminder: {text}\nSet for: {date}"
                )
                cursor.execute('DELETE FROM reminders WHERE id = ?', (reminder_id,))
                context.application.bot_data['db_conn'].commit()
            except Exception as e:
                logger.error(f"Error sending reminder {reminder_id}: {str(e)}")
                
    except Exception as e:
        logger.error(f"Error checking reminders: {str(e)}")

async def remove_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a reminder."""
    try:
        if not context.args:
            await update.message.reply_text("Please provide the reminder ID to remove.\nExample: /remove_reminder 1")
            return
        
        reminder_id = int(context.args[0])
        cursor = context.application.bot_data['db_conn'].cursor()
        
        cursor.execute(
            'SELECT * FROM reminders WHERE id = ? AND user_id = ?',
            (reminder_id, update.effective_user.id)
        )
        
        if cursor.fetchone():
            cursor.execute('DELETE FROM reminders WHERE id = ?', (reminder_id,))
            context.application.bot_data['db_conn'].commit()
            await update.message.reply_text(f"✅ Reminder #{reminder_id} removed successfully!")
        else:
            await update.message.reply_text("❌ Reminder not found or you don't have permission to remove it.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error removing reminder: {str(e)}")

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all reminders."""
    try:
        cursor = context.application.bot_data['db_conn'].cursor()
        cursor.execute('''
            SELECT id, reminder_text, reminder_date 
            FROM reminders 
            WHERE user_id = ?
            ORDER BY reminder_date
        ''', (update.effective_user.id,))
        
        reminders = cursor.fetchall()
        
        if not reminders:
            await update.message.reply_text("You don't have any reminders set.")
            return
        
        response = ["📋 Your Reminders:\n"]
        for reminder_id, text, date in reminders:
            response.append(f"ID #{reminder_id}: {date} - {text}")
        
        response.append("\nTo remove a reminder, use /remove_reminder <id>")
        await update.message.reply_text("\n".join(response))
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error listing reminders: {str(e)}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Error handling update: {context.error}", exc_info=context.error)
    try:
        if update and update.effective_message:
            await update.message.reply_text(
                "Sorry, an error occurred while processing your request."
            )
    except Exception as e:
        logger.error(f"Error in error handler: {str(e)}")

async def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token
    application = Application.builder().token(Config.TOKEN).build()

    # Setup database and store connection in bot_data
    application.bot_data['db_conn'] = await setup_database()

    # Add job to check reminders every minute
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=60)

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add_reminder", add_reminder))
    application.add_handler(CommandHandler("remove_reminder", remove_reminder))
    application.add_handler(CommandHandler("reminders", list_reminders))
    application.add_handler(MessageHandler(filters.VOICE, process_voice))
    
    # Add error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # Run the async main() function
    asyncio.run(main())