"""Тексты, кнопки и форматирование."""
import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

import db
import logic

B_STATUS = "🏓 Мой статус"
B_WHO = "👥 Кто готов"
B_TABLE = "🟢 Стол"
B_STAND = "🏆 Таблица"
B_MY = "📅 Мои матчи"
B_LIVE = "▶️ Вести счёт"
B_RES = "✍️ Внести результат"
B_PROF = "👤 Профиль"
MENU = {B_STATUS, B_WHO, B_TABLE, B_STAND, B_MY, B_LIVE, B_RES, B_PROF}

# код: (подпись, через сколько минут готов, сколько минут статус действует после этого)
STATUSES = {
    "now": ("🟢 Готов играть прямо сейчас", 0, 30),
    "5": ("🟡 Готов через 5 минут", 5, 30),
    "30": ("🟠 Готов через 30 минут", 30, 30),
    "60": ("🔵 Готов через час", 60, 30),
    "wait": ("📨 Жду приглашения на игру", 0, 180),
}


def main_kb() -> ReplyKeyboardMarkup:
    rows = [[B_STATUS, B_WHO], [B_TABLE, B_STAND], [B_MY, B_LIVE], [B_RES, B_PROF]]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
    )


def ikb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows if row]
    )


def esc(s) -> str:
    return html.escape(str(s or ""))


def name(u: dict | None) -> str:
    return esc(u["name"]) if u else "?"


def full(u: dict) -> str:
    return f"{name(u)} · {esc(u['department'])}" if u.get("department") else name(u)


def dur(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} мин"
    h, m = divmod(minutes, 60)
    return f"{h} ч {m} мин" if m else f"{h} ч"


def status(u: dict) -> tuple[bool, str, int]:
    """(активен ли статус, текст, ключ сортировки: сколько секунд до готовности)."""
    t = db.now()
    code = u.get("status")
    if not code or code not in STATUSES or (u.get("status_until") or 0) < t:
        return False, "⚪️ не ищет игру", 10 ** 9
    if code == "wait":
        return True, "📨 ждёт приглашения", 1
    left = (u["ready_at"] or 0) - t
    if left > 0:
        return True, f"🟡 будет готов через {dur(left)}", left
    return True, "🟢 готов прямо сейчас", 0


def games_score(scores) -> str:
    w = [0, 0]
    for a, b in scores:
        w[0 if a > b else 1] += 1
    return f"{w[0]}:{w[1]}"


def scores_text(scores, flip=False) -> str:
    return ", ".join(f"{b}:{a}" if flip else f"{a}:{b}" for a, b in scores)


def result_line(m: dict, umap: dict, viewer: int | None = None) -> str:
    """Результат с точки зрения viewer (если он участник), иначе p1 vs p2."""
    flip = viewer == m["p2"]
    me, opp = (m["p2"], m["p1"]) if flip else (m["p1"], m["p2"])
    if m["walkover"]:
        body = "тех. победа" if m["winner"] == me else "тех. поражение"
    else:
        body = f"{games_score([(b, a) for a, b in m['scores']] if flip else m['scores'])} ({scores_text(m['scores'], flip)})"
    if viewer in (m["p1"], m["p2"]):
        icon = "✅" if m["winner"] == viewer else "❌"
        return f"{icon} vs {name(umap.get(opp))}: {body}"
    return f"{name(umap.get(me))} — {name(umap.get(opp))}: {body}"


def league_name(code: str | None) -> str:
    return logic.LEAGUE_NAMES.get(code or "", "—")


def profile_text(u: dict) -> str:
    unit = logic.UNITS.get(u["exp_unit"] or "", ("", 1))[0]
    exp = f"{u['exp_value']} {unit}" if u["exp_value"] else "только начинаю"
    return (
        f"👤 <b>{name(u)}</b>\n"
        f"Отдел: {esc(u['department'])}\n"
        f"Опыт: {exp}\n"
        f"Уровень: {logic.LEVELS.get(u['level'], '—')}\n"
        f"Рейтинг посева: {u['seed']}\n"
        f"Лига: {league_name(u['league'])}\n"
        f"Турнир: {'участвую' if u['wants_tournament'] else 'только дружеские игры'}"
    )
