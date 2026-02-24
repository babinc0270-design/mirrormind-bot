import os
import sqlite3
from flask import Flask, request
import logging
import asyncio
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from google import genai
from google.genai import types


# ==============================
# CONFIG
# ==============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Set TELEGRAM_TOKEN and GEMINI_API_KEY environment variables.")

client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(level=logging.INFO)


# ==============================
# DATABASE
# ==============================

conn = sqlite3.connect("mirrormind.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS messages (
    user_id INTEGER,
    text TEXT,
    mood_score INTEGER,
    timestamp DATETIME
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    language TEXT
)
""")

conn.commit()


# ==============================
# LANGUAGE SYSTEM
# ==============================

LANGUAGE_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🇬🇧 English"],
        ["🇮🇳 Hindi"],
        ["🇮🇳 Bengali"],
        ["🇮🇳 Hinglish"],
        ["🇮🇳 Bengalish"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

def normalize_language(text):
    if "Hinglish" in text:
        return "Hinglish"
    elif "Bengalish" in text:
        return "Bengalish"
    elif "English" in text:
        return "English"
    elif "Hindi" in text:
        return "Hindi"
    elif "Bengali" in text:
        return "Bengali"
    return None

def language_instruction(lang):
    mapping = {
        "English": "Respond in English.",
        "Hindi": "Respond in Hindi using Devanagari script.",
        "Bengali": "Respond in Bengali script.",
        "Hinglish": "Respond in Hinglish (Hindi written in English letters).",
        "Bengalish": "Respond in Bengalish (Bengali written in English letters).",
    }
    return mapping.get(lang, "Respond in English.")


# ==============================
# GEMINI GENERATOR
# ==============================

def generate_ai(contents):
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents
        )
        return response.text
    except Exception as e:
        logging.error(f"Gemini Error: {e}")
        return None


# ==============================
# COMMANDS
# ==============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    cursor.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()

    if not result:
        await update.message.reply_text(
            "Select your language:",
            reply_markup=LANGUAGE_KEYBOARD
        )
    else:
        await update.message.reply_text("Hi. Tell me what's on your mind.")


async def language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Select language:",
        reply_markup=LANGUAGE_KEYBOARD
    )


# ==============================
# TEXT HANDLER
# ==============================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    lang_select = normalize_language(text)
    if lang_select:
        cursor.execute("""
        INSERT INTO users(user_id, language)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET language=excluded.language
        """, (user_id, lang_select))
        conn.commit()

        await update.message.reply_text(f"Language set to {lang_select} ✅")
        return

    cursor.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()

    if not result:
        await update.message.reply_text(
            "Please select language first.",
            reply_markup=LANGUAGE_KEYBOARD
        )
        return

    lang = result[0]

    prompt = f"""
You are MirrorMind Pro.
{language_instruction(lang)}

Tone:
- Warm
- Emotionally intelligent
- Supportive

Respond in 4–6 sentences.
"""

    ai_response = generate_ai([prompt, text])

    if not ai_response:
        await update.message.reply_text("Error generating response.")
        return

    cursor.execute("""
    INSERT INTO messages(user_id, text, mood_score, timestamp)
    VALUES (?, ?, ?, ?)
    """, (user_id, text, None, datetime.now()))
    conn.commit()

    await update.message.reply_text(ai_response)


# ==============================
# PHOTO HANDLER
# ==============================


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    photo = update.message.photo[-1]

    file = await photo.get_file()
    file_bytes = await file.download_as_bytearray()

    cursor.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    lang = result[0] if result else "English"

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                f"{language_instruction(lang)} Describe this image emotionally.",
                types.Part.from_bytes(
                    data=file_bytes,
                    mime_type="image/jpeg",
                ),
            ],
        )

        await update.message.reply_text(response.text)

    except Exception as e:
        logging.error(e)
        await update.message.reply_text("Couldn't analyze image.")


# ==============================
# SMART AUDIO HANDLER
# ==============================

async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    media = update.message.voice or update.message.audio

    file = await media.get_file()
    file_bytes = await file.download_as_bytearray()

    mime_type = media.mime_type or "audio/ogg"

    cursor.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    lang = result[0] if result else "English"

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                f"{language_instruction(lang)} Transcribe this audio and respond emotionally.",
                types.Part.from_bytes(
                    data=file_bytes,
                    mime_type=mime_type,
                ),
            ],
        )

        await update.message.reply_text(response.text)

    except Exception as e:
        logging.error(e)
        await update.message.reply_text("Couldn't process audio.")


# ==============================
# VIDEO HANDLER
# ==============================

async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Video analysis currently limited. Coming soon 🔥"
    )


# ==============================
# MAIN
# ==============================

if __name__ == "__main__":

    PORT = int(os.environ.get("PORT", 10000))
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, audio_handler))
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))

    async def main():
        global application

        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("language", language_cmd))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
        application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
        application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, audio_handler))
        application.add_handler(MessageHandler(filters.VIDEO, video_handler))

        await application.initialize()
        await application.start()
        await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

        await asyncio.Event().wait()
        asyncio.run(main())


    flask_app = Flask(__name__)

    @flask_app.route("/")
    def home():
        return "MirrorMind Bot Running"

        @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run(application.process_update(update))
        return "OK"
    if __name__ == "__main__":
        import threading

        PORT = int(os.environ.get("PORT", 10000))

        threading.Thread(target=lambda: asyncio.run(main())).start()

        flask_app.run(host="0.0.0.0", port=PORT)
