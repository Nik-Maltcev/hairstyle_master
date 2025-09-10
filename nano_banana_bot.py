import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
import requests
import os
import base64
from io import BytesIO

# --- Ключи берутся из переменных окружения на Railway ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
SEGMIND_API_KEY = os.getenv('SEGMIND_API_KEY') # <-- ИЗМЕНЕНИЕ: Новый ключ

# --- Константы для Segmind ---
SEGMIND_API_URL = "https://api.segmind.com/v1/nano-banana"

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
    """Получает фото, создает для него публичную ссылку и предлагает выбрать прическу."""
    photo_file = await update.message.photo[-1].get_file()

    # Создаем временную публичную ссылку на файл через API Telegram
    public_photo_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{photo_file.file_path}"
    context.user_data['photo_url'] = public_photo_url

    # Создаем кнопки с прическами
    keyboard = [
        [InlineKeyboardButton(key, callback_data=value)]
        for key, value in HAIRSTYLES.items()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Отлично! Фото принято. Теперь выбери прическу:", reply_markup=reply_markup)
    return HAIRSTYLE

# --- Функция для генерации изображения с использованием Segmind ---
async def generate_image_with_segmind(update: Update, context) -> int:
    """Получает выбор прически, генерирует изображение и отправляет пользователю."""
    query = update.callback_query
    await query.answer()

    hairstyle_prompt = query.data
    photo_url = context.user_data.get('photo_url')

    if not photo_url:
        await query.edit_message_text(text="😔 Фото не найдено. Пожалуйста, начните заново с команды /start.")
        return ConversationHandler.END

    await query.edit_message_text(text="⏳ Отправляю запрос в Segmind AI... Это может занять около минуты.")

    try:
        # Проверяем, что у нас есть API ключ
        if not SEGMIND_API_KEY:
            await context.bot.send_message(
                chat_id=query.message.chat_id, 
                text="😔 Ошибка конфигурации: отсутствует API ключ Segmind."
            )
            return ConversationHandler.END
        
        print(f"Using photo URL: {photo_url}")
        print(f"Using prompt: A photorealistic portrait of a person with beautiful {hairstyle_prompt}, high detail, 8k")
        
        # Формируем заголовки и тело запроса для Segmind
        headers = {
            'x-api-key': SEGMIND_API_KEY,
            'Content-Type': 'application/json'
        }
        
        # Пробуем сначала с image_urls (как в документации)
        data = {
            "prompt": f"A photorealistic portrait of a person with beautiful {hairstyle_prompt}, high detail, 8k",
            "image_urls": [photo_url]
        }

        # Отправляем POST-запрос
        response = requests.post(SEGMIND_API_URL, json=data, headers=headers, timeout=120)
        
        # Если получили 406 с image_urls, попробуем с base64
        if response.status_code == 406:
            print("Trying with base64 encoded image...")
            
            # Загружаем изображение от Telegram и конвертируем в base64
            photo_response = requests.get(photo_url, timeout=30)
            photo_response.raise_for_status()
            photo_base64 = base64.b64encode(photo_response.content).decode('utf-8')
            
            # Пробуем с base64
            data_base64 = {
                "prompt": f"A photorealistic portrait of a person with beautiful {hairstyle_prompt}, high detail, 8k",
                "image": photo_base64
            }
            
            response = requests.post(SEGMIND_API_URL, json=data_base64, headers=headers, timeout=120)
            
            if response.status_code == 406:
                error_text = response.text if response.text else "Unknown error"
                print(f"Segmind API 406 Error: {error_text}")
                await context.bot.send_message(
                    chat_id=query.message.chat_id, 
                    text="😔 Ошибка обработки изображения. Возможно, проблемы с форматом изображения или API ключом."
                )
                return ConversationHandler.END
            
        response.raise_for_status() # Проверка на ошибки HTTP (4xx, 5xx)

        # Ответ от Segmind - это само изображение в виде байтов
        generated_image_bytes = response.content

        # Отправляем результат пользователю
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=generated_image_bytes,
            caption="Готово! Как тебе такой образ?"
        )
        await query.message.delete() # Удаляем сообщение "Отправляю запрос..."

    except requests.exceptions.RequestException as e:
        error_message = f"😔 Ошибка при связи с API Segmind. Проверьте ваш API ключ или попробуйте позже."
        print(f"Segmind API RequestException: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response text: {e.response.text}")
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_message)
    except Exception as e:
        error_message = f"😔 Ой, что-то пошло не так. Попробуйте еще раз с другим фото."
        print(f"General exception: {e}")
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
    if not TELEGRAM_TOKEN:
        print("Ошибка: Отсутствует переменная окружения TELEGRAM_TOKEN.")
        return
    if not SEGMIND_API_KEY:
        print("Ошибка: Отсутствует переменная окружения SEGMIND_API_KEY.")
        return
    
    print(f"Telegram Token: {'*' * 10}{TELEGRAM_TOKEN[-10:] if len(TELEGRAM_TOKEN) > 10 else 'SHORT'}")
    print(f"Segmind API Key: {'*' * 10}{SEGMIND_API_KEY[-10:] if len(SEGMIND_API_KEY) > 10 else 'SHORT'}")
    print(f"Segmind API URL: {SEGMIND_API_URL}")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
            HAIRSTYLE: [CallbackQueryHandler(generate_image_with_segmind)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=600
    )

    application.add_handler(conv_handler)

    print("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == '__main__':
    main()
