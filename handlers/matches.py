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
    nxt = await services.next_matches(m.chat.id)
    kb = None
    if nxt:
        opp = nxt[0][1]
        kb = ikb([[(f"📨 Пригласить {opp['name']}", f"inv:{opp['tg_id']}")],
                  [("▶️ Начать матч и вести счёт", f"pick:live:t:{nxt[0][0]['id']}")]])
    await m.answer(await services.my_matches_text(m.chat.id), reply_markup=kb)


# ---------- выбор матча ----------

async def choose_match(m: Message, uid: int, purpose: str):
    """purpose: live — вести счёт, res — внести результат."""
    rows = [[(f"🏆 #{mt['id']} vs {opp['name']}", f"pick:{purpose}:t:{mt['id']}")]
            for mt, opp in (await services.next_matches(uid))[:8]]
    rows.append([("🤝 Дружеская игра", f"pick:{purpose}:f")])
    title = "▶️ <b>Ведём счёт.</b> С кем играешь?" if purpose == "live" else "✍️ <b>Внести результат.</b> Какой матч?"
    await m.answer(title, reply_markup=ikb(rows))


@router.message(F.text == ui.B_LIVE)
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
        await opponent_menu(c.message, uid, purpose, state)
        return
    if kind == "u":  # выбран соперник для дружеской игры
        opp = int(parts[3])
        mid = await db.ex("INSERT INTO matches(tournament_id, p1, p2, status, best_of, created_at) "
                          "VALUES (NULL, ?, ?, 'setup', ?, ?)", uid, opp, config.BEST_OF, db.now())
    else:
        mid = int(parts[3])
        mt = await db.match(mid)
        if not mt or mt["status"] != "scheduled" or uid not in (mt["p1"], mt["p2"]):
            await c.message.answer("Этот матч уже сыгран или идёт.")
            return
    if purpose == "live":
        await ask_server(c.message, mid, uid)
    else:
        await state.set_state(S.scores)
        await state.update_data(mid=mid)
        mt = await db.match(mid)
        opp = await db.user(mt["p2"] if mt["p1"] == uid else mt["p1"])
        need = logic.need_wins(mt["best_of"])
        await c.message.answer(
            f"Напиши счёт каждой партии против <b>{name(opp)}</b> — <b>сначала твои очки</b>.\n"
            f"Матч до {need} побед в партиях. Например:\n<code>11:7 9:11 11:5</code>")


async def opponent_menu(m: Message, uid: int, purpose: str, state: FSMContext):
    ready = [u for u in await db.users() if u["tg_id"] != uid and ui.status(u)[0]]
    rows = [[(f"{u['name']} ({ui.status(u)[1]})", f"pick:{purpose}:u:{u['tg_id']}")] for u in ready[:8]]
    await state.set_state(S.find_opp)
    await state.update_data(purpose=purpose)
    await m.answer("🤝 С кем играешь? Выбери из тех, кто готов, или <b>напиши часть имени</b> соперника:",
                   reply_markup=ikb(rows) if rows else None)


async def search_users(text: str, exclude: set[int]) -> list[dict]:
    q = text.strip().lower().lstrip("@")
    return [u for u in await db.users() if u["tg_id"] not in exclude
            and (q in u["name"].lower() or q in (u["username"] or "").lower())][:10]


@router.message(S.find_opp, F.text, ~F.text.in_(ui.MENU))
async def find_opp(m: Message, state: FSMContext):
    purpose = (await state.get_data())["purpose"]
    found = await search_users(m.text, {m.chat.id})
    if not found:
        await m.answer("Никого не нашёл 🤷 Попробуй иначе. Соперник должен быть зарегистрирован в боте (/qr).")
        return
    await m.answer("Кого из них?", reply_markup=ikb(
        [[(f"{u['name']} · {u['department']}", f"pick:{purpose}:u:{u['tg_id']}")] for u in found]))


# ---------- живой счёт ----------

async def ask_server(m: Message, mid: int, uid: int):
    mt = await db.match(mid)
    users = await services.umap()
    await m.answer("🏓 Кто подаёт первым? (разыграйте подачу)", reply_markup=ikb([
        [(users[mt["p1"]]["name"], f"srv:{mid}:0"), (users[mt["p2"]]["name"], f"srv:{mid}:1")]]))


@router.callback_query(F.data.startswith("srv:"))
async def live_start(c: CallbackQuery):
    _, mid, fs = c.data.split(":")
    mid = int(mid)
    mt = await db.match(mid)
    if mt["status"] not in ("scheduled", "setup"):
        await c.answer("Матч уже идёт или сыгран", show_alert=True)
        return
    await db.ex("UPDATE matches SET status='live' WHERE id=?", mid)
    await db.set_live(mid, {"fs": int(fs), "pts": []})
    users = await services.umap()
    table = await services.occupy_free_table(
        c.from_user.id, 40, f"{users[mt['p1']]['name']} — {users[mt['p2']]['name']}", mid)
    await c.answer()
    note = f"Стол {table} отмечен занятым ✅" if table and config.TABLES > 1 else \
        "Стол отмечен занятым ✅" if table else "⚠️ Все столы отмечены занятыми, но счёт ведём."
    await c.message.edit_text(note)
    text, kb = await live_view(mid)
    await c.message.answer(text, reply_markup=kb)


async def live_view(mid: int):
    mt = await db.match(mid)
    users = await services.umap()
    live = mt["live"]
    games, cur = logic.replay(live["pts"])
    n1, n2 = users[mt["p1"]]["name"], users[mt["p2"]]["name"]
    need = logic.need_wins(mt["best_of"])
    gw = [sum(1 for a, b in games if a > b), sum(1 for a, b in games if b > a)]
    winner = logic.games_winner(games, mt["best_of"])
    w = max(len(n1), len(n2), 5)
    kind = "🏆 Турнир" if mt["tournament_id"] else "🤝 Дружеская"
    head = f"{kind} · до {need} побед\n"
    if games:
        head += "Партии: " + ", ".join(f"{a}:{b}" for a, b in games) + "\n"
    if winner is not None:
        board = f"{n1:<{w}}  {gw[0]}\n{n2:<{w}}  {gw[1]}"
        text = head + f"<pre>{ui.esc(board)}</pre>\n🏁 <b>Матч окончен!</b> Победил(а) <b>{ui.esc([n1, n2][winner])}</b>"
        return text, ikb([[("✅ Отправить результат", f"lv:done:{mid}")],
                          [("↩️ Отменить последнее очко", f"lv:undo:{mid}")]])
    srv = logic.server(live["fs"], len(games), cur)
    ball = [" 🏓" if srv == 0 else "", " 🏓" if srv == 1 else ""]
    board = (f"{'':<{w}}  П  Очки\n"
             f"{n1:<{w}}  {gw[0]}  {cur[0]:>2}{ball[0]}\n"
             f"{n2:<{w}}  {gw[1]}  {cur[1]:>2}{ball[1]}")
    text = head + f"Партия №{len(games) + 1}\n<pre>{ui.esc(board)}</pre>\n🏓 — подаёт"
    return text, ikb([
        [(f"+1 {n1}", f"lv:0:{mid}"), (f"+1 {n2}", f"lv:1:{mid}")],
        [("↩️ Отменить", f"lv:undo:{mid}"), ("⏹ Прервать", f"lv:stop:{mid}")],
    ])


@router.callback_query(F.data.startswith("lv:"))
async def live_action(c: CallbackQuery, bot: Bot):
    _, act, mid = c.data.split(":")
    mid = int(mid)
    mt = await db.match(mid)
    if not mt or mt["status"] != "live":
        await c.answer("Матч уже не идёт", show_alert=True)
        return
    live = mt["live"]
    if act in ("0", "1"):
        games, _ = logic.replay(live["pts"])
        if logic.games_winner(games, mt["best_of"]) is not None:
            await c.answer("Матч уже окончен")
            return
        live["pts"].append(int(act))
    elif act == "undo":
        if not live["pts"]:
            await c.answer("Нечего отменять")
            return
        live["pts"].pop()
    elif act == "stop":
        await services.free_table(match_id=mid)
        if mt["tournament_id"]:
            await db.ex("UPDATE matches SET status='scheduled', live=NULL WHERE id=?", mid)
        else:
            await db.ex("DELETE FROM matches WHERE id=?", mid)
        await c.answer()
        await c.message.edit_text("⏹ Матч прерван, счёт не сохранён. Стол освобождён.")
        return
    elif act == "done":
        games, _ = logic.replay(live["pts"])
        text, kb = await services.submit_result(bot, mid, games, c.from_user.id)
        await c.answer()
        await c.message.edit_text(text, reply_markup=kb)
        return
    await db.set_live(mid, live)
    # продлеваем занятость стола, пока идёт счёт
    await db.ex("UPDATE tables SET busy_until=? WHERE match_id=?", db.now() + 20 * 60, mid)
    text, kb = await live_view(mid)
    await c.answer()
    await c.message.edit_text(text, reply_markup=kb)


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
    found = await search_users(m.text, {mt["p1"], mt["p2"]})
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
    if not await services.can_confirm(mt, c.from_user.id):
        await c.answer("Подтвердить может только судья матча или админ", show_alert=True)
        return
    if act == "cf":
        await services.confirm(bot, mt, c.from_user.id)
        mark = "✅ Подтверждено"
    else:
        await services.reject(bot, mt, c.from_user.id)
        mark = "❌ Отклонено"
    await c.answer(mark)
    await c.message.edit_text(c.message.html_text + f"\n\n<b>{mark}</b>")
