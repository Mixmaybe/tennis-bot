"""Мои матчи, живой счёт, ввод результата, судья, подтверждение."""
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import db
import logic
import services
import ui
from handlers import live
from ui import ikb, name

router = Router()


class S(StatesGroup):
    scores = State()      # ждём ввод счёта; data: mid
    find_opp = State()    # поиск соперника; data: purpose
    find_ref = State()    # поиск судьи; data: mid


# ---------- мои матчи ----------

@router.message(F.text == ui.B_MY)
async def my_matches(m: Message, state: FSMContext):
    await state.clear()
    if not await db.user(m.chat.id):
        await m.answer("Сначала зарегистрируйся: /start")
        return
    uid = m.chat.id
    rows = []
    # сейчас играю — можно завершить без результата / освободить стол
    for mt in await db.matches("status='live' AND (p1=? OR p2=?)", uid, uid):
        rows.append([("⏹ Завершить идущий матч без результата", f"lend:{mt['id']}")])
    for tb in await services.tables():
        if tb["busy"] and not tb["match_id"] and uid in (tb["busy_by"], tb["busy_with"]) and tb["busy_with"]:
            a, b = tb["busy_by"], tb["busy_with"]
            rows.append([("✍️ Внести результат", f"res2:{a}:{b}"), ("🏁 Закончили", f"bkx:{a}:{b}")])
    nxt = await services.next_matches(uid)
    if nxt:
        opp = nxt[0][1]
        rows += [[(f"📨 Пригласить {opp['name']}", f"inv:{opp['tg_id']}")],
                 [("🏆 Начать турнирный матч и вести счёт", f"pick:live:t:{nxt[0][0]['id']}")]]
    await m.answer(await services.my_matches_text(uid), reply_markup=ikb(rows) if rows else None)


# ---------- выбор матча ----------

async def choose_match(m: Message, uid: int, purpose: str):
    """purpose: live — вести счёт турнирного матча, res — внести результат."""
    nxt = (await services.next_matches(uid))[:8]
    rows = [[(f"🏆 #{mt['id']} vs {opp['name']}", f"pick:{purpose}:t:{mt['id']}")] for mt, opp in nxt]
    if purpose == "res":
        rows.append([("🤝 Дружеская игра", "pick:res:f")])
        await m.answer("✍️ <b>Внести результат.</b> Какой матч сыграли?", reply_markup=ikb(rows))
        return
    rows.append([("👨‍⚖️ Судить турнирный матч других", "lref")])
    n = len(await db.matches("status='live'"))
    if n:
        rows.append([(f"👀 Смотреть идущие матчи ({n})", "lwl")])
    text = ("🏆 <b>Живой счёт — только для турнирных матчей.</b>\n"
            + ("Выбери свой матч, чтобы вести счёт кнопками «+1», или стань судьёй чужого:" if nxt else
               "У тебя сейчас нет несыгранных турнирных матчей, но можно судить чужой:")
            + f"\n\n🤝 Сыграли дружескую игру? Нажми «{ui.B_RES}» и впиши счёт.")
    await m.answer(text, reply_markup=ikb(rows))


@router.message(F.text.in_({ui.B_LIVE, *ui.OLD_BUTTONS}))
async def live_cmd(m: Message, state: FSMContext):
    await state.clear()
    if await db.user(m.chat.id):
        await choose_match(m, m.chat.id, "live")


@router.message(F.text == ui.B_RES)
async def res_cmd(m: Message, state: FSMContext):
    await state.clear()
    if await db.user(m.chat.id):
        await choose_match(m, m.chat.id, "res")


@router.callback_query(F.data.startswith("pick:"))
async def pick(c: CallbackQuery, state: FSMContext):
    parts = c.data.split(":")
    purpose, kind = parts[1], parts[2]
    uid = c.from_user.id
    await c.answer()
    if kind == "f":
        await opponent_menu(c.message, uid, "res", state)
        return
    if kind == "u":  # выбран соперник для дружеской игры — только внести результат
        await friendly_result(c.message, state, uid, int(parts[3]))
        return
    mid = int(parts[3])
    mt = await db.match(mid)
    if not mt or mt["status"] != "scheduled" or uid not in (mt["p1"], mt["p2"]):
        await c.message.answer("Этот матч уже сыгран или идёт.")
        return
    if purpose == "live":
        await live.ask_server(c.message, mid)
    else:
        await ask_scores(c.message, state, mid, uid)


@router.callback_query(F.data.startswith("res2:"))
async def res_for_pair(c: CallbackQuery, state: FSMContext):
    """Кнопка «Внести результат» из брони стола: соперник — второй из пары."""
    _, a, b = c.data.split(":")
    a, b = int(a), int(b)
    uid = c.from_user.id
    await c.answer()
    await friendly_result(c.message, state, uid, b if uid == a else a)


async def friendly_result(m: Message, state: FSMContext, uid: int, opp: int):
    mid = await db.ex("INSERT INTO matches(tournament_id, p1, p2, status, best_of, created_at) "
                      "VALUES (NULL, ?, ?, 'setup', ?, ?)", uid, opp, config.BEST_OF, db.now())
    await ask_scores(m, state, mid, uid)


async def ask_scores(m: Message, state: FSMContext, mid: int, uid: int):
    await state.set_state(S.scores)
    await state.update_data(mid=mid)
    mt = await db.match(mid)
    opp = await db.user(mt["p2"] if mt["p1"] == uid else mt["p1"])
    need = logic.need_wins(mt["best_of"])
    fmt = (f"Турнирный матч — до {need} побед в партиях." if mt["tournament_id"]
           else "Можно одну партию или матч до 2 побед.")
    await m.answer(
        f"Напиши счёт каждой партии против <b>{name(opp)}</b> — <b>сначала твои очки</b>.\n"
        f"{fmt} Например:\n<code>11:7 9:11 11:5</code> или <code>11:8</code>")


async def opponent_menu(m: Message, uid: int, purpose: str, state: FSMContext):
    ready = [u for u in await db.users() if u["tg_id"] != uid and ui.status(u)[0]]
    rows = [[(f"{u['name']} ({ui.status(u)[1]})", f"pick:{purpose}:u:{u['tg_id']}")] for u in ready[:8]]
    await state.set_state(S.find_opp)
    await state.update_data(purpose=purpose)
    await m.answer("🤝 С кем играешь? Выбери из тех, кто готов, или <b>напиши часть имени</b> соперника:",
                   reply_markup=ikb(rows) if rows else None)


@router.message(S.find_opp, F.text, ~F.text.in_(ui.MENU))
async def find_opp(m: Message, state: FSMContext):
    purpose = (await state.get_data())["purpose"]
    found = await services.search_users(m.text, {m.chat.id})
    if not found:
        await m.answer("Никого не нашёл 🤷 Попробуй иначе. Соперник должен быть зарегистрирован в боте (/qr).")
        return
    await m.answer("Кого из них?", reply_markup=ikb(
        [[(f"{u['name']} · {u['department']}", f"pick:{purpose}:u:{u['tg_id']}")] for u in found]))


# ---------- ручной ввод счёта ----------

@router.message(S.scores, F.text, ~F.text.in_(ui.MENU))
async def enter_scores(m: Message, state: FSMContext, bot: Bot):
    mid = (await state.get_data())["mid"]
    mt = await db.match(mid)
    if not mt or mt["status"] not in ("scheduled", "setup"):
        await state.clear()
        await m.answer("Этот матч уже сыгран.")
        return
    games = logic.parse_scores(m.text)
    if not mt["tournament_id"]:
        best_of = 1 if len(games) == 1 else 3
        if best_of != mt["best_of"]:
            await db.ex("UPDATE matches SET best_of=? WHERE id=?", best_of, mid)
            mt["best_of"] = best_of
    err = logic.match_error(games, mt["best_of"])
    if err:
        await m.answer(f"⚠️ {err}\n\nНапиши ещё раз, сначала твои очки: <code>11:7 9:11 11:5</code>")
        return
    if m.chat.id == mt["p2"]:
        games = [(b, a) for a, b in games]
    await state.clear()
    text, kb = await services.submit_result(bot, mid, games, m.chat.id)
    await m.answer(text, reply_markup=kb)


# ---------- судья ----------

@router.callback_query(F.data.startswith("ref:"))
async def choose_ref(c: CallbackQuery, bot: Bot):
    _, mid, ref = c.data.split(":")
    mt = await db.match(int(mid))
    if mt["status"] != "pending":
        await c.answer("Результат уже обработан", show_alert=True)
        return
    await services.ask_referee(bot, int(mid), int(ref))
    who = "админам" if ref == "0" else name(await db.user(int(ref)))
    await c.answer()
    await c.message.edit_text(c.message.html_text.split("\n\n👨‍⚖️")[0] + f"\n\n⏳ Отправил на подтверждение: {who}")


@router.callback_query(F.data.startswith("refs:"))
async def ref_search(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.find_ref)
    await state.update_data(mid=int(c.data.split(":")[1]))
    await c.answer()
    await c.message.answer("Напиши часть имени судьи:")


@router.message(S.find_ref, F.text, ~F.text.in_(ui.MENU))
async def find_ref(m: Message, state: FSMContext):
    mid = (await state.get_data())["mid"]
    mt = await db.match(mid)
    found = await services.search_users(m.text, {mt["p1"], mt["p2"]})
    if not found:
        await m.answer("Никого не нашёл. Судья тоже должен быть зарегистрирован в боте.")
        return
    await state.clear()
    await m.answer("Кто судья?", reply_markup=ikb([[(u["name"], f"ref:{mid}:{u['tg_id']}")] for u in found]))


# ---------- подтверждение ----------

@router.callback_query(F.data.regexp(r"^(cf|rj):\d+$"))
async def confirm_or_reject(c: CallbackQuery, bot: Bot):
    act, mid = c.data.split(":")
    mt = await db.match(int(mid))
    if not mt or mt["status"] != "pending":
        await c.answer("Этот результат уже обработан", show_alert=True)
        await c.message.edit_reply_markup(reply_markup=None)
        return
    uid = c.from_user.id
    if not await services.can_confirm(mt, uid):
        if mt["need_players"] and uid in (mt["p1"], mt["p2"]):
            msg = "Ты уже подтвердил(а), ждём соперника"
        elif mt["need_players"]:
            msg = "Подтвердить могут только игроки этого матча"
        else:
            msg = "Подтвердить может только судья матча или админ"
        await c.answer(msg, show_alert=True)
        return
    if act == "cf" and mt["need_players"] and uid in (mt["p1"], mt["p2"]):
        both = await services.player_confirm(bot, mt, uid)
        mark = "✅ Подтверждено" if both else "✅ Ты подтвердил(а), ждём соперника"
    elif act == "cf":
        await services.confirm(bot, mt, uid)
        mark = "✅ Подтверждено"
    else:
        await services.reject(bot, mt, uid)
        mark = "❌ Отклонено"
    await c.answer(mark)
    await c.message.edit_text(c.message.html_text + f"\n\n<b>{mark}</b>")
