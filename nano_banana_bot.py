import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
import requests
import os
import base64
from io import BytesIO

# --- Ключи берутся из переменных окружения на Railway ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

# --- Константы для OpenRouter ---
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
# Модель для анализа фото и создания промпта
GEMINI_FLASH_MODEL = "google/gemini-flash-1.5"
# Модель для генерации изображения по промпту (из документации)
IMAGE_GEN_MODEL = "google/gemini-2.5-flash-image-preview"

# --- Состояния для диалога ---
PHOTO, HAIRSTYLE = range(2)

# --- Словарь с прическами ---
HAIRSTYLES = {
    "Кудри 💇‍♀️": "long curly hair",
    "Каре 👩‍🦰": "bob cut",
    "Короткая стрижка 💇‍♂️": "short pixie cut",
    "Длинные прямые 👱‍♀️": "long straight hair",
    "Цветные волосы 🌈": "rainbow colored hair"
}

# --- Вспомогательная функция для кодирования изображения в Base64 ---
def encode_image_to_base64(image_bytes):
    """Кодирует байты изображения в строку Base64."""
    return base64.b64encode(image_bytes).decode('utf-8')

# --- Функция /start ---
async def start(update: Update, context) -> int:
    """Начинает диалог и просит пользователя загрузить фото."""
    await update.message.reply_text(
        "👋 Привет! Я могу помочь тебе 'примерить' новую прическу с помощью AI.\n\n"
        "Пожалуйста, загрузи свое селфи (как фото, а не как документ)."
    )
    return PHOTO

# --- Функция для получения фото ---
async def get_photo(update: Update, context) -> int:
    """Сохраняет фото в памяти и предлагает выбрать прическу."""
    photo_file = await update.message.photo[-1].get_file()

    photo_bytes_io = BytesIO()
    await photo_file.download_to_memory(photo_bytes_io)
    photo_bytes_io.seek(0)
    context.user_data['photo_bytes'] = photo_bytes_io.read()

    keyboard = [
        [InlineKeyboardButton(key, callback_data=value)]
        for key, value in HAIRSTYLES.items()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Отлично! Теперь выбери прическу:", reply_markup=reply_markup)
    return HAIRSTYLE

# --- Функция для генерации изображения с использованием OpenRouter ---
async def generate_image_with_openrouter(update: Update, context) -> int:
    """Получает выбор прически, генерирует изображение и отправляет пользователю."""
    query = update.callback_query
    await query.answer()

    hairstyle_prompt = query.data
    photo_bytes = context.user_data.get('photo_bytes')

    if not photo_bytes:
        await query.edit_message_text(text="😔 Фото не найдено. Пожалуйста, начните заново с команды /start.")
        return ConversationHandler.END

    await query.edit_message_text(text="⏳ Анализирую ваше фото, чтобы создать уникальный запрос для AI...")

    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }

        # --- Фаза 1: Анализ фото и генерация промпта с Gemini Flash ---
        base64_image = encode_image_to_base64(photo_bytes)
        payload_gemini = {
            "model": GEMINI_FLASH_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Create a short, detailed, photorealistic prompt for an image generation model. The goal is to reimagine the person in the photo with '{hairstyle_prompt}'. Describe their key facial features, gender, and approximate age based on the photo. The final image should be a high-quality, realistic portrait. Start the prompt with 'photorealistic portrait of a person...'. Do not mention the original photo."
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                        }
                    ]
                }
            ]
        }

        response_gemini = requests.post(
            f"{OPENROUTER_API_BASE}/chat/completions",
            headers=headers,
            json=payload_gemini,
            timeout=30
        )
        response_gemini.raise_for_status()
        analysis_result = response_gemini.json()
        generated_prompt = analysis_result['choices'][0]['message']['content'].strip()

        await query.edit_message_text(text=f"✅ Запрос создан! Генерирую новый образ... Это может занять минуту.")

        # --- Фаза 2: Генерация изображения по созданному промпту (ИСПРАВЛЕНО) ---
        payload_image_gen = {
            "model": IMAGE_GEN_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": generated_prompt
                }
            ],
            "modalities": ["image", "text"] # <-- Ключевое изменение согласно документации
        }

        response_image_gen = requests.post(
            f"{OPENROUTER_API_BASE}/chat/completions", # <-- Правильный URL
            headers=headers,
            json=payload_image_gen,
            timeout=120
        )
        response_image_gen.raise_for_status()

        image_result = response_image_gen.json()

        # Парсим ответ согласно новой документации
        message = image_result.get("choices")[0].get("message")
        if message and message.get("images"):
            # Получаем Base64 URL из ответа
            image_url = message["images"][0]["image_url"]["url"]

            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=image_url, # Telegram API может обработать base64 data URL
                caption="Готово! Как тебе такой образ?"
            )
            await query.message.delete()
        else:
            raise ValueError("В ответе API не найдено сгенерированное изображение.")

    except requests.exceptions.RequestException as e:
        error_message = f"😔 Ошибка при связи с API. Возможно, модель занята или ваш баланс на OpenRouter исчерпан.\n\nДетали: {e}"
        print(error_message)
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_message)
    except Exception as e:
        error_message = f"😔 Ой, что-то пошло не так. Попробуйте еще раз с другим фото.\n\nДетали: {e}"
        print(error_message)
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_message)

    return ConversationHandler.END


# --- Функция для отмены ---
async def cancel(update: Update, context) -> int:
    """Отменяет текущий диалог."""
    await update.message.reply_text('Действие отменено. Чтобы начать заново, отправьте /start.')
    context.user_data.clear()
    return ConversationHandler.END


# --- Основная функция для запуска бота ---
def main() -> None:
    """Запуск бота."""
    if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
        print("Ошибка: Отсутствуют переменные окружения TELEGRAM_TOKEN или OPENROUTER_API_KEY.")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
            HAIRSTYLE: [CallbackQueryHandler(generate_image_with_openrouter)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=600
    )

    application.add_handler(conv_handler)

    print("Бот запущен и готов к работе...")
    application.run_polling()


if __name__ == '__main__':
    main()
