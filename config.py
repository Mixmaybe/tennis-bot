import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# Telegram ID главных админов через запятую (узнать свой ID: @userinfobot)
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
# Сколько теннисных столов в офисе
TABLES = int(os.getenv("TABLES", "1"))
# Матч до скольки партий: 3 = до 2 побед, 5 = до 3 побед
BEST_OF = int(os.getenv("BEST_OF", "3"))
DB_PATH = os.getenv("DB_PATH", "tennis.db")
# Локальный порт-«замок», чтобы на компьютере не запустились два бота сразу
LOCK_PORT = int(os.getenv("LOCK_PORT", "47231"))
