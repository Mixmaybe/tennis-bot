"""Турнирная таблица и команды админа."""
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
import db
import logic
import services
import ui
from ui import ikb, name

router = Router()


# ---------- таблица ----------

@router.message(F.text == ui.B_STAND)
async def standings_cmd(m: Message, state: FSMContext):
    await state.clear()
    t = await db.active_tournament() or await db.last_tournament()
    if not t:
        total = await db.q1("SELECT COUNT(*) c, SUM(wants_tournament) w FROM users")
        await m.answer(f"Турнир пока не создан.\nЗарегистрировано игроков: {total['c']}, "
                       f"хотят участвовать: {total['w'] or 0}.",
                       reply_markup=ikb([[("📜 Как будет считаться", "rules")]]))
        return
    entry = await db.player_entry(t["id"], m.chat.id)
    leagues = [r["league"] for r in await db.q(
        "SELECT DISTINCT league FROM tournament_players WHERE tournament_id=? ORDER BY league", t["id"])]
    league = entry["league"] if entry else (leagues[0] if leagues else None)
    text, kb = await standings_view(t, league, leagues)
    await m.answer(text, reply_markup=kb)


async def standings_view(t: dict, league: str | None, leagues: list[str]):
    if t["status"] == "registration" or not league:
        players = await db.tournament_players(t["id"])
        lines = [f"🏆 <b>{ui.esc(t['name'])}</b> — идёт регистрация ({len(players)} чел.)"]
        for code in leagues:
            lines.append(f"\n<b>{ui.league_name(code)}</b>:")
            lines += [f"• {ui.full(p)} ({logic.LEVELS.get(p['level'], '')})" for p in players if p["t_league"] == code]
        return "\n".join(lines), ikb([[("📜 Правила", "rules")]])
    text = await services.standings_text(t, league)
    if t["status"] == "finished":
        text = "🏁 Турнир завершён\n" + text
    rows = [[(("• " if c == league else "") + ui.league_name(c), f"stl:{t['id']}:{c}") for c in leagues]] \
        if len(leagues) > 1 else []
    rows.append([("📜 Как считается", "rules"), ("🔄 Обновить", f"stl:{t['id']}:{league}")])
    return text, ikb(rows)


@router.callback_query(F.data.startswith("stl:"))
async def standings_league(c: CallbackQuery):
    _, tid, league = c.data.split(":")
    t = await db.q1("SELECT * FROM tournaments WHERE id=?", int(tid))
    leagues = [r["league"] for r in await db.q(
        "SELECT DISTINCT league FROM tournament_players WHERE tournament_id=? ORDER BY league", t["id"])]
    text, kb = await standings_view(t, league, leagues)
    await c.answer()
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass  # ничего не изменилось


@router.callback_query(F.data == "rules")
async def rules(c: CallbackQuery):
    t = await db.active_tournament()
    best_of = t["best_of"] if t else config.BEST_OF
    await c.answer()
    await c.message.answer(logic.RULES_TEXT.format(need=logic.need_wins(best_of)))


# ---------- админ ----------

async def admin_only(m: Message) -> bool:
    if db.is_admin(await db.user(m.chat.id)) or m.chat.id in config.ADMIN_IDS:
        return True
    await m.answer("Эта команда только для админов.")
    return False


async def resolve(arg: str) -> dict | None:
    arg = (arg or "").strip().lstrip("@")
    if arg.isdigit():
        return await db.user(int(arg))
    return await db.q1("SELECT * FROM users WHERE lower(username)=lower(?)", arg)


@router.message(Command("admin"))
async def admin_help(m: Message):
    if not await admin_only(m):
        return
    await m.answer(
        "👑 <b>Команды админа</b>\n\n"
        "/newtournament Название — создать турнир (открыть регистрацию)\n"
        "/starttournament — разбить по лигам и создать расписание «каждый с каждым»\n"
        "/finishtournament — завершить и объявить победителей\n"
        "/pending — результаты, ждущие подтверждения\n"
        "/players — все игроки\n"
        "/setleague @user A|B|C — перевести игрока в лигу (A — высшая, B — первая, C — открытая)\n"
        "/removeplayer @user — убрать из турнира\n"
        "/walkover ID_матча @победитель — тех. победа (неявка)\n"
        "/addadmin @user, /deladmin @user — доверенные судьи-админы\n"
        "/broadcast текст — сообщение всем\n\n"
        "Вместо @user можно указать Telegram ID."
    )


@router.message(Command("newtournament"))
async def new_tournament(m: Message, command: CommandObject, bot: Bot):
    if not await admin_only(m):
        return
    if await db.active_tournament():
        await m.answer("Уже есть активный турнир. Сначала /finishtournament")
        return
    title = (command.args or "Офисный турнир").strip()[:60]
    tid = await db.ex("INSERT INTO tournaments(name, status, best_of, created_at) VALUES (?, 'registration', ?, ?)",
                      title, config.BEST_OF, db.now())
    t = await db.q1("SELECT * FROM tournaments WHERE id=?", tid)
    wanting = await db.q("SELECT * FROM users WHERE wants_tournament=1")
    for u in wanting:
        await services.add_to_tournament(bot, t, u["tg_id"], u["league"] or logic.league_for(u["seed"]))
    for u in await db.users():
        text = (f"🏆 Открыта регистрация на турнир <b>{ui.esc(title)}</b>!\n"
                + ("Ты уже в списке участников ✅" if u["wants_tournament"]
                   else "Хочешь участвовать? Нажми «👤 Профиль» → «🏆 Турнир / лига»."))
        await services.notify(bot, u["tg_id"], text)
    await m.answer(f"Турнир «{ui.esc(title)}» создан, участников: {len(wanting)}.\n"
                   "Когда все запишутся — /starttournament")


@router.message(Command("starttournament"))
async def start_tournament(m: Message, bot: Bot):
    if not await admin_only(m):
        return
    t = await db.active_tournament()
    if not t or t["status"] != "registration":
        await m.answer("Нет турнира в стадии регистрации. Создай: /newtournament Название")
        return
    await m.answer("🏁 Запускаю…\n" + await services.start_tournament(bot, t))


@router.message(Command("finishtournament"))
async def finish_tournament(m: Message, bot: Bot):
    if not await admin_only(m):
        return
    t = await db.active_tournament()
    if not t:
        await m.answer("Нет активного турнира.")
        return
    await db.ex("UPDATE tournaments SET status='finished' WHERE id=?", t["id"])
    await db.ex("DELETE FROM matches WHERE tournament_id=? AND status IN ('scheduled','live')", t["id"])
    users = await services.umap()
    lines = [f"🏁 Турнир <b>{ui.esc(t['name'])}</b> завершён!\n"]
    medals = ["🥇", "🥈", "🥉"]
    for r in await db.q("SELECT DISTINCT league FROM tournament_players WHERE tournament_id=? ORDER BY league", t["id"]):
        players = [p["tg_id"] for p in await db.tournament_players(t["id"], r["league"])]
        done = await db.matches("tournament_id=? AND league=? AND status='confirmed'", t["id"], r["league"])
        top = logic.standings(players, done, t["best_of"])[:3]
        lines.append(f"<b>{ui.league_name(r['league'])}</b>")
        lines += [f"{medals[i]} {name(users.get(pid))} — {s['mp']} очк." for i, (pid, s) in enumerate(top)]
        lines.append("")
    text = "\n".join(lines)
    for uid in users:
        await services.notify(bot, uid, text)


@router.message(Command("pending"))
async def pending(m: Message):
    if not await admin_only(m):
        return
    ms = await db.matches("status='pending' AND tournament_id IS NOT NULL ORDER BY played_at")
    if not ms:
        await m.answer("Нет результатов на подтверждение ✅")
        return
    users = await services.umap()
    for mt in ms[:20]:
        ref = "любой админ" if mt["referee_id"] == 0 else name(users.get(mt["referee_id"])) if mt["referee_id"] else "не выбран"
        await m.answer(services.score_card(mt, users) + f"\nСудья: {ref}",
                       reply_markup=ikb([[("✅ Подтвердить", f"cf:{mt['id']}"), ("❌ Отклонить", f"rj:{mt['id']}")]]))


@router.message(Command("players"))
async def players(m: Message):
    if not await admin_only(m):
        return
    us = await db.users()
    lines = [f"Игроков: {len(us)}"]
    for u in us:
        crown = "👑 " if db.is_admin(u) else ""
        uname = f" @{u['username']}" if u["username"] else ""
        lines.append(f"{crown}{ui.full(u)}{ui.esc(uname)} — {logic.LEVELS.get(u['level'], '')}, "
                     f"посев {u['seed']}, {ui.league_name(u['league']) if u['wants_tournament'] else 'без турнира'}")
    await m.answer("\n".join(lines)[:4000])


@router.message(Command("setleague"))
async def set_league(m: Message, command: CommandObject, bot: Bot):
    if not await admin_only(m):
        return
    args = (command.args or "").split()
    if len(args) != 2 or args[1].upper() not in logic.LEAGUE_NAMES:
        await m.answer("Формат: /setleague @user A|B|C")
        return
    u, code = await resolve(args[0]), args[1].upper()
    if not u:
        await m.answer("Игрок не найден.")
        return
    await db.ex("UPDATE users SET league=?, wants_tournament=1 WHERE tg_id=?", code, u["tg_id"])
    t = await db.active_tournament()
    if t:
        await services.remove_from_tournament(t, u["tg_id"])
        await services.add_to_tournament(bot, t, u["tg_id"], code)
    await m.answer(f"{name(u)} → {ui.league_name(code)} ✅"
                   + ("\nНесыгранные матчи в старой лиге удалены, в новой созданы." if t and t["status"] == "running" else ""))
    await services.notify(bot, u["tg_id"], f"Админ перевёл тебя в {ui.league_name(code)}.")


@router.message(Command("removeplayer"))
async def remove_player(m: Message, command: CommandObject):
    if not await admin_only(m):
        return
    u, t = await resolve(command.args), await db.active_tournament()
    if not u or not t:
        await m.answer("Игрок или активный турнир не найден. Формат: /removeplayer @user")
        return
    await services.remove_from_tournament(t, u["tg_id"])
    await db.ex("UPDATE users SET wants_tournament=0 WHERE tg_id=?", u["tg_id"])
    await m.answer(f"{name(u)} убран из турнира, несыгранные матчи удалены.")


@router.message(Command("walkover"))
async def walkover(m: Message, command: CommandObject, bot: Bot):
    if not await admin_only(m):
        return
    args = (command.args or "").split()
    if len(args) != 2 or not args[0].lstrip("#").isdigit():
        await m.answer("Формат: /walkover ID_матча @победитель")
        return
    mt, u = await db.match(int(args[0].lstrip("#"))), await resolve(args[1])
    if not mt or not u or u["tg_id"] not in (mt["p1"], mt["p2"]) or not mt["tournament_id"]:
        await m.answer("Матч не найден или этот игрок в нём не участвует.")
        return
    await db.save_scores(mt["id"], [], u["tg_id"], "pending", walkover=1, reported_by=m.chat.id)
    await services.confirm(bot, await db.match(mt["id"]), m.chat.id)
    await m.answer(f"Тех. победа {name(u)} в матче #{mt['id']} ✅")


@router.message(Command("addadmin", "deladmin"))
async def set_admin(m: Message, command: CommandObject, bot: Bot):
    if not await admin_only(m):
        return
    u = await resolve(command.args)
    if not u:
        await m.answer("Игрок не найден (он должен сначала зарегистрироваться в боте).")
        return
    flag = int(command.command == "addadmin")
    await db.ex("UPDATE users SET is_admin=? WHERE tg_id=?", flag, u["tg_id"])
    await m.answer(f"{name(u)}: {'теперь админ 👑' if flag else 'больше не админ'}")
    if flag:
        await services.notify(bot, u["tg_id"], "👑 Тебе выданы права админа: можешь подтверждать результаты. /admin")


@router.message(Command("broadcast"))
async def broadcast(m: Message, command: CommandObject, bot: Bot):
    if not await admin_only(m) or not command.args:
        return
    us = await db.users()
    for u in us:
        await services.notify(bot, u["tg_id"], f"📢 {ui.esc(command.args)}")
    await m.answer(f"Отправлено {len(us)} игрокам.")
