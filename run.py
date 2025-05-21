import asyncio
import logging

from bot.bot import bot, dp

import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def start_bot():
    """Запускаем Telegram-бота (aiogram) через long polling."""
    try:
        # Если ранее был какой-то вебхук, удаляем
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.exception("Не смог удалить предыдущий webhook: %s", e)

    # Начинаем приём сообщений
    await dp.start_polling(bot, skip_updates=True)


async def main():
    # 1) Конфигурация и запуск Uvicorn для FastAPI
    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=True,  # при разработке, удалить в продакшене(не забыть)
    )
    server = uvicorn.Server(config)

    task_api = asyncio.create_task(server.serve())
    task_bot = asyncio.create_task(start_bot())

    await asyncio.wait([task_api, task_bot], return_when=asyncio.FIRST_COMPLETED)


if __name__ == "__main__":
    asyncio.run(main())
