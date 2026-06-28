import html
import logging

from aiogram.types import Message
from aiogram import Bot
from config import ADMIN

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096

def split_message(text, limit=MAX_MESSAGE_LENGTH):
    parts = []
    while len(text) > limit:
        split_index = text.rfind('\n', 0, limit)
        if split_index == -1:
            split_index = limit
        parts.append(text[:split_index])
        text = text[split_index:].lstrip()
    parts.append(text)
    return parts

async def handle_admin_command(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN:
        return

    if not message.text.startswith("!message"):
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("❗️Использование: !message <user_id> <сообщение>")
        return

    try:
        target_user_id = int(args[1])
        text = args[2]
        for part in split_message(text):
            await bot.send_message(chat_id=target_user_id, text=part)
        await message.reply("✅ Сообщение отправлено.")
    except Exception as e:
        await message.reply(f"⚠️ Ошибка: {e}")

async def handle_user_message(message: Message, bot: Bot):
    if message.from_user.id in ADMIN:
        return  # не пересылать сообщения от админов

    # Экранируем пользовательский текст — у бота включён parse_mode=HTML по умолчанию
    text = html.escape(message.text) if message.text else "&lt;не текстовое сообщение&gt;"
    user = message.from_user
    username = html.escape(user.username) if user.username else "без username"
    header = f"📩 Ответ от @{username} (ID: {user.id}):\n"

    for admin_id in ADMIN:
        try:
            for i, part in enumerate(split_message(text)):
                prefix = header if i == 0 else f"(Продолжение от {user.id}):\n"
                await bot.send_message(chat_id=admin_id, text=prefix + part)
        except Exception as e:
            logger.exception("Ошибка пересылки админу %s: %s", admin_id, e)