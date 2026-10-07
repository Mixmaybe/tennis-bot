import asyncio
import logging
import os
import socket
import sys
from logging.handlers import RotatingFileHandler

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
            await live.close_stale(bot)
            await services.housekeeping(bot)
        except Exception:
            logging.exception("housekeeping")
        await asyncio.sleep(10)


ALREADY_RUNNING = 3  # код выхода, по которому start_bot.cmd понимает, что перезапускать не нужно
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def single_instance() -> socket.socket:
    """Два бота с одним токеном мешают друг другу — второй экземпляр сразу выходит."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", config.LOCK_PORT))
    except OSError:
        print("Бот уже запущен", file=sys.stderr)
        raise SystemExit(ALREADY_RUNNING)
    return lock


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    handlers = [RotatingFileHandler(os.path.join(LOG_DIR, "bot.log"), maxBytes=1_000_000, backupCount=3,
                                    encoding="utf-8")]
    if sys.stderr and sys.stderr.isatty():  # запуск из консоли — дублируем журнал на экран
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


async def main():
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
    _lock = single_instance()
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except BaseException:
        logging.exception("Бот упал, start_bot.cmd перезапустит его через 10 секунд")
        raise
