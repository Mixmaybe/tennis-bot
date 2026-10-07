import json
import time

import aiosqlite

import config

DB: aiosqlite.Connection

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    name TEXT NOT NULL,
    department TEXT,
    exp_value INTEGER,
    exp_unit TEXT,
    exp_days INTEGER,
    level INTEGER,
    seed INTEGER,
    league TEXT,
    wants_tournament INTEGER DEFAULT 0,
    is_admin INTEGER DEFAULT 0,
    status TEXT,
    ready_at INTEGER,
    status_until INTEGER,
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS tournaments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    status TEXT,              -- registration | running | finished
    best_of INTEGER,
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS tournament_players(
    tournament_id INTEGER,
    tg_id INTEGER,
    league TEXT,
    PRIMARY KEY(tournament_id, tg_id)
);
CREATE TABLE IF NOT EXISTS matches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER,    -- NULL = дружеская игра
    league TEXT,
    p1 INTEGER,
    p2 INTEGER,
    status TEXT,              -- scheduled | live | pending | confirmed | rejected
    best_of INTEGER,
    scores TEXT,              -- JSON [[p1, p2], ...]
    winner INTEGER,
    walkover INTEGER DEFAULT 0,
    reported_by INTEGER,
    referee_id INTEGER,       -- 0 = любой админ
    confirmed_by INTEGER,
    live TEXT,                -- JSON {"fs": 0|1, "pts": [0,1,...]}
    created_at INTEGER,
    played_at INTEGER
);
CREATE TABLE IF NOT EXISTS tables(
    table_no INTEGER PRIMARY KEY,
    busy_by INTEGER,
    note TEXT,
    match_id INTEGER,
    busy_until INTEGER
);
"""


def now() -> int:
    return int(time.time())


async def init(path: str):
    global DB
    DB = await aiosqlite.connect(path)
    DB.row_factory = aiosqlite.Row
    await DB.executescript(SCHEMA)
    # миграции: новые колонки для старых баз
    async with DB.execute("PRAGMA table_info(users)") as cur:
        cols = {r[1] for r in await cur.fetchall()}
    for col, ddl in [("verified", "INTEGER DEFAULT 0"),  # 0 нет, 1 подтверждён, -1 отклонён, 2 на проверке
                     ("verify_photo", "TEXT"), ("verify_kind", "TEXT")]:
        if col not in cols:
            await DB.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    for n in range(1, config.TABLES + 1):
        await DB.execute("INSERT OR IGNORE INTO tables(table_no) VALUES (?)", (n,))
    await DB.commit()


async def q(sql: str, *args) -> list[dict]:
    async with DB.execute(sql, args) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def q1(sql: str, *args) -> dict | None:
    rows = await q(sql, *args)
    return rows[0] if rows else None


async def ex(sql: str, *args) -> int:
    cur = await DB.execute(sql, args)
    await DB.commit()
    return cur.lastrowid


# ---------- пользователи ----------

async def user(tg_id: int) -> dict | None:
    return await q1("SELECT * FROM users WHERE tg_id=?", tg_id)


async def users() -> list[dict]:
    return await q("SELECT * FROM users ORDER BY name")


async def admins() -> list[dict]:
    rows = await q("SELECT * FROM users WHERE is_admin=1")
    known = {r["tg_id"] for r in rows}
    for aid in config.ADMIN_IDS - known:
        u = await user(aid)
        if u:
            rows.append(u)
    return rows


def is_admin(u: dict | None) -> bool:
    return bool(u) and (u["is_admin"] or u["tg_id"] in config.ADMIN_IDS)


# ---------- турниры ----------

async def active_tournament() -> dict | None:
    return await q1("SELECT * FROM tournaments WHERE status IN ('registration','running') ORDER BY id DESC LIMIT 1")


async def last_tournament() -> dict | None:
    return await q1("SELECT * FROM tournaments ORDER BY id DESC LIMIT 1")


async def tournament_players(tid: int, league: str | None = None) -> list[dict]:
    sql = ("SELECT u.*, tp.league AS t_league FROM tournament_players tp "
           "JOIN users u ON u.tg_id=tp.tg_id WHERE tp.tournament_id=?")
    args = [tid]
    if league:
        sql += " AND tp.league=?"
        args.append(league)
    return await q(sql + " ORDER BY u.seed DESC, u.name", *args)


async def player_entry(tid: int, tg_id: int) -> dict | None:
    return await q1("SELECT * FROM tournament_players WHERE tournament_id=? AND tg_id=?", tid, tg_id)


# ---------- матчи ----------

def decode(m: dict | None) -> dict | None:
    if m is None:
        return None
    m["scores"] = [tuple(g) for g in json.loads(m["scores"])] if m.get("scores") else []
    m["live"] = json.loads(m["live"]) if m.get("live") else None
    return m


async def match(mid: int) -> dict | None:
    return decode(await q1("SELECT * FROM matches WHERE id=?", mid))


async def matches(sql_where: str, *args) -> list[dict]:
    return [decode(m) for m in await q(f"SELECT * FROM matches WHERE {sql_where}", *args)]


async def save_scores(mid: int, scores: list, winner: int, status: str, **extra):
    fields = {"scores": json.dumps(scores), "winner": winner, "status": status, "played_at": now(), **extra}
    sets = ", ".join(f"{k}=?" for k in fields)
    await ex(f"UPDATE matches SET {sets} WHERE id=?", *fields.values(), mid)


async def set_live(mid: int, live: dict | None):
    await ex("UPDATE matches SET live=? WHERE id=?", json.dumps(live) if live else None, mid)
