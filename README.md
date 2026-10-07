# Office Table Tennis — Telegram Bot

[English](#english) · [Русский](#русский)

---

## English

A Telegram bot for table tennis in the office. People can find someone to play with, book the table or join a queue for it, record match results, and run an office tournament with standings.

### Features

- **Registration via QR code.** The bot asks for name, department, years of experience and a self-assessed level (beginner → professional). It then suggests a league and offers to join the tournament. There is an optional verification step: send a photo of an office pass or a selfie, and an admin approves it.
- **"Ready to play" status.** Options are now, in 5 / 30 / 60 minutes, or "waiting for an invite". The status resets on its own. If the ready time falls in working hours (9–13, 14–18), the bot shows a reminder that work comes first.
- **Invites.** When an invite is accepted, the bot offers to book the free table for 10 minutes. If the table is busy, it puts the pair in a queue and shows their position and who is playing now. When the table frees up, the next pair is notified and gets the booking automatically. Bookings are serialized, so two pairs pressing at the same moment can never both get the table.
- **Friendly games.** Book the table, press "Started playing" (the table is held for 30 min), play, press "Finished", and optionally enter the score. The opponent confirms the result.
- **Tournament.** Players are split into leagues by seeding. Inside each league, everyone plays everyone once (round robin), in any order. The bot suggests the next opponent, preferring players who are ready right now.
- **Live scoring (tournament only).** Players or a referee press "+1" after each rally. The bot closes games (11 points, win by 2 after 10:10) and shows who serves. Anyone can watch the scoreboard live. When a referee keeps the score, both players confirm the result. Abandoned matches close automatically after 30 minutes without a point.
- **Ending a match.** Players can end a match without a result at any time.

### Ranking (ITTF style)

- **Match points:** win 2, loss 1, walkover 0.
- **Ties:** first head-to-head match points, then the games won/lost ratio in those matches, then the points ratio. After that, the same ratios across all matches.
- **Result entry:** a match is best of 3 by default (`BEST_OF`). A tournament result counts only after a referee, an admin or both players confirm it.

### Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and get the token.
2. Find your Telegram ID with [@userinfobot](https://t.me/userinfobot).
3. Copy `.env.example` to `.env` and fill it in:
   ```
   BOT_TOKEN=123456:ABC...
   ADMIN_IDS=111111111,222222222
   TABLES=1
   BEST_OF=3
   DB_PATH=tennis.db
   ```
4. Install and run (Python 3.11+):
   ```
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements.txt   # Windows
   .venv/bin/python -m pip install -r requirements.txt       # Linux/macOS
   python bot.py
   ```
5. Send `/qr` to the bot, print the QR code and put it next to the table.

The bot uses long polling, so it needs no public URL. It works only while the process is running. Data is stored in SQLite (`tennis.db`), and copying that file is enough for a backup.

### Commands

| Command | Who | What |
|---|---|---|
| `/start`, `/help` | everyone | main menu and guide |
| `/verify` | everyone | optional verification by photo |
| `/qr` | everyone | QR code with a link to the bot |
| `/admin` | admins | list of admin commands |
| `/newtournament Name` | admins | open tournament registration |
| `/starttournament` | admins | split players into leagues and create the schedule |
| `/finishtournament` | admins | close the tournament and announce winners |
| `/pending` | admins | results waiting for confirmation |
| `/players` | admins | all players |
| `/setleague @user A\|B\|C` | admins | move a player to another league |
| `/removeplayer @user` | admins | remove a player from the tournament |
| `/walkover ID @winner` | admins | award a walkover |
| `/addadmin`, `/deladmin @user` | admins | manage trusted admins |
| `/broadcast text` | admins | message to everyone |

### Project structure

```
bot.py            entry point, routers, background ticker (queue, stale matches)
config.py         settings from .env
db.py             SQLite schema, migrations, queries
logic.py          pure logic: seeding, leagues, score validation, live replay, ITTF standings
services.py       shared operations: tournament, tables & queue, results & confirmation
ui.py             buttons, texts, formatting
handlers/
  reg.py          registration, profile, verification
  guide.py        /help guide
  play.py         status, who is ready, invites, table booking & queue, QR
  live.py         live scoring, referee, spectators, ending matches
  matches.py      my matches, result entry, referee choice, confirmation
  tourney.py      standings and admin commands
```

---

## Русский

Telegram-бот для настольного тенниса в офисе. С ним можно найти, с кем сыграть, забронировать стол или встать в очередь, записать результат и провести офисный турнир с таблицей.

### Возможности

- **Регистрация по QR-коду.** Бот спрашивает имя, отдел, стаж и самооценку уровня (от начинающего до профессионала). По ответам он предлагает лигу и участие в турнире. Есть необязательная верификация: фото пропуска или селфи, которое проверяет админ.
- **Статус «готов играть».** Варианты: сейчас, через 5 / 30 / 60 минут, «жду приглашения». Статус сбрасывается сам. Если время готовности попадает на рабочие часы (9–13, 14–18), бот напоминает, что работа в приоритете.
- **Приглашения.** Когда приглашение принято, бот предлагает забронировать свободный стол на 10 минут. Если стол занят, пара встаёт в очередь и видит свой номер и кто сейчас играет. Когда стол освобождается, следующей паре приходит уведомление, и стол бронируется за ней сам. Брони обрабатываются строго по одной, поэтому две пары, нажавшие одновременно, не получат стол обе.
- **Дружеская игра.** Забронировали стол → «Начали играть» (стол за вами 30 минут) → сыграли → «Закончили» → по желанию вносите счёт. Соперник подтверждает результат.
- **Турнир.** Игроки делятся на лиги по рейтингу посева. Внутри лиги каждый играет с каждым один матч, порядок свободный. Бот подсказывает следующего соперника и в первую очередь предлагает тех, кто готов играть сейчас.
- **Живой счёт (только турнир).** Игроки или судья жмут «+1» после каждого розыгрыша. Бот сам закрывает партии (до 11, после 10:10 до разницы в 2) и показывает подачу. Любой может смотреть табло в реальном времени. Если счёт ведёт судья, результат подтверждают оба игрока. Брошенный матч закрывается сам через 30 минут без очков.
- **Завершение матча.** Игроки в любой момент могут завершить матч без результата.

### Таблица (по правилам ITTF)

- **Очки:** победа 2, поражение 1, неявка 0.
- **При равенстве:** сначала очки в личных встречах, потом соотношение партий в них, потом соотношение мячей. Дальше те же соотношения по всем матчам.
- **Результат:** по умолчанию матч идёт до 2 побед (`BEST_OF=3`). Турнирный результат засчитывается только после подтверждения судьёй, админом или обоими игроками.

### Установка

1. Создайте бота в [@BotFather](https://t.me/BotFather) и получите токен.
2. Узнайте свой Telegram ID у [@userinfobot](https://t.me/userinfobot).
3. Скопируйте `.env.example` в `.env` и заполните (пример выше, в английском разделе).
4. Установите зависимости и запустите (Python 3.11+):
   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   .venv\Scripts\python bot.py
   ```
5. Отправьте боту `/qr`, распечатайте QR-код и повесьте у стола.

Бот работает через long polling, поэтому публичный адрес ему не нужен. Он работает, только пока запущен процесс. Данные хранятся в SQLite (`tennis.db`): для резервной копии достаточно скопировать этот файл.

### Команды

| Команда | Кто | Что делает |
|---|---|---|
| `/start`, `/help` | все | главное меню и инструкция |
| `/verify` | все | необязательная верификация по фото |
| `/qr` | все | QR-код со ссылкой на бота |
| `/admin` | админы | список админских команд |
| `/newtournament Название` | админы | открыть регистрацию на турнир |
| `/starttournament` | админы | разбить по лигам и создать расписание |
| `/finishtournament` | админы | завершить турнир и объявить победителей |
| `/pending` | админы | результаты, ждущие подтверждения |
| `/players` | админы | все игроки |
| `/setleague @user A\|B\|C` | админы | перевести игрока в другую лигу |
| `/removeplayer @user` | админы | убрать игрока из турнира |
| `/walkover ID @победитель` | админы | техническая победа |
| `/addadmin`, `/deladmin @user` | админы | доверенные админы |
| `/broadcast текст` | админы | сообщение всем |

### Структура проекта

```
bot.py            точка входа, роутеры, фоновая проверка (очередь, брошенные матчи)
config.py         настройки из .env
db.py             схема SQLite, миграции, запросы
logic.py          чистая логика: посев, лиги, проверка счёта, живой счёт, таблица ITTF
services.py       общие операции: турнир, столы и очередь, результаты и подтверждение
ui.py             кнопки, тексты, форматирование
handlers/
  reg.py          регистрация, профиль, верификация
  guide.py        инструкция /help
  play.py         статус, кто готов, приглашения, бронь стола и очередь, QR
  live.py         живой счёт, судья, зрители, завершение матча
  matches.py      мои матчи, ввод результата, выбор судьи, подтверждение
  tourney.py      таблица и команды админа
```
