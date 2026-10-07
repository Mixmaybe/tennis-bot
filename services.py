"""Операции, которые используют несколько обработчиков."""
import asyncio
import json
import logging

from aiogram import Bot

import db
import logic
import ui
from ui import ikb, name


async def notify(bot: Bot, uid: int, text: str, kb=None):
    try:
        await bot.send_message(uid, text, reply_markup=kb)
    except Exception as e:  # пользователь заблокировал бота и т.п.
        logging.warning("notify %s failed: %s", uid, e)


async def umap() -> dict[int, dict]:
    return {u["tg_id"]: u for u in await db.users()}


# ---------- турнир ----------

async def add_to_tournament(bot: Bot, t: dict, uid: int, league: str):
    """Добавить игрока. Если турнир уже идёт — сразу создать ему матчи со всеми в лиге."""
    if await db.player_entry(t["id"], uid):
        return
    if t["status"] == "running":
        # лигу могли слить с соседней при старте — берём ближайшую существующую
        existing = {r["league"] for r in await db.q(
            "SELECT DISTINCT league FROM tournament_players WHERE tournament_id=?", t["id"])}
        if existing and league not in existing:
            order = [code for code, _, _ in logic.LEAGUES]
            league = min(existing, key=lambda c: abs(order.index(c) - order.index(league)))
    await db.ex("INSERT INTO tournament_players VALUES (?,?,?)", t["id"], uid, league)
    if t["status"] != "running":
        return
    others = [p["tg_id"] for p in await db.tournament_players(t["id"], league) if p["tg_id"] != uid]
    for opp in others:
        await create_match(t, league, uid, opp)
        await notify(bot, opp, f"🆕 В {ui.league_name(league)} новый участник: <b>{name(await db.user(uid))}</b>. "
                               f"У вас добавился матч — см. «{ui.B_MY}».")


async def remove_from_tournament(t: dict, uid: int):
    await db.ex("DELETE FROM tournament_players WHERE tournament_id=? AND tg_id=?", t["id"], uid)
    await db.ex("DELETE FROM matches WHERE tournament_id=? AND (p1=? OR p2=?) AND status IN ('scheduled','live')",
                t["id"], uid, uid)


async def create_match(t: dict, league: str, a: int, b: int):
    await db.ex("INSERT INTO matches(tournament_id, league, p1, p2, status, best_of, created_at) "
                "VALUES (?,?,?,?, 'scheduled', ?, ?)", t["id"], league, a, b, t["best_of"], db.now())


async def start_tournament(bot: Bot, t: dict) -> str:
    players = await db.tournament_players(t["id"])
    if len(players) < 2:
        return "Нужно минимум 2 участника."
    by_league: dict[str, list[int]] = {}
    for p in players:
        by_league.setdefault(p["t_league"], []).append(p["tg_id"])
    by_league = logic.merge_small_leagues(by_league)
    lines = []
    for league, ids in by_league.items():
        for uid in ids:
            await db.ex("UPDATE tournament_players SET league=? WHERE tournament_id=? AND tg_id=?", league, t["id"], uid)
        for a, b in logic.round_robin_pairs(ids):
            await create_match(t, league, a, b)
        lines.append(f"{ui.league_name(league)}: {len(ids)} игроков, {len(ids) * (len(ids) - 1) // 2} матчей")
    await db.ex("UPDATE tournaments SET status='running' WHERE id=?", t["id"])
    t["status"] = "running"
    for p in players:
        text = await my_matches_text(p["tg_id"])
        await notify(bot, p["tg_id"], f"🏁 Турнир <b>{ui.esc(t['name'])}</b> начался!\n\n{text}")
    return "\n".join(lines)


async def standings_text(t: dict, league: str, users: dict | None = None) -> str:
    users = users or await umap()
    players = [p["tg_id"] for p in await db.tournament_players(t["id"], league)]
    done = await db.matches("tournament_id=? AND league=? AND status='confirmed'", t["id"], league)
    total = len(await db.matches("tournament_id=? AND league=?", t["id"], league))
    rows = logic.standings(players, done, t["best_of"])
    lines = [f"{'#':>2} {'Игрок':<11} И В П  О Парт  Мячи"]
    for i, (pid, s) in enumerate(rows, 1):
        nm = users[pid]["name"] if pid in users else "?"
        nm = nm if len(nm) <= 11 else nm[:10] + "…"
        diff = s["pw"] - s["pl"]
        lines.append(f"{i:>2} {nm:<11} {s['p']} {s['w']} {s['l']} {s['mp']:>2} {s['gw']:>2}:{s['gl']:<2} {diff:+4d}")
    head = f"🏆 <b>{ui.esc(t['name'])}</b> — {ui.league_name(league)}\nСыграно матчей: {len(done)} из {total}\n"
    return head + "<pre>" + ui.esc("\n".join(lines)) + "</pre>"


async def tournament_match_between(a: int, b: int) -> dict | None:
    t = await db.active_tournament()
    if not t or t["status"] != "running":
        return None
    ms = await db.matches("tournament_id=? AND status='scheduled' AND ((p1=? AND p2=?) OR (p1=? AND p2=?))",
                          t["id"], a, b, b, a)
    return ms[0] if ms else None


async def next_matches(uid: int) -> list[tuple[dict, dict]]:
    """Несыгранные турнирные матчи игрока, отсортированные: сначала те, чей соперник готов играть."""
    t = await db.active_tournament()
    if not t or t["status"] != "running":
        return []
    ms = await db.matches("tournament_id=? AND (p1=? OR p2=?) AND status='scheduled'", t["id"], uid, uid)
    users = await umap()
    played = {}
    for m in await db.matches("tournament_id=? AND status='confirmed'", t["id"]):
        for p in (m["p1"], m["p2"]):
            played[p] = played.get(p, 0) + 1
    res = []
    for m in ms:
        opp = users.get(m["p2"] if m["p1"] == uid else m["p1"])
        if opp:
            res.append((m, opp))
    res.sort(key=lambda x: (ui.status(x[1])[2], played.get(x[1]["tg_id"], 0)))
    return res


async def my_matches_text(uid: int) -> str:
    t = await db.active_tournament() or await db.last_tournament()
    users = await umap()
    parts = []
    if t:
        entry = await db.player_entry(t["id"], uid)
        if entry:
            parts.append(f"🏆 <b>{ui.esc(t['name'])}</b> — {ui.league_name(entry['league'])}")
            if t["status"] == "registration":
                parts.append("Турнир ещё не начался — матчи появятся после старта.")
            nxt = await next_matches(uid)
            if nxt:
                parts.append("\n<b>С кем играть дальше</b> (сначала те, кто готов):")
                for i, (m, opp) in enumerate(nxt):
                    mark = "👉 " if i == 0 else "• "
                    parts.append(f"{mark}#{m['id']} vs <b>{ui.full(opp)}</b> — {ui.status(opp)[1]}")
            elif t["status"] == "running":
                parts.append("Все твои матчи сыграны 🎉")
            pend = await db.matches("tournament_id=? AND (p1=? OR p2=?) AND status IN ('pending','live')", t["id"], uid, uid)
            if pend:
                parts.append("\n<b>Ждут подтверждения / идут</b>:")
                parts += [f"⏳ #{m['id']} " + ui.result_line(m, users, uid) if m["status"] == "pending"
                          else f"▶️ #{m['id']} идёт матч" for m in pend]
            done = await db.matches("tournament_id=? AND (p1=? OR p2=?) AND status='confirmed' ORDER BY played_at DESC",
                                    t["id"], uid, uid)
            if done:
                parts.append("\n<b>Сыграно</b>:")
                parts += [ui.result_line(m, users, uid) for m in done]
    friendly = await db.matches("tournament_id IS NULL AND (p1=? OR p2=?) AND status='confirmed' "
                                "ORDER BY played_at DESC LIMIT 5", uid, uid)
    if friendly:
        parts.append("\n🤝 <b>Последние дружеские игры</b>:")
        parts += [ui.result_line(m, users, uid) for m in friendly]
    return "\n".join(parts) or "Пока нет матчей. Поставь статус «готов играть» или вступи в турнир в профиле."


# ---------- столы и очередь ----------

# Все операции бронирования идут через один замок: кто первый нажал — тому стол,
# следующий (даже на долю секунды позже) попадает в очередь.
LOCK = asyncio.Lock()
BOOK_MIN = 10        # бронь стола для пары
PLAY_MIN = 30        # пара нажала «Начали играть» — стол за ней на это время
LIVE_MIN = 20        # стол держится за матчем, пока ведут счёт (продлевается каждым очком)
QUEUE_TTL = 90 * 60  # заявка в очереди сгорает через 1,5 часа


async def tables() -> list[dict]:
    rows = await db.q("SELECT * FROM tables ORDER BY table_no")
    for r in rows:
        r["busy"] = bool(r["busy_by"]) and (r["busy_until"] or 0) > db.now()
    return rows


async def occupy(table_no: int, uid: int, minutes: int, note: str, match_id: int | None = None,
                 with_uid: int | None = None):
    await db.ex("UPDATE tables SET busy_by=?, busy_with=?, note=?, match_id=?, busy_until=? WHERE table_no=?",
                uid, with_uid, note, match_id, db.now() + minutes * 60, table_no)


async def free_table(table_no: int | None = None, match_id: int | None = None, bot: Bot | None = None):
    where, arg = ("match_id=?", match_id) if match_id is not None else ("table_no=?", table_no)
    await db.ex(f"UPDATE tables SET busy_by=NULL, busy_with=NULL, note=NULL, match_id=NULL, busy_until=NULL "
                f"WHERE {where}", arg)
    if bot:
        await advance_queue(bot)


def pair_label(users: dict, a: int, b: int) -> str:
    return f"{users[a]['name'] if a in users else '?'} — {users[b]['name'] if b in users else '?'}"


async def pair_table(a: int, b: int) -> dict | None:
    """Стол, который сейчас держит эта пара (бронь или матч)."""
    for tb in await tables():
        if tb["busy"] and {tb["busy_by"], tb["busy_with"]} & {a, b} - {None}:
            return tb
    return None


async def queue() -> list[dict]:
    return await db.q("SELECT * FROM table_queue WHERE status='waiting' ORDER BY id")


async def queue_entry(a: int, b: int) -> tuple[dict, int] | tuple[None, None]:
    for i, e in enumerate(await queue(), 1):
        if {e["p1"], e["p2"]} & {a, b}:
            return e, i
    return None, None


async def request_table(a: int, b: int, book: bool) -> tuple[str, dict | int | None]:
    """
    Пара нашла друг друга. Возвращает:
      ('own', стол)    — у пары уже есть стол;
      ('free', None)   — стол свободен (если book=False — только сообщаем);
      ('booked', стол) — забронировали на 10 минут;
      ('queued', №)    — стол занят или есть очередь: пара в очереди под этим номером.
    """
    async with LOCK:
        tb = await pair_table(a, b)
        if tb:
            return "own", tb
        e, pos = await queue_entry(a, b)
        if e:
            return "queued", pos
        q = await queue()
        free = [t for t in await tables() if not t["busy"]]
        if free and not q:
            if not book:
                return "free", None
            users = await umap()
            await occupy(free[0]["table_no"], a, BOOK_MIN, pair_label(users, a, b) + " (бронь)", None, b)
            return "booked", free[0]
        await db.ex("INSERT INTO table_queue(p1, p2, status, created_at) VALUES (?, ?, 'waiting', ?)", a, b, db.now())
        return "queued", len(q) + 1


async def playing_now_text(users: dict | None = None) -> str:
    users = users or await umap()
    lines = []
    for tb in await tables():
        if not tb["busy"]:
            continue
        who = pair_label(users, tb["busy_by"], tb["busy_with"]) if tb["busy_with"] else \
            (tb["note"] or name(users.get(tb["busy_by"])))
        score = ""
        if tb["match_id"]:
            m = await db.match(tb["match_id"])
            if m and m["live"]:
                games, cur = logic.replay(m["live"]["pts"])
                score = f", счёт {ui.games_score(games)} (партия {cur[0]}:{cur[1]})"
        what = "играют" if tb["match_id"] else "забронировали"
        lines.append(f"{what}: <b>{ui.esc(who)}</b>{score}, ещё ~{ui.dur(tb['busy_until'] - db.now())}")
    return "\n".join(lines) or "никого"


def queue_kb(entry_id: int):
    return ikb([[("🚪 Выйти из очереди", f"qx:{entry_id}")]])


async def table_result_text(kind: str, val, a: int, b: int) -> tuple[str, object]:
    """Текст и кнопки для пары по результату request_table."""
    users = await umap()
    if kind in ("booked", "own"):
        label = f" {val['table_no']}" if len(await tables()) > 1 else ""
        left = ui.dur(val["busy_until"] - db.now()) if kind == "own" else f"{BOOK_MIN} мин"
        rows = []
        if await tournament_match_between(a, b):
            rows.append([("🏆 Турнирный матч — вести счёт", f"lp:{a}:{b}")])
        rows += [[("🏓 Начали играть", f"bkp:{a}:{b}")],
                 [("✍️ Внести результат", f"res2:{a}:{b}")],
                 [("🏁 Закончили / снять бронь", f"bkx:{a}:{b}")]]
        return (f"✅ Стол{label} забронирован за вами (<b>{ui.esc(pair_label(users, a, b))}</b>) — {left}.\n\n"
                f"Подошли к столу — нажмите «🏓 Начали играть», стол будет за вами {PLAY_MIN} минут.\n"
                "Сыграли — «✍️ Внести результат» и «🏁 Закончили», чтобы стол достался следующим.\n"
                "Не подойдёте — бронь сгорит сама.",
                ikb(rows))
    if kind == "free":
        return ("🟢 <b>Стол сейчас свободен!</b> Забронировать его за вами на 10 минут?",
                ikb([[("🟢 Забронировать на 10 минут", f"bk:{a}:{b}")]]))
    e, pos = await queue_entry(a, b)
    return (f"⏳ <b>Стол занят — вы в очереди №{pos}.</b>\n"
            f"Сейчас за столом {await playing_now_text(users)}.\n\n"
            "Как только стол освободится, бот сразу позовёт вас и забронирует стол на 10 минут.",
            queue_kb(e["id"]) if e else None)


async def advance_queue(bot: Bot):
    """Отдать свободные столы следующим парам из очереди."""
    called = []
    async with LOCK:
        await db.ex("UPDATE table_queue SET status='expired' WHERE status='waiting' AND created_at < ?",
                    db.now() - QUEUE_TTL)
        users = await umap()
        for tb in await tables():
            if tb["busy"]:
                continue
            q = await queue()
            if not q:
                break
            e = q[0]
            await occupy(tb["table_no"], e["p1"], BOOK_MIN, pair_label(users, e["p1"], e["p2"]) + " (бронь)",
                         None, e["p2"])
            await db.ex("UPDATE table_queue SET status='called', called_at=? WHERE id=?", db.now(), e["id"])
            called.append(e)
    if not called:
        return
    for e in called:
        tb = await pair_table(e["p1"], e["p2"])
        text, kb = await table_result_text("booked", tb, e["p1"], e["p2"])
        for uid in (e["p1"], e["p2"]):
            await notify(bot, uid, "🔔 <b>Ваша очередь!</b>\n" + text, kb)
    q = await queue()
    if q:
        for uid in (q[0]["p1"], q[0]["p2"]):
            await notify(bot, uid, "⏳ Вы <b>следующие</b> в очереди к столу. Будьте рядом!", queue_kb(q[0]["id"]))


async def tables_text() -> str:
    users = await umap()
    lines = []
    rows = await tables()
    for tb in rows:
        label = f"Стол {tb['table_no']}" if len(rows) > 1 else "Стол"
        if tb["busy"]:
            who = pair_label(users, tb["busy_by"], tb["busy_with"]) if tb["busy_with"] else \
                (tb["note"] or users.get(tb["busy_by"], {}).get("name", "?"))
            what = "идёт матч" if tb["match_id"] else "занят"
            score = ""
            if tb["match_id"]:
                m = await db.match(tb["match_id"])
                if m and m["live"]:
                    games, cur = logic.replay(m["live"]["pts"])
                    score = f" · {ui.games_score(games)}, партия {cur[0]}:{cur[1]}"
            lines.append(f"🔴 <b>{label}</b> {what}: {ui.esc(who)}{score} — ещё ~{ui.dur(tb['busy_until'] - db.now())}")
        else:
            lines.append(f"🟢 <b>{label}</b> свободен")
    q = await queue()
    if q:
        lines.append("\n⏳ <b>Очередь:</b>")
        lines += [f"{i}. {ui.esc(pair_label(users, e['p1'], e['p2']))} — ждут {ui.dur(db.now() - e['created_at'])}"
                  for i, e in enumerate(q, 1)]
    return "\n".join(lines)


async def attach_table(mt: dict) -> str:
    """Матч начался: закрепить за ним стол пары или свободный стол."""
    users = await umap()
    note = pair_label(users, mt["p1"], mt["p2"])
    async with LOCK:
        tb = await pair_table(mt["p1"], mt["p2"])
        if not tb and not await queue():
            tb = next((t for t in await tables() if not t["busy"]), None)
        if tb:
            await occupy(tb["table_no"], mt["p1"], LIVE_MIN, note, mt["id"], mt["p2"])
            await db.ex("UPDATE table_queue SET status='called' WHERE status='waiting' AND "
                        "(p1 IN (?,?) OR p2 IN (?,?))", mt["p1"], mt["p2"], mt["p1"], mt["p2"])
            return "Стол отмечен занятым за этим матчем ✅"
    return ("⚠️ Стол сейчас занят или на него есть очередь — счёт ведём, "
            "но проверьте, что не заняли чужую бронь.")


async def search_users(text: str, exclude: set[int]) -> list[dict]:
    q = text.strip().lower().lstrip("@")
    return [u for u in await db.users() if u["tg_id"] not in exclude
            and (q in u["name"].lower() or q in (u["username"] or "").lower())][:10]


# ---------- результаты ----------

def score_card(m: dict, users: dict) -> str:
    kind = "🏆 Турнирный матч" if m["tournament_id"] else "🤝 Дружеская игра"
    return (f"{kind} #{m['id']}\n<b>{name(users.get(m['p1']))}</b> — <b>{name(users.get(m['p2']))}</b>: "
            f"<b>{ui.games_score(m['scores'])}</b> ({ui.scores_text(m['scores'])})\n"
            f"Победитель: <b>{name(users.get(m['winner']))}</b>")


def confirm_kb(mid: int):
    return ikb([[("✅ Верно", f"cf:{mid}"), ("❌ Неверно", f"rj:{mid}")]])


async def submit_result(bot: Bot, mid: int, scores: list[tuple[int, int]], reporter: int):
    """Счёт внёс сам игрок. Возвращает текст и клавиатуру для него."""
    m = await db.match(mid)
    win_idx = logic.games_winner(scores, m["best_of"])
    winner = m["p1"] if win_idx == 0 else m["p2"]
    await db.save_scores(mid, scores, winner, "pending", reported_by=reporter, live=None)
    await free_table(match_id=mid, bot=bot)
    m = await db.match(mid)
    users = await umap()
    card = score_card(m, users)
    opp = m["p2"] if reporter == m["p1"] else m["p1"]
    if m["tournament_id"]:
        await notify(bot, opp, f"📝 {name(users.get(reporter))} внёс результат:\n{card}\n\nЖдём подтверждения судьи.")
        return card + "\n\n👨‍⚖️ <b>Кто судил матч?</b> Он подтвердит счёт:", await referee_kb(mid, users)
    await notify(bot, opp, f"📝 {name(users.get(reporter))} внёс результат:\n{card}\n\nВсё верно?", confirm_kb(mid))
    return card + "\n\nОтправил сопернику на подтверждение ✉️", None


async def submit_by_referee(bot: Bot, mid: int, scores: list[tuple[int, int]], ref: int) -> str:
    """Счёт вёл судья (не игрок): результат подтверждают оба игрока."""
    m = await db.match(mid)
    winner = m["p1"] if logic.games_winner(scores, m["best_of"]) == 0 else m["p2"]
    await db.save_scores(mid, scores, winner, "pending", reported_by=ref, referee_id=ref, need_players=1,
                         confirms="[]", live=None)
    await free_table(match_id=mid, bot=bot)
    m = await db.match(mid)
    users = await umap()
    card = score_card(m, users)
    for uid in (m["p1"], m["p2"]):
        await notify(bot, uid, f"👨‍⚖️ Судья <b>{name(users.get(ref))}</b> записал результат вашего матча:\n\n{card}\n\n"
                               "Подтверди, что всё верно 👇 Результат засчитается, когда подтвердят оба игрока.",
                     confirm_kb(mid))
    return card + "\n\n✉️ Отправил игрокам на подтверждение."


async def referee_kb(mid: int, users: dict):
    m = await db.match(mid)
    exclude = {m["p1"], m["p2"]}
    rows = [[("👑 Любой админ", f"ref:{mid}:0")]]
    admins = [u for u in await db.admins() if u["tg_id"] not in exclude]
    ready = [u for u in users.values() if u["tg_id"] not in exclude and ui.status(u)[0]
             and u["tg_id"] not in {a["tg_id"] for a in admins}]
    for u in (admins + ready)[:10]:
        rows.append([(("👑 " if db.is_admin(u) else "") + u["name"], f"ref:{mid}:{u['tg_id']}")])
    rows.append([("🔎 Найти судью по имени", f"refs:{mid}")])
    return ikb(rows)


async def ask_referee(bot: Bot, mid: int, ref_id: int):
    m = await db.match(mid)
    await db.ex("UPDATE matches SET referee_id=? WHERE id=?", ref_id, mid)
    users = await umap()
    kb = ikb([[("✅ Подтвердить", f"cf:{mid}"), ("❌ Отклонить", f"rj:{mid}")]])
    text = f"👨‍⚖️ Тебя указали судьёй. Подтверди результат:\n\n{score_card(m, users)}"
    targets = [a["tg_id"] for a in await db.admins()] if ref_id == 0 else [ref_id]
    for uid in targets:
        await notify(bot, uid, text, kb)


def confirms_of(m: dict) -> list[int]:
    return json.loads(m["confirms"]) if m.get("confirms") else []


async def can_confirm(m: dict, uid: int) -> bool:
    u = await db.user(uid)
    if m.get("need_players"):
        # счёт вёл судья — подтверждают сами игроки (или админ, не игравший в матче)
        if uid in (m["p1"], m["p2"]):
            return uid not in confirms_of(m)
        return db.is_admin(u)
    if m["tournament_id"]:
        # свой турнирный матч не может подтвердить даже админ
        return uid not in (m["p1"], m["p2"]) and (db.is_admin(u) or m["referee_id"] == uid)
    if db.is_admin(u):
        return True
    return uid in (m["p1"], m["p2"]) and uid != m["reported_by"]


async def player_confirm(bot: Bot, m: dict, uid: int) -> bool:
    """Игрок подтвердил счёт судьи. True — подтвердили оба, результат засчитан."""
    confirms = confirms_of(m) + [uid]
    await db.ex("UPDATE matches SET confirms=? WHERE id=?", json.dumps(confirms), m["id"])
    if {m["p1"], m["p2"]} <= set(confirms):
        await confirm(bot, m, m["referee_id"] or uid)
        return True
    other = m["p2"] if uid == m["p1"] else m["p1"]
    users = await umap()
    await notify(bot, other, f"✅ {name(users.get(uid))} подтвердил(а) счёт матча #{m['id']}. Ждём тебя 👇",
                 confirm_kb(m["id"]))
    return False


async def confirm(bot: Bot, m: dict, by: int):
    await db.ex("UPDATE matches SET status='confirmed', confirmed_by=? WHERE id=?", by, m["id"])
    users = await umap()
    card = score_card(m, users)
    for uid in (m["p1"], m["p2"]):
        extra = ""
        if m["tournament_id"]:
            t = await db.q1("SELECT * FROM tournaments WHERE id=?", m["tournament_id"])
            extra = "\n\n" + await standings_text(t, m["league"], users)
            nxt = await next_matches(uid)
            if nxt:
                extra += f"\n👉 Следующий соперник: <b>{ui.full(nxt[0][1])}</b> — {ui.status(nxt[0][1])[1]}"
        await notify(bot, uid, f"✅ Результат засчитан (судья/подтвердил: {name(users.get(by))}):\n{card}{extra}")
    if m["tournament_id"]:
        left = await db.q1("SELECT COUNT(*) c FROM matches WHERE tournament_id=? AND status!='confirmed'", m["tournament_id"])
        if left["c"] == 0:
            for a in await db.admins():
                await notify(bot, a["tg_id"], "🎉 Все матчи турнира сыграны! Заверши турнир: /finishtournament")


async def reject(bot: Bot, m: dict, by: int):
    users = await umap()
    if m["tournament_id"]:
        await db.ex("UPDATE matches SET status='scheduled', scores=NULL, winner=NULL, referee_id=NULL, "
                    "reported_by=NULL, need_players=0, confirms=NULL WHERE id=?", m["id"])
    else:
        await db.ex("UPDATE matches SET status='rejected' WHERE id=?", m["id"])
    for uid in {m["p1"], m["p2"], m["referee_id"]} - {None, 0}:
        await notify(bot, uid, f"❌ {name(users.get(by))} отклонил(а) результат матча #{m['id']}. "
                               f"Сыграйте или внесите счёт заново через «{ui.B_LIVE}» / «{ui.B_RES}».")


async def housekeeping(bot: Bot):
    """Каждые несколько секунд: сгоревшие брони → следующей паре из очереди."""
    await advance_queue(bot)
