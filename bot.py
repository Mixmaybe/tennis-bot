import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

import config
import db
import services
from handlers import guide, live, matches, play, reg, tourney


async def ticker(bot: Bot):
    """Сгоревшие брони стола передаются следующей паре из очереди."""
    while True:
        try:
            await services.housekeeping(bot)
        except Exception:
            logging.exception("housekeeping")
        await asyncio.sleep(10)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not config.BOT_TOKEN:
        raise SystemExit("Заполни BOT_TOKEN в файле .env (токен от @BotFather)")
    await db.init(config.DB_PATH)
    # брошенные заготовки дружеских матчей старше суток
    await db.ex("DELETE FROM matches WHERE status='setup' AND created_at < ?", db.now() - 86400)

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(reg.router, guide.router, play.router, live.router, matches.router, tourney.router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать / главное меню"),
        BotCommand(command="help", description="Инструкция: как играть, вести счёт, турнир"),
        BotCommand(command="verify", description="Верификация: фото пропуска или своё фото"),
        BotCommand(command="qr", description="QR-код, чтобы позвать коллег"),
        BotCommand(command="admin", description="Команды админа"),
    ])
    me = await bot.get_me()
    logging.info("Бот @%s запущен", me.username)
    asyncio.create_task(ticker(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
