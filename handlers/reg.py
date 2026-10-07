"""Регистрация, профиль и необязательная верификация."""
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

import config
import db
import logic
import services
import ui
from handlers import guide
from ui import ikb

router = Router()


class Reg(StatesGroup):
    name = State()
    dept = State()
    unit = State()
    value = State()
    level = State()
    confirm = State()


class Verify(StatesGroup):
    photo = State()


LEVEL_HINTS = {
    1: "играл(а) пару раз, учусь попадать по мячу",
    2: "иногда играю, могу держать розыгрыш",
    3: "играю регулярно, есть своя подача и вращения",
    4: "занимался(ась) в секции или играю в любительских лигах",
    5: "есть разряд или спортивное прошлое в теннисе",
}


def reply_kb(options: list[str]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=o)] for o in options],
                               resize_keyboard=True, one_time_keyboard=True)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    u = await db.user(m.from_user.id)
    if u:
        await db.ex("UPDATE users SET username=? WHERE tg_id=?", m.from_user.username, u["tg_id"])
        await m.answer(f"С возвращением, <b>{ui.name(u)}</b>! 🏓\nКнопки меню внизу, инструкция — /help",
                       reply_markup=ui.main_kb())
        return
    await m.answer(
        "Привет! Это бот офисного настольного тенниса 🏓\n\n"
        "<b>Что здесь можно делать:</b>\n"
        "• найти, с кем поиграть прямо сейчас, и позвать коллегу;\n"
        "• видеть, свободен ли стол;\n"
        "• вести счёт кнопками прямо во время игры;\n"
        "• участвовать в офисном турнире с таблицей.\n\n"
        "<b>Регистрация займёт минуту, всего 4 вопроса:</b>\n"
        "1️⃣ имя и фамилия\n2️⃣ отдел\n3️⃣ сколько играешь\n4️⃣ свой уровень\n\n"
        "В конце покажу всё, что ты ввёл(а), и можно будет исправить. "
        "Данные видят только коллеги в этом боте.\n\n"
        f"<i>{ui.WORK_NOTICE}</i>",
        reply_markup=ReplyKeyboardRemove(),
    )
    await begin_registration(m, state)


async def begin_registration(m: Message, state: FSMContext, full_name: str | None = None):
    await state.set_state(Reg.name)
    await m.answer(
        "1️⃣ <b>Как тебя зовут?</b>\n"
        "Напиши <b>имя и фамилию</b>, как тебя знают в офисе, например <i>Анна Смирнова</i>. "
        "По имени тебя будут искать, приглашать и выбирать судьёй.\n\n"
        "Если имя из Telegram подходит, просто нажми кнопку внизу 👇",
        reply_markup=reply_kb([full_name or m.from_user.full_name]),
    )


@router.message(Reg.name, F.text, ~F.text.in_(ui.MENU))
async def reg_name(m: Message, state: FSMContext):
    name = " ".join(m.text.split())[:40]
    if len(name) < 2 or name.startswith("/"):
        await m.answer("Не похоже на имя 🙂 Напиши имя и фамилию, например <i>Анна Смирнова</i>.")
        return
    await state.update_data(name=name)
    await state.set_state(Reg.dept)
    depts = [r["department"] for r in await db.q(
        "SELECT department, COUNT(*) c FROM users WHERE department IS NOT NULL GROUP BY department ORDER BY c DESC LIMIT 8")]
    hint = ("Если твой отдел уже есть в кнопках внизу — <b>выбери его кнопкой</b>, "
            "чтобы не было дублей вроде «IT» и «ИТ». Если нет — напиши название." if depts
            else "Напиши название отдела, например <i>Бухгалтерия</i> или <i>IT</i>.")
    await m.answer(f"2️⃣ <b>Из какого ты отдела?</b>\n{hint}",
                   reply_markup=reply_kb(depts) if depts else ReplyKeyboardRemove())


@router.message(Reg.dept, F.text, ~F.text.in_(ui.MENU))
async def reg_dept(m: Message, state: FSMContext):
    dept = " ".join(m.text.split())[:40]
    if len(dept) < 2 or dept.startswith("/"):
        await m.answer("Напиши название отдела, например <i>Бухгалтерия</i>.")
        return
    await state.update_data(dept=dept)
    await state.set_state(Reg.unit)
    await m.answer("👍", reply_markup=ReplyKeyboardRemove())
    await m.answer(
        "3️⃣ <b>Сколько ты играешь в настольный теннис?</b>\n"
        "Имеется в виду общий стаж, примерно. Например, играл(а) 3 года в школе — это «Годы» → 3. "
        "Начал(а) месяц назад — «Месяцы» → 1.\n\nСначала выбери, в чём считать:",
        reply_markup=ikb([
            [("Дни", "unit:d"), ("Месяцы", "unit:m"), ("Годы", "unit:y")],
            [("Ещё не играл(а)", "unit:0")],
        ]))


@router.callback_query(Reg.unit, F.data.startswith("unit:"))
async def reg_unit(c: CallbackQuery, state: FSMContext):
    unit = c.data.split(":")[1]
    await c.answer()
    if unit == "0":
        await state.update_data(unit=None, value=0)
        await c.message.edit_text("3️⃣ Стаж: ещё не играл(а) ✅")
        await ask_level(c.message, state)
        return
    await state.update_data(unit=unit)
    await state.set_state(Reg.value)
    await c.message.edit_text(f"3️⃣ Сколько <b>{logic.UNITS[unit][0]}</b>? Напиши только число, например <code>2</code>.")


@router.message(Reg.value, F.text, ~F.text.in_(ui.MENU))
async def reg_value(m: Message, state: FSMContext):
    unit = (await state.get_data())["unit"]
    limit = {"d": 3650, "m": 600, "y": 70}[unit]
    text = m.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= limit:
        await m.answer(f"Напиши просто число от 1 до {limit}, без слов, например <code>3</code>.")
        return
    await state.update_data(value=int(text))
    await ask_level(m, state)


async def ask_level(m: Message, state: FSMContext):
    await state.set_state(Reg.level)
    lines = "\n".join(f"<b>{logic.LEVELS[k]}</b> — {hint}" for k, hint in LEVEL_HINTS.items())
    await m.answer(
        f"4️⃣ <b>Как ты оцениваешь свой уровень?</b>\n\n{lines}\n\n"
        "Отвечай честно: по уровню бот подберёт лигу, где тебе будет интересно играть.",
        reply_markup=ikb([[(title, f"lvl:{k}")] for k, title in logic.LEVELS.items()]))


@router.callback_query(Reg.level, F.data.startswith("lvl:"))
async def reg_level(c: CallbackQuery, state: FSMContext):
    level = int(c.data.split(":")[1])
    await state.update_data(level=level)
    await state.set_state(Reg.confirm)
    d = await state.get_data()
    exp = logic.plural(d["value"], d["unit"]) if d.get("unit") else "ещё не играл(а)"
    await c.answer()
    await c.message.edit_text(
        "📝 <b>Проверь, всё ли верно:</b>\n\n"
        f"Имя: <b>{ui.esc(d['name'])}</b>\n"
        f"Отдел: <b>{ui.esc(d['dept'])}</b>\n"
        f"Стаж: <b>{exp}</b>\n"
        f"Уровень: <b>{logic.LEVELS[level]}</b>",
        reply_markup=ikb([[("✅ Всё верно", "reg:ok")], [("✏️ Заполнить заново", "reg:redo")]]))


@router.callback_query(Reg.confirm, F.data == "reg:redo")
async def reg_redo(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await c.message.edit_text("Хорошо, заполним заново.")
    await begin_registration(c.message, state, c.from_user.full_name)


@router.callback_query(Reg.confirm, F.data == "reg:ok")
async def reg_save(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    unit, value, level = d.get("unit"), d.get("value", 0), d["level"]
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
    await c.message.edit_reply_markup(reply_markup=None)
    if exists:
        await c.message.answer("Данные обновлены ✅", reply_markup=ui.main_kb())
        return
    await c.message.answer("Готово, ты зарегистрирован(а)! 🎉 Внизу появилось меню с кнопками.",
                           reply_markup=ui.main_kb())
    await offer_tournament(c.message, league, seed)
    await c.message.answer(
        "🪪 <b>Необязательно:</b> подтверди, что ты из нашего офиса. Отправь фото пропуска или своё фото, "
        "админ проверит, и рядом с твоим именем появится ☑️. Так коллегам проще понять, кто есть кто.",
        reply_markup=ikb([[("🪪 Пройти верификацию", "vf:start")], [("Позже", "vf:later")]]))
    await c.message.answer(guide.INTRO, reply_markup=guide.guide_kb())


async def offer_tournament(m: Message, league: str, seed: int):
    t = await db.active_tournament()
    t_line = (f"Сейчас идёт регистрация/турнир <b>{ui.esc(t['name'])}</b>." if t
              else "Турнир пока не открыт — если согласишься, попадёшь в него автоматически, как только он откроется.")
    others = [(f"Хочу в {name}", f"join:{code}") for code, name, _ in logic.LEAGUES if code != league]
    await m.answer(
        f"🏆 Твой рейтинг посева: <b>{seed}</b>\nПредлагаем тебе <b>{ui.league_name(league)}</b>.\n\n{t_line}\n\n"
        "Добавить тебя в турнирную таблицу? Это не мешает играть дружеские игры.",
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
        await start(m, state)
        return
    wins = await db.q1("SELECT COUNT(*) c FROM matches WHERE status='confirmed' AND winner=?", u["tg_id"])
    total = await db.q1("SELECT COUNT(*) c FROM matches WHERE status='confirmed' AND (p1=? OR p2=?)", u["tg_id"], u["tg_id"])
    text = ui.profile_text(u) + f"\n\nВсего матчей: {total['c']}, побед: {wins['c']}"
    rows = [[("🏆 Турнир / лига", "prof:t")], [("✏️ Изменить данные", "prof:edit")]]
    if (u.get("verified") or 0) in (0, -1):
        rows.append([("🪪 Пройти верификацию", "vf:start")])
    await m.answer(text, reply_markup=ikb(rows))


@router.callback_query(F.data == "prof:edit")
async def prof_edit(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await begin_registration(c.message, state, c.from_user.full_name)


@router.callback_query(F.data == "prof:t")
async def prof_t(c: CallbackQuery):
    u = await db.user(c.from_user.id)
    await c.answer()
    await offer_tournament(c.message, u["league"] or logic.league_for(u["seed"]), u["seed"])


# ---------- верификация ----------

VERIFY_ASK = (
    "🪪 <b>Верификация (необязательно)</b>\n\n"
    "Отправь <b>одно фото</b> — на выбор:\n"
    "• <b>офисный пропуск</b> — можно закрыть пальцем номер или штрихкод, главное, чтобы был виден сам пропуск;\n"
    "• или <b>своё фото</b> (селфи), чтобы коллеги узнали тебя у стола.\n\n"
    "Фото увидят только админы бота. Отправь его как обычную картинку 📎, не файлом."
)


@router.message(Command("verify"))
async def verify_cmd(m: Message, state: FSMContext):
    u = await db.user(m.from_user.id)
    if not u:
        await m.answer("Сначала зарегистрируйся: /start")
        return
    if u.get("verified") == 1:
        await m.answer("Ты уже верифицирован(а) ☑️")
        return
    await ask_verify_kind(m, state)


async def ask_verify_kind(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(VERIFY_ASK, reply_markup=ikb([
        [("🪪 Фото пропуска", "vf:kind:pass"), ("🤳 Своё фото", "vf:kind:selfie")],
        [("Отмена", "vf:later")],
    ]))


@router.callback_query(F.data == "vf:start")
async def vf_start(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await ask_verify_kind(c.message, state)


@router.callback_query(F.data == "vf:later")
async def vf_later(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await c.message.edit_text("Ок! Верификацию можно пройти в любой момент: /verify или «👤 Профиль».")


@router.callback_query(F.data.startswith("vf:kind:"))
async def vf_kind(c: CallbackQuery, state: FSMContext):
    kind = c.data.split(":")[2]
    await state.set_state(Verify.photo)
    await state.update_data(kind=kind)
    await c.answer()
    what = "фото пропуска" if kind == "pass" else "своё фото"
    await c.message.edit_text(f"Жду {what} 📷 — просто отправь картинку в этот чат.")


@router.message(Verify.photo, F.photo)
async def vf_photo(m: Message, state: FSMContext, bot: Bot):
    kind = (await state.get_data()).get("kind", "pass")
    await state.clear()
    file_id = m.photo[-1].file_id
    await db.ex("UPDATE users SET verified=2, verify_photo=?, verify_kind=? WHERE tg_id=?", file_id, kind, m.chat.id)
    u = await db.user(m.chat.id)
    caption = (f"🪪 Заявка на верификацию\n{ui.full(u)}"
               f"{' @' + ui.esc(u['username']) if u['username'] else ''}\n"
               f"Тип: {'пропуск' if kind == 'pass' else 'своё фото'}")
    kb = ikb([[("☑️ Подтвердить", f"vfa:ok:{u['tg_id']}"), ("❌ Отклонить", f"vfa:no:{u['tg_id']}")]])
    sent = 0
    for a in await db.admins():
        if a["tg_id"] == u["tg_id"]:
            continue
        try:
            await bot.send_photo(a["tg_id"], file_id, caption=caption, reply_markup=kb)
            sent += 1
        except Exception:
            pass
    if not sent and db.is_admin(u):  # админ проверяет сам себя, если других админов нет
        await db.ex("UPDATE users SET verified=1 WHERE tg_id=?", u["tg_id"])
        await m.answer("Ты админ, других админов нет — отметил тебя верифицированным ☑️")
        return
    await m.answer("Спасибо! Фото отправлено админу на проверку ⏳ Я сообщу, когда он посмотрит.")


@router.message(Verify.photo, F.document)
async def vf_document(m: Message):
    await m.answer("Это пришло файлом 📄. Отправь, пожалуйста, как фото: 📎 → «Фото или видео».")


@router.message(Verify.photo, ~F.text.in_(ui.MENU))
async def vf_other(m: Message):
    await m.answer("Жду именно фотографию 📷 Передумал(а) — отправь /start.")


@router.callback_query(F.data.startswith("vfa:"))
async def vf_decide(c: CallbackQuery, bot: Bot):
    _, act, uid = c.data.split(":")
    if not db.is_admin(await db.user(c.from_user.id)):
        await c.answer("Только для админов", show_alert=True)
        return
    u = await db.user(int(uid))
    if not u or u.get("verified") != 2:
        await c.answer("Эту заявку уже рассмотрели", show_alert=True)
        await c.message.edit_reply_markup(reply_markup=None)
        return
    ok = act == "ok"
    await db.ex("UPDATE users SET verified=? WHERE tg_id=?", 1 if ok else -1, u["tg_id"])
    await services.notify(bot, u["tg_id"],
                          "☑️ Верификация пройдена! Теперь рядом с твоим именем галочка." if ok else
                          "❌ Админ не смог подтвердить фото. Можно отправить другое: /verify")
    await c.answer("Готово")
    await c.message.edit_caption(caption=(c.message.caption or "") + ("\n\n☑️ Подтверждено" if ok else "\n\n❌ Отклонено"))
