"""Регистрация и профиль."""
from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

import config
import db
import logic
import services
import ui
from ui import ikb

router = Router()


class Reg(StatesGroup):
    name = State()
    dept = State()
    unit = State()
    value = State()
    level = State()


def reply_kb(options: list[str]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=o)] for o in options],
                               resize_keyboard=True, one_time_keyboard=True)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    u = await db.user(m.from_user.id)
    if u:
        await db.ex("UPDATE users SET username=? WHERE tg_id=?", m.from_user.username, u["tg_id"])
        await m.answer(f"С возвращением, <b>{ui.name(u)}</b>! 🏓\nЧто делаем? /help — подсказка.",
                       reply_markup=ui.main_kb())
        return
    await begin_registration(m, state)


async def begin_registration(m: Message, state: FSMContext, full_name: str | None = None):
    await state.set_state(Reg.name)
    await m.answer(
        "Привет! Это бот офисного настольного тенниса 🏓\n\n"
        "Здесь можно найти соперника, узнать, свободен ли стол, вести счёт и участвовать в турнире.\n\n"
        "Давай зарегистрируемся. <b>Как тебя зовут?</b> Напиши имя и фамилию, чтобы коллеги тебя узнали.",
        reply_markup=reply_kb([full_name or m.from_user.full_name]),
    )


@router.message(Reg.name, F.text, ~F.text.in_(ui.MENU))
async def reg_name(m: Message, state: FSMContext):
    name = m.text.strip()[:40]
    if len(name) < 2:
        await m.answer("Слишком коротко, напиши имя ещё раз.")
        return
    await state.update_data(name=name)
    await state.set_state(Reg.dept)
    depts = [r["department"] for r in await db.q(
        "SELECT department, COUNT(*) c FROM users WHERE department IS NOT NULL GROUP BY department ORDER BY c DESC LIMIT 8")]
    await m.answer("<b>Из какого ты отдела?</b> Выбери или напиши свой.",
                   reply_markup=reply_kb(depts) if depts else ReplyKeyboardRemove())


@router.message(Reg.dept, F.text, ~F.text.in_(ui.MENU))
async def reg_dept(m: Message, state: FSMContext):
    await state.update_data(dept=m.text.strip()[:40])
    await state.set_state(Reg.unit)
    await m.answer("Отлично! Теперь <b>сколько ты играешь в настольный теннис?</b>",
                   reply_markup=ReplyKeyboardRemove())
    await m.answer("Выбери, в чём считать:", reply_markup=ikb([
        [("Дни", "unit:d"), ("Месяцы", "unit:m"), ("Годы", "unit:y")],
        [("Ещё не играл(а)", "unit:0")],
    ]))


@router.callback_query(Reg.unit, F.data.startswith("unit:"))
async def reg_unit(c: CallbackQuery, state: FSMContext):
    unit = c.data.split(":")[1]
    await c.answer()
    if unit == "0":
        await state.update_data(unit=None, value=0)
        await ask_level(c.message, state)
        return
    await state.update_data(unit=unit)
    await state.set_state(Reg.value)
    await c.message.edit_text(f"Сколько {logic.UNITS[unit][0]}? Напиши число.")


@router.message(Reg.value, F.text, ~F.text.in_(ui.MENU))
async def reg_value(m: Message, state: FSMContext):
    if not m.text.strip().isdigit() or not 0 <= int(m.text) <= 3650:
        await m.answer("Напиши просто число, например <code>3</code>.")
        return
    await state.update_data(value=int(m.text))
    await ask_level(m, state)


async def ask_level(m: Message, state: FSMContext):
    await state.set_state(Reg.level)
    await m.answer("<b>Как ты оцениваешь свой уровень?</b>",
                   reply_markup=ikb([[(title, f"lvl:{k}")] for k, title in logic.LEVELS.items()]))


@router.callback_query(Reg.level, F.data.startswith("lvl:"))
async def reg_level(c: CallbackQuery, state: FSMContext):
    level = int(c.data.split(":")[1])
    d = await state.get_data()
    await state.clear()
    unit, value = d.get("unit"), d.get("value", 0)
    days = value * logic.UNITS[unit][1] if unit else 0
    seed = logic.seed_score(level, days)
    league = logic.league_for(seed)
    uid = c.from_user.id
    exists = await db.user(uid)
    if exists:
        await db.ex("UPDATE users SET name=?, department=?, exp_value=?, exp_unit=?, exp_days=?, level=?, seed=?, "
                    "username=? WHERE tg_id=?", d["name"], d["dept"], value, unit, days, level, seed,
                    c.from_user.username, uid)
    else:
        await db.ex("INSERT INTO users(tg_id, username, name, department, exp_value, exp_unit, exp_days, level, seed, "
                    "is_admin, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    uid, c.from_user.username, d["name"], d["dept"], value, unit, days, level, seed,
                    int(uid in config.ADMIN_IDS), db.now())
    await c.answer("Сохранено!")
    await c.message.edit_text(f"Уровень: {logic.LEVELS[level]} ✅")
    await c.message.answer("Готово, ты зарегистрирован(а)! 🎉", reply_markup=ui.main_kb())
    await offer_tournament(c.message, league, seed)


async def offer_tournament(m: Message, league: str, seed: int):
    t = await db.active_tournament()
    t_line = (f"Сейчас идёт регистрация/турнир <b>{ui.esc(t['name'])}</b>." if t
              else "Турнир пока не открыт — если согласишься, попадёшь в него автоматически, как только он откроется.")
    others = [(f"Хочу в {name}", f"join:{code}") for code, name, _ in logic.LEAGUES if code != league]
    await m.answer(
        f"🏆 Твой рейтинг посева: <b>{seed}</b>\nПредлагаем тебе <b>{ui.league_name(league)}</b>.\n\n{t_line}\n\n"
        "Добавить тебя в турнирную таблицу?",
        reply_markup=ikb([[(f"✅ Да, {ui.league_name(league)}", f"join:{league}")], others,
                          [("🤝 Только дружеские игры", "join:no")]]),
    )


@router.callback_query(F.data.startswith("join:"))
async def join(c: CallbackQuery, bot: Bot):
    code = c.data.split(":")[1]
    uid = c.from_user.id
    t = await db.active_tournament()
    if code == "no":
        await db.ex("UPDATE users SET wants_tournament=0 WHERE tg_id=?", uid)
        if t and t["status"] == "registration":
            await services.remove_from_tournament(t, uid)
        await c.answer()
        await c.message.edit_text("Ок, только дружеские игры 🤝 Передумаешь — зайди в «👤 Профиль».")
        return
    await db.ex("UPDATE users SET wants_tournament=1, league=? WHERE tg_id=?", code, uid)
    await c.answer()
    if not t:
        await c.message.edit_text(f"Записал в {ui.league_name(code)} ✅ Как только админ откроет турнир — ты в нём.")
        return
    entry = await db.player_entry(t["id"], uid)
    if entry and t["status"] == "registration":
        await db.ex("UPDATE tournament_players SET league=? WHERE tournament_id=? AND tg_id=?", code, t["id"], uid)
    elif entry:
        await c.message.edit_text("Ты уже в турнире. Сменить лигу во время турнира может только админ.")
        return
    else:
        await services.add_to_tournament(bot, t, uid, code)
    text = f"Ты в турнире <b>{ui.esc(t['name'])}</b>, {ui.league_name(code)} ✅"
    if t["status"] == "running":
        text += f"\nСмотри соперников в «{ui.B_MY}»."
    await c.message.edit_text(text)


@router.message(F.text == ui.B_PROF)
async def profile(m: Message, state: FSMContext):
    await state.clear()
    u = await db.user(m.from_user.id)
    if not u:
        await begin_registration(m, state)
        return
    wins = await db.q1("SELECT COUNT(*) c FROM matches WHERE status='confirmed' AND winner=?", u["tg_id"])
    total = await db.q1("SELECT COUNT(*) c FROM matches WHERE status='confirmed' AND (p1=? OR p2=?)", u["tg_id"], u["tg_id"])
    text = ui.profile_text(u) + f"\n\nВсего матчей: {total['c']}, побед: {wins['c']}"
    await m.answer(text, reply_markup=ikb([
        [("🏆 Турнир / лига", "prof:t")],
        [("✏️ Изменить данные", "prof:edit")],
    ]))


@router.callback_query(F.data == "prof:edit")
async def prof_edit(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await begin_registration(c.message, state, c.from_user.full_name)


@router.callback_query(F.data == "prof:t")
async def prof_t(c: CallbackQuery):
    u = await db.user(c.from_user.id)
    await c.answer()
    await offer_tournament(c.message, u["league"] or logic.league_for(u["seed"]), u["seed"])
