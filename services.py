"""Операции, которые используют несколько обработчиков."""
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


# ---------- столы ----------

async def tables() -> list[dict]:
    rows = await db.q("SELECT * FROM tables ORDER BY table_no")
    for r in rows:
        r["busy"] = bool(r["busy_by"]) and (r["busy_until"] or 0) > db.now()
    return rows


async def occupy_free_table(uid: int, minutes: int, note: str, match_id: int | None = None) -> int | None:
    for tb in await tables():
        if not tb["busy"]:
            await occupy(tb["table_no"], uid, minutes, note, match_id)
            return tb["table_no"]
    return None


async def occupy(table_no: int, uid: int, minutes: int, note: str, match_id: int | None = None):
    await db.ex("UPDATE tables SET busy_by=?, note=?, match_id=?, busy_until=? WHERE table_no=?",
                uid, note, match_id, db.now() + minutes * 60, table_no)


async def free_table(table_no: int | None = None, match_id: int | None = None):
    if match_id is not None:
        await db.ex("UPDATE tables SET busy_by=NULL, note=NULL, match_id=NULL, busy_until=NULL WHERE match_id=?", match_id)
    else:
        await db.ex("UPDATE tables SET busy_by=NULL, note=NULL, match_id=NULL, busy_until=NULL WHERE table_no=?", table_no)


async def tables_text() -> str:
    users = await umap()
    lines = []
    rows = await tables()
    for tb in rows:
        label = f"Стол {tb['table_no']}" if len(rows) > 1 else "Стол"
        if tb["busy"]:
            who = name(users.get(tb["busy_by"]))
            lines.append(f"🔴 <b>{label}</b> занят: {ui.esc(tb['note']) or who} — ещё ~{ui.dur(tb['busy_until'] - db.now())}")
        else:
            lines.append(f"🟢 <b>{label}</b> свободен")
    return "\n".join(lines)


# ---------- результаты ----------

def score_card(m: dict, users: dict) -> str:
    kind = "🏆 Турнирный матч" if m["tournament_id"] else "🤝 Дружеская игра"
    return (f"{kind} #{m['id']}\n<b>{name(users.get(m['p1']))}</b> — <b>{name(users.get(m['p2']))}</b>: "
            f"<b>{ui.games_score(m['scores'])}</b> ({ui.scores_text(m['scores'])})\n"
            f"Победитель: <b>{name(users.get(m['winner']))}</b>")


async def submit_result(bot: Bot, mid: int, scores: list[tuple[int, int]], reporter: int):
    """Сохранить счёт (ориентирован p1:p2) и запустить подтверждение. Возвращает текст и клавиатуру для репортёра."""
    m = await db.match(mid)
    win_idx = logic.games_winner(scores, m["best_of"])
    winner = m["p1"] if win_idx == 0 else m["p2"]
    await db.save_scores(mid, scores, winner, "pending", reported_by=reporter, live=None)
    await free_table(match_id=mid)
    m = await db.match(mid)
    users = await umap()
    card = score_card(m, users)
    opp = m["p2"] if reporter == m["p1"] else m["p1"]
    if m["tournament_id"]:
        await notify(bot, opp, f"📝 {name(users.get(reporter))} внёс результат:\n{card}\n\nЖдём подтверждения судьи.")
        return card + "\n\n👨‍⚖️ <b>Кто судил матч?</b> Он подтвердит счёт:", await referee_kb(mid, users)
    await notify(bot, opp, f"📝 {name(users.get(reporter))} внёс результат:\n{card}\n\nВсё верно?",
                 ikb([[("✅ Верно", f"cf:{mid}"), ("❌ Неверно", f"rj:{mid}")]]))
    return card + "\n\nОтправил сопернику на подтверждение ✉️", None


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


async def can_confirm(m: dict, uid: int) -> bool:
    u = await db.user(uid)
    if m["tournament_id"]:
        # свой турнирный матч не может подтвердить даже админ
        return uid not in (m["p1"], m["p2"]) and (db.is_admin(u) or m["referee_id"] == uid)
    if db.is_admin(u):
        return True
    return uid in (m["p1"], m["p2"]) and uid != m["reported_by"]


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
        await notify(bot, uid, f"✅ Результат подтверждён ({name(users.get(by))}):\n{card}{extra}")
    if m["tournament_id"]:
        left = await db.q1("SELECT COUNT(*) c FROM matches WHERE tournament_id=? AND status!='confirmed'", m["tournament_id"])
        if left["c"] == 0:
            for a in await db.admins():
                await notify(bot, a["tg_id"], "🎉 Все матчи турнира сыграны! Заверши турнир: /finishtournament")


async def reject(bot: Bot, m: dict, by: int):
    users = await umap()
    if m["tournament_id"]:
        await db.ex("UPDATE matches SET status='scheduled', scores=NULL, winner=NULL, referee_id=NULL, "
                    "reported_by=NULL WHERE id=?", m["id"])
    else:
        await db.ex("UPDATE matches SET status='rejected' WHERE id=?", m["id"])
    for uid in (m["p1"], m["p2"]):
        await notify(bot, uid, f"❌ {name(users.get(by))} отклонил результат матча #{m['id']}. "
                               f"Внесите счёт заново через «{ui.B_RES}».")
