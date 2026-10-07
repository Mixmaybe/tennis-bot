"""Живой счёт: игроки или судья ведут счёт, любой может смотреть игру в реальном времени."""
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import logic
import services
import ui
from ui import ikb, name

router = Router()
LIVE_LOCK = asyncio.Lock()  # два судьи жмут «+1» одновременно — очки не потеряются


class J(StatesGroup):
    p1 = State()
    p2 = State()   # data: a


# ---------- выбор матча для пары ----------

async def tournament_match_between(a: int, b: int) -> dict | None:
    t = await db.active_tournament()
    if not t or t["status"] != "running":
        return None
    ms = await db.matches("tournament_id=? AND status='scheduled' AND ((p1=? AND p2=?) OR (p1=? AND p2=?))",
                          t["id"], a, b, b, a)
    return ms[0] if ms else None


PAIR_SQL = "((p1=? AND p2=?) OR (p1=? AND p2=?))"
STALE_SEC = 30 * 60  # матч без единого очка 30 минут считается брошенным


async def pair_match(a: int, b: int) -> dict | None:
    """Начатый или уже идущий матч именно этой пары."""
    rows = await db.matches(f"status IN ('setup','live') AND {PAIR_SQL} ORDER BY status='live' DESC, id DESC",
                            a, b, b, a)
    return rows[0] if rows else None


async def busy_player(a: int, b: int) -> dict | None:
    """Идущий матч одного из игроков с КЕМ-ТО ДРУГИМ."""
    rows = await db.matches(f"status='live' AND (p1 IN (?,?) OR p2 IN (?,?)) AND NOT {PAIR_SQL}",
                            a, b, a, b, a, b, b, a)
    return rows[0] if rows else None


async def busy_alert(c: CallbackQuery, other: dict) -> None:
    users = await services.umap()
    await c.answer(f"{services.pair_label(users, other['p1'], other['p2'])} — сейчас идёт этот матч. "
                   "Дождитесь, пока он закончится.", show_alert=True)


async def join_existing(c: CallbackQuery, bot: Bot, mt: dict):
    """Второй игрок (или судья) нажал кнопку позже: подключаем к тому же матчу, а не ругаемся."""
    if mt["status"] == "live":
        await c.answer("Ваш матч уже идёт — открываю табло")
        await open_view(bot, mt["id"], c.from_user.id, "ctl")
    else:
        await c.answer("Подключаю к вашему матчу")
        await ask_server(c.message, mt["id"])


async def setup_pair(m: Message, a: int, b: int):
    users = await services.umap()
    rows = []
    tm = await tournament_match_between(a, b)
    if tm:
        rows.append([(f"🏆 Турнирный матч #{tm['id']} (до {logic.need_wins(tm['best_of'])} побед)", f"lt:{tm['id']}")])
    rows.append([("🤝 Одна партия", f"lf:{a}:{b}:1")])
    rows.append([("🤝 До 2 побед (2–3 партии)", f"lf:{a}:{b}:3")])
    await m.answer(f"🏓 <b>{name(users.get(a))}</b> — <b>{name(users.get(b))}</b>\nКакой матч играете?",
                   reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("lp:"))
async def lp(c: CallbackQuery):
    _, a, b = c.data.split(":")
    await c.answer()
    await setup_pair(c.message, int(a), int(b))


@router.callback_query(F.data.startswith("lf:"))
async def lf(c: CallbackQuery, bot: Bot):
    _, a, b, bo = c.data.split(":")
    a, b = int(a), int(b)
    own = await pair_match(a, b)
    if own:
        if own["status"] == "setup" and own["best_of"] != int(bo):
            await db.ex("UPDATE matches SET best_of=? WHERE id=?", int(bo), own["id"])
        await join_existing(c, bot, own)
        return
    other = await busy_player(a, b)
    if other:
        await busy_alert(c, other)
        return
    mid = await db.ex("INSERT INTO matches(tournament_id, p1, p2, status, best_of, created_at) "
                      "VALUES (NULL, ?, ?, 'setup', ?, ?)", a, b, int(bo), db.now())
    await c.answer()
    await ask_server(c.message, mid)


@router.callback_query(F.data.startswith("lt:"))
async def lt(c: CallbackQuery, bot: Bot):
    mid = int(c.data.split(":")[1])
    mt = await db.match(mid)
    if mt and mt["status"] == "live":
        await join_existing(c, bot, mt)
        return
    if not mt or mt["status"] != "scheduled":
        await c.answer("Этот матч уже сыгран и ждёт подтверждения", show_alert=True)
        return
    other = await busy_player(mt["p1"], mt["p2"])
    if other:
        await busy_alert(c, other)
        return
    await c.answer()
    await ask_server(c.message, mid)


async def ask_server(m: Message, mid: int):
    mt = await db.match(mid)
    users = await services.umap()
    await m.answer("🏓 Кто подаёт первым? (разыграйте подачу)", reply_markup=ikb([
        [(users[mt["p1"]]["name"], f"srv:{mid}:0"), (users[mt["p2"]]["name"], f"srv:{mid}:1")]]))


# ---------- старт ----------

@router.callback_query(F.data.startswith("srv:"))
async def live_start(c: CallbackQuery, bot: Bot):
    _, mid, fs = c.data.split(":")
    mid = int(mid)
    async with LIVE_LOCK:
        mt = await db.match(mid)
        if not mt:
            await c.answer("Матч не найден", show_alert=True)
            return
        if mt["status"] == "live":
            # соперник уже выбрал подачу — просто показываем табло
            await join_existing(c, bot, mt)
            return
        if mt["status"] not in ("scheduled", "setup"):
            await c.answer("Этот матч уже сыгран", show_alert=True)
            return
        other = await busy_player(mt["p1"], mt["p2"])
        if other:
            await busy_alert(c, other)
            return
        starter = c.from_user.id
        players = (mt["p1"], mt["p2"])
        await db.ex("UPDATE matches SET status='live', scorer=? WHERE id=?", starter, mid)
        await db.set_live(mid, {"fs": int(fs), "pts": [], "refs": [] if starter in players else [starter],
                                "t": db.now()})
    note = await services.attach_table(await db.match(mid))
    await c.answer()
    await c.message.edit_text(note)
    await open_view(bot, mid, starter, "ctl")
    users = await services.umap()
    for p in players:
        if p == starter:
            continue
        who = f"👨‍⚖️ <b>{name(users.get(starter))}</b> судит ваш матч и ведёт счёт." if starter not in players \
            else f"▶️ <b>{name(users.get(starter))}</b> начал(а) матч и ведёт счёт."
        await services.notify(bot, p, who + " Счёт ниже обновляется сам 👇")
        await open_view(bot, mid, p, "watch")


async def open_view(bot: Bot, mid: int, chat_id: int, mode: str):
    text, kb = await render(mid, mode)
    try:
        msg = await bot.send_message(chat_id, text, reply_markup=kb)
    except Exception as e:
        logging.warning("open_view %s: %s", chat_id, e)
        return
    await db.ex("INSERT OR REPLACE INTO live_views VALUES (?,?,?,?)", mid, chat_id, msg.message_id, mode)


# ---------- отрисовка ----------

async def render(mid: int, mode: str):
    mt = await db.match(mid)
    users = await services.umap()
    live = mt["live"] or {"fs": 0, "pts": [], "refs": []}
    games, cur = logic.replay(live["pts"])
    n1, n2 = users[mt["p1"]]["name"], users[mt["p2"]]["name"]
    need = logic.need_wins(mt["best_of"])
    gw = [sum(1 for a, b in games if a > b), sum(1 for a, b in games if b > a)]
    winner = logic.games_winner(games, mt["best_of"])
    w = max(len(n1), len(n2), 5)
    kind = "🏆 Турнир" if mt["tournament_id"] else "🤝 Дружеская"
    fmt = "одна партия" if mt["best_of"] == 1 else f"до {need} побед"
    head = f"{kind} · {fmt}\n"
    if live.get("refs"):
        head += "👨‍⚖️ Судья: " + ", ".join(name(users.get(r)) for r in live["refs"]) + "\n"
    if games:
        head += "Партии: " + ", ".join(f"{a}:{b}" for a, b in games) + "\n"

    if winner is not None:
        board = f"{n1:<{w}}  {gw[0]}\n{n2:<{w}}  {gw[1]}"
        text = head + f"<pre>{ui.esc(board)}</pre>\n🏁 <b>Матч окончен!</b> Победил(а) <b>{ui.esc([n1, n2][winner])}</b>"
        if mode == "ctl":
            return text + "\nПроверь счёт и отправь игрокам на подтверждение.", ikb([
                [("✅ Отправить результат", f"lv:done:{mid}")],
                [("↩️ Отменить последнее очко", f"lv:undo:{mid}")]])
        return text + "\n⏳ Ждём, когда результат отправят на подтверждение.", None

    srv = logic.server(live["fs"], len(games), cur)
    ball = [" 🏓" if srv == 0 else "", " 🏓" if srv == 1 else ""]
    board = (f"{'':<{w}}  П  Очки\n"
             f"{n1:<{w}}  {gw[0]}  {cur[0]:>2}{ball[0]}\n"
             f"{n2:<{w}}  {gw[1]}  {cur[1]:>2}{ball[1]}")
    text = head + f"Партия №{len(games) + 1}\n<pre>{ui.esc(board)}</pre>\n🏓 — подаёт · П — выиграно партий"
    if mode == "ctl":
        return text, ikb([
            [(f"+1 {n1}", f"lv:0:{mid}"), (f"+1 {n2}", f"lv:1:{mid}")],
            [("↩️ Отменить", f"lv:undo:{mid}"), ("⏹ Прервать", f"lv:stop:{mid}")],
        ])
    return text + "\n👀 Ты смотришь матч, счёт обновляется сам.", ikb([
        [("🖊 Вести счёт (судить)", f"lj:{mid}"), ("🔕 Не следить", f"lu:{mid}")]])


async def broadcast(bot: Bot, mid: int, final: str | None = None, skip: tuple[int, int] | None = None):
    """Обновить все открытые табло матча. final — итоговый текст, после него табло закрываются."""
    views = await db.q("SELECT * FROM live_views WHERE match_id=?", mid)
    cache = {}
    for v in views:
        if skip == (v["chat_id"], v["message_id"]):
            continue
        if final:
            text, kb = final, None
        else:
            if v["mode"] not in cache:
                cache[v["mode"]] = await render(mid, v["mode"])
            text, kb = cache[v["mode"]]
        try:
            await bot.edit_message_text(text, chat_id=v["chat_id"], message_id=v["message_id"], reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                logging.warning("broadcast %s: %s", v["chat_id"], e)
        except Exception as e:
            logging.warning("broadcast %s: %s", v["chat_id"], e)
    if final:
        await db.ex("DELETE FROM live_views WHERE match_id=?", mid)


# ---------- кнопки табло ----------

@router.callback_query(F.data.startswith("lv:"))
async def live_action(c: CallbackQuery, bot: Bot):
    _, act, mid = c.data.split(":")
    mid = int(mid)
    uid = c.from_user.id
    here = (c.message.chat.id, c.message.message_id)
    async with LIVE_LOCK:
        mt = await db.match(mid)
        if not mt or mt["status"] != "live":
            await c.answer("Матч уже не идёт", show_alert=True)
            return
        view = await db.q1("SELECT * FROM live_views WHERE match_id=? AND chat_id=? AND message_id=?", mid, *here)
        if not view or view["mode"] != "ctl":
            await c.answer("Нажми «🖊 Вести счёт», чтобы судить", show_alert=True)
            return
        players = (mt["p1"], mt["p2"])
        live = mt["live"]
        live.setdefault("refs", [])
        games, _ = logic.replay(live["pts"])
        finished = logic.games_winner(games, mt["best_of"]) is not None

        if act in ("0", "1"):
            if finished:
                await c.answer("Матч уже окончен")
                return
            live["pts"].append(int(act))
            if uid not in players and uid not in live["refs"]:
                live["refs"].append(uid)
        elif act == "undo":
            if not live["pts"]:
                await c.answer("Нечего отменять")
                return
            live["pts"].pop()
        elif act == "stop":
            if mt["tournament_id"]:
                await db.ex("UPDATE matches SET status='scheduled', live=NULL WHERE id=?", mid)
            else:
                await db.ex("DELETE FROM matches WHERE id=?", mid)
            await services.free_table(match_id=mid, bot=bot)
            await c.answer()
            await broadcast(bot, mid, f"⏹ Матч прерван ({name(await db.user(uid))}), счёт не сохранён. Стол освобождён.")
            return
        elif act == "done":
            if not finished:
                await c.answer("Матч ещё не окончен")
                return
            await c.answer()
            referee = uid if uid not in players else (live["refs"][0] if live["refs"] else None)
            if referee:
                text = await services.submit_by_referee(bot, mid, games, referee)
                kb = None
            else:
                text, kb = await services.submit_result(bot, mid, games, uid)
            await broadcast(bot, mid, "🏁 Матч окончен\n" + text.split("\n\n")[0] +
                            "\n\n⏳ Результат отправлен на подтверждение.", skip=here)
            await c.message.edit_text(text, reply_markup=kb)
            return
        live["t"] = db.now()
        await db.set_live(mid, live)
        await db.ex("UPDATE tables SET busy_until=? WHERE match_id=?", db.now() + services.LIVE_MIN * 60, mid)
    await c.answer()
    await broadcast(bot, mid)


@router.callback_query(F.data.startswith("lj:"))
async def live_join(c: CallbackQuery):
    mid = int(c.data.split(":")[1])
    mt = await db.match(mid)
    if not mt or mt["status"] != "live":
        await c.answer("Матч уже не идёт", show_alert=True)
        return
    await db.ex("INSERT OR REPLACE INTO live_views VALUES (?,?,?, 'ctl')", mid, c.message.chat.id, c.message.message_id)
    await c.answer("Теперь ты ведёшь счёт 🖊")
    text, kb = await render(mid, "ctl")
    await c.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("lu:"))
async def live_unwatch(c: CallbackQuery):
    mid = int(c.data.split(":")[1])
    await db.ex("DELETE FROM live_views WHERE match_id=? AND chat_id=? AND message_id=?",
                mid, c.message.chat.id, c.message.message_id)
    await c.answer()
    await c.message.edit_text("🔕 Ты больше не следишь за этим матчем.")


@router.callback_query(F.data.startswith("lw:"))
async def live_watch(c: CallbackQuery, bot: Bot):
    mid = int(c.data.split(":")[1])
    mt = await db.match(mid)
    if not mt or mt["status"] != "live":
        await c.answer("Матч уже закончился", show_alert=True)
        return
    await c.answer()
    await open_view(bot, mid, c.from_user.id, "watch")


async def close_stale(bot: Bot):
    """Закрыть брошенные матчи: живой счёт без очков 30 минут, незапущенные заготовки — тоже."""
    limit = db.now() - STALE_SEC
    for mt in await db.matches("status IN ('live','setup')"):
        last = (mt["live"] or {}).get("t") or mt["created_at"] or 0
        if last >= limit:
            continue
        async with LIVE_LOCK:
            if mt["tournament_id"]:
                await db.ex("UPDATE matches SET status='scheduled', live=NULL WHERE id=? AND status IN ('live','setup')",
                            mt["id"])
            else:
                await db.ex("DELETE FROM matches WHERE id=? AND status IN ('live','setup')", mt["id"])
        await services.free_table(match_id=mt["id"], bot=bot)
        if mt["status"] == "live":
            await broadcast(bot, mt["id"], "⏹ Матч закрыт: 30 минут не было ни одного очка. "
                                           "Счёт не сохранён — если играете, начните заново.")
        logging.info("closed stale match %s", mt["id"])


# ---------- список идущих матчей и судейство чужой игры ----------

async def live_matches_kb() -> tuple[str, object]:
    users = await services.umap()
    ms = await db.matches("status='live' ORDER BY id")
    if not ms:
        return "Сейчас никто не играет со счётом.", None
    lines, rows = ["👀 <b>Идут матчи:</b>"], []
    for m in ms:
        games, cur = logic.replay(m["live"]["pts"] if m["live"] else [])
        label = services.pair_label(users, m["p1"], m["p2"])
        lines.append(f"• {ui.esc(label)} — {ui.games_score(games)}, партия {cur[0]}:{cur[1]}")
        rows.append([(f"👀 Смотреть: {label}", f"lw:{m['id']}")])
    return "\n".join(lines), ikb(rows)


@router.callback_query(F.data == "lwl")
async def live_list(c: CallbackQuery):
    text, kb = await live_matches_kb()
    await c.answer()
    await c.message.answer(text + "\n\nОткрой табло, а если хочешь помочь — нажми там «🖊 Вести счёт».",
                           reply_markup=kb)


@router.callback_query(F.data == "lref")
async def judge_menu(c: CallbackQuery, state: FSMContext):
    """Судить игру других: выбрать пару, которая стоит у стола, или найти игроков по имени."""
    users = await services.umap()
    rows = []
    for tb in await services.tables():
        if tb["busy"] and tb["busy_with"] and not tb["match_id"]:
            rows.append([(f"🏓 {services.pair_label(users, tb['busy_by'], tb['busy_with'])}",
                          f"lp:{tb['busy_by']}:{tb['busy_with']}")])
    live = await db.matches("status='live'")
    if live:
        rows.append([("👀 Подключиться к идущему матчу", "lwl")])
    await state.set_state(J.p1)
    await c.answer()
    await c.message.answer(
        "👨‍⚖️ <b>Судить игру</b>\n\nВыбери пару у стола или <b>напиши имя первого игрока</b>:"
        if rows else "👨‍⚖️ <b>Судить игру</b>\n\n<b>Напиши имя первого игрока</b> (можно часть):",
        reply_markup=ikb(rows) if rows else None)


@router.message(J.p1, F.text, ~F.text.in_(ui.MENU))
async def judge_p1(m: Message):
    found = await services.search_users(m.text, set())
    if not found:
        await m.answer("Никого не нашёл. Игрок должен быть зарегистрирован в боте.")
        return
    await m.answer("Первый игрок:", reply_markup=ikb([[(u["name"], f"jp1:{u['tg_id']}")] for u in found]))


@router.callback_query(F.data.startswith("jp1:"))
async def judge_pick1(c: CallbackQuery, state: FSMContext):
    a = int(c.data.split(":")[1])
    await state.set_state(J.p2)
    await state.update_data(a=a)
    await c.answer()
    await c.message.edit_text(f"Первый игрок: <b>{name(await db.user(a))}</b>\nТеперь <b>напиши имя второго</b>:")


@router.message(J.p2, F.text, ~F.text.in_(ui.MENU))
async def judge_p2(m: Message, state: FSMContext):
    a = (await state.get_data())["a"]
    found = await services.search_users(m.text, {a})
    if not found:
        await m.answer("Никого не нашёл. Попробуй иначе.")
        return
    await m.answer("Второй игрок:", reply_markup=ikb([[(u["name"], f"jp2:{a}:{u['tg_id']}")] for u in found]))


@router.callback_query(F.data.startswith("jp2:"))
async def judge_pick2(c: CallbackQuery, state: FSMContext):
    _, a, b = c.data.split(":")
    await state.clear()
    await c.answer()
    await c.message.edit_reply_markup(reply_markup=None)
    await setup_pair(c.message, int(a), int(b))
