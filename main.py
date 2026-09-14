import asyncio
import logging

from aiogram import Bot, Dispatcher

import db
from config import BOT_TOKEN
from handlers import common, user, mentor, leader
from web_server import start_web_server


async def main():
    logging.basicConfig(level=logging.INFO)
    await db.init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(common.router)  # отмена ввода кнопками меню — всегда первым
    dp.include_router(mentor.router)
    dp.include_router(leader.router)
    dp.include_router(user.router)

    # Запуск HTTP сервера для вебхуков от Google Таблиц
    await start_web_server(bot)

    # Запуск фонового трекера смен из отчётников моделей
    from services.shift_tracker import start_shift_tracker
    asyncio.create_task(start_shift_tracker(bot, interval_seconds=180))

    # Запуск сервиса 6-часовых напоминаний и утреннего дайджеста
    from services.reminder_service import start_reminder_service
    asyncio.create_task(start_reminder_service(bot, interval_seconds=60))

    print("Бот запущен ✅")
    await dp.start_polling(bot)




if __name__ == "__main__":
    asyncio.run(main())
