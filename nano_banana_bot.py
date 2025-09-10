import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
import requests
import os
import base64
import asyncio
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
    # Получаем информацию о файле
    photo_file = await update.message.photo[-1].get_file()
    
    # Скачиваем файл напрямую в память
    photo_bytes = await photo_file.download_as_bytearray()
    
    # Кодируем в base64 для отправки в API
    photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
    context.user_data['photo_base64'] = photo_base64
    
    print(f"Photo downloaded and encoded to base64, size: {len(photo_bytes)} bytes")

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
    photo_base64 = context.user_data.get('photo_base64')

    if not photo_base64:
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
        
        print(f"Using base64 encoded image, size: {len(photo_base64)} chars")
        print(f"Using prompt: A photorealistic portrait of a person with beautiful {hairstyle_prompt}, high detail, 8k")
        
        # Формируем заголовки и тело запроса для Segmind
        headers = {
            'x-api-key': SEGMIND_API_KEY,
            'Content-Type': 'application/json'
        }
        
        # Попробуем несколько форматов запроса
        # Формат 1: с base64 изображением
        data = {
            "prompt": f"Transform the hairstyle of this person to {hairstyle_prompt}, preserve the original face and identity completely, photorealistic, high quality, professional portrait, 8k resolution",
            "images": [photo_base64]  # Попробуем 'images' вместо 'image'
        }

        # Отправляем POST-запрос с retry механизмом
        max_retries = 3
        retry_delay = 5  # секунд
        
        # Пробуем разные форматы данных
        data_formats = [
            {
                "prompt": f"Transform the hairstyle of this person to {hairstyle_prompt}, preserve the original face and identity completely, photorealistic, high quality, professional portrait, 8k resolution",
                "images": [photo_base64]
            },
            {
                "prompt": f"Transform the hairstyle of this person to {hairstyle_prompt}, preserve the original face and identity completely, photorealistic, high quality, professional portrait, 8k resolution",
                "image": photo_base64
            },
            {
                "prompt": f"Change hairstyle to {hairstyle_prompt}, keep same person, photorealistic",
                "image_base64": photo_base64
            }
        ]
        
        success = False
        for format_idx, data in enumerate(data_formats):
            print(f"Trying data format {format_idx + 1}: {list(data.keys())}")
            
            for attempt in range(max_retries):
                try:
                    response = requests.post(SEGMIND_API_URL, json=data, headers=headers, timeout=120)
                
                # Если сервер временно недоступен (502, 503, 504), пробуем еще раз
                if response.status_code in [502, 503, 504] and attempt < max_retries - 1:
                    print(f"Server error {response.status_code}, retrying in {retry_delay} seconds... (attempt {attempt + 1}/{max_retries})")
                    # Обновляем сообщение пользователю
                    await query.edit_message_text(
                        text=f"⏳ Сервер временно перегружен, повторяю попытку... ({attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Увеличиваем задержку для следующей попытки
                    continue
                
                # Проверяем специфичные ошибки
                if response.status_code == 406:
                    error_text = response.text if response.text else "Unknown error"
                    print(f"Segmind API 406 Error: {error_text}")
                    await context.bot.send_message(
                        chat_id=query.message.chat_id, 
                        text="😔 Ошибка обработки изображения. Возможно, проблемы с форматом изображения или API ключом."
                    )
                    return ConversationHandler.END
                
                # Если получили 502/503/504 на последней попытке
                if response.status_code in [502, 503, 504]:
                    print(f"Server error {response.status_code} persists after all retries")
                    await context.bot.send_message(
                        chat_id=query.message.chat_id, 
                        text="😔 Сервер Segmind временно недоступен. Попробуйте позже через несколько минут."
                    )
                    return ConversationHandler.END
                    
                    response.raise_for_status() # Проверка на ошибки HTTP (4xx, 5xx)
                    success = True
                    break  # Успешный ответ, выходим из цикла
                    
                except requests.exceptions.Timeout:
                    if attempt < max_retries - 1:
                        print(f"Request timeout, retrying in {retry_delay} seconds... (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    else:
                        raise
            
            if success:
                break  # Успешный формат, выходим из цикла форматов
            else:
                print(f"Data format {format_idx + 1} failed, trying next format...")
        
        if not success:
            await context.bot.send_message(
                chat_id=query.message.chat_id, 
                text="😔 Не удалось обработать изображение ни с одним из форматов данных."
            )
            return ConversationHandler.END        # Ответ от Segmind - это само изображение в виде байтов
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
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(conv_handler)

    print("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == '__main__':
    main()
