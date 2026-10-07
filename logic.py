"""Чистая логика без Telegram: уровни, лиги, валидация счёта, живой счёт, турнирная таблица."""
import re
from itertools import combinations

LEVELS = {
    1: "Начинающий",
    2: "Любитель",
    3: "Увлекающийся",
    4: "Полупрофессионал",
    5: "Профессионал",
}
UNITS = {"d": ("дней", 1), "m": ("месяцев", 30), "y": ("лет", 365)}
UNIT_FORMS = {"d": ("день", "дня", "дней"), "m": ("месяц", "месяца", "месяцев"), "y": ("год", "года", "лет")}


def plural(n: int, unit: str) -> str:
    one, few, many = UNIT_FORMS[unit]
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"

# (код, название, минимальный рейтинг посева)
LEAGUES = [
    ("A", "Высшая лига", 80),
    ("B", "Первая лига", 45),
    ("C", "Открытая лига", 0),
]
LEAGUE_NAMES = {code: name for code, name, _ in LEAGUES}


# ---------- посев ----------

def exp_points(days: int) -> int:
    if days < 90:
        return 0
    if days < 365:
        return 5
    if days < 3 * 365:
        return 10
    if days < 5 * 365:
        return 15
    return 20


def seed_score(level: int, exp_days: int) -> int:
    """Рейтинг посева 20..120: самоощущение (20 за ступень) + стаж (0..20)."""
    return level * 20 + exp_points(exp_days)


def league_for(seed: int) -> str:
    for code, _, minimum in LEAGUES:
        if seed >= minimum:
            return code
    return LEAGUES[-1][0]


def merge_small_leagues(by_league: dict[str, list[int]]) -> dict[str, list[int]]:
    """Лига с одним игроком не может играть: переносим его в соседнюю непустую лигу."""
    order = [code for code, _, _ in LEAGUES]
    changed = True
    while changed:
        changed = False
        filled = [c for c in order if by_league.get(c)]
        if len(filled) < 2:
            break
        for i, code in enumerate(filled):
            if len(by_league[code]) == 1:
                target = filled[i + 1] if i + 1 < len(filled) else filled[i - 1]
                by_league[target] += by_league.pop(code)
                changed = True
                break
    return {c: p for c, p in by_league.items() if p}


def round_robin_pairs(players: list[int]) -> list[tuple[int, int]]:
    return list(combinations(players, 2))


# ---------- счёт ----------

def need_wins(best_of: int) -> int:
    return best_of // 2 + 1


def game_over(a: int, b: int) -> bool:
    return max(a, b) >= 11 and abs(a - b) >= 2


def game_error(a: int, b: int) -> str | None:
    w, l = max(a, b), min(a, b)
    if w == 11 and l <= 9:
        return None
    if w > 11 and w - l == 2:
        return None
    if w < 11:
        return f"{a}:{b} — партия идёт до 11 очков"
    if w == 11 and l == 10:
        return f"{a}:{b} — при 10:10 играют до разницы в 2 очка (12:10, 13:11…)"
    return f"{a}:{b} — после 10:10 разница должна быть ровно 2 очка"


def parse_scores(text: str) -> list[tuple[int, int]]:
    return [(int(a), int(b)) for a, b in re.findall(r"(\d{1,2})\s*[:\-–—/ ]\s*(\d{1,2})", text)]


def games_winner(games: list[tuple[int, int]], best_of: int) -> int | None:
    """0 — выиграл первый, 1 — второй, None — матч не закончен."""
    need = need_wins(best_of)
    wins = [0, 0]
    for a, b in games:
        wins[0 if a > b else 1] += 1
        if max(wins) == need:
            return 0 if wins[0] == need else 1
    return None


def match_error(games: list[tuple[int, int]], best_of: int) -> str | None:
    if not games:
        return "Не нашёл ни одной партии. Пример: 11:7 9:11 11:5"
    for a, b in games:
        err = game_error(a, b)
        if err:
            return err
    need = need_wins(best_of)
    wins = [0, 0]
    for i, (a, b) in enumerate(games):
        wins[0 if a > b else 1] += 1
        if max(wins) == need and i != len(games) - 1:
            return f"Матч закончился после {i + 1}-й партии (до {need} побед) — лишние партии"
    if max(wins) < need:
        return f"Матч не закончен: нужно выиграть {need} партии (сейчас {wins[0]}:{wins[1]})"
    return None


# ---------- живой счёт ----------

def replay(points: list[int]) -> tuple[list[tuple[int, int]], list[int]]:
    """points — последовательность выигравших розыгрыш (0/1). Возвращает (сыгранные партии, текущий счёт)."""
    games, cur = [], [0, 0]
    for w in points:
        cur[w] += 1
        if game_over(*cur):
            games.append((cur[0], cur[1]))
            cur = [0, 0]
    return games, cur


def server(first_server: int, game_index: int, cur: list[int]) -> int:
    """Подача переходит каждые 2 розыгрыша, при 10:10 — каждый розыгрыш; в новой партии первым подаёт другой."""
    fs = (first_server + game_index) % 2
    pts = cur[0] + cur[1]
    switches = pts // 2 if pts < 20 else 10 + (pts - 20)
    return (fs + switches) % 2


# ---------- турнирная таблица (ITTF) ----------

def _blank():
    return {"p": 0, "w": 0, "l": 0, "mp": 0, "gw": 0, "gl": 0, "pw": 0, "pl": 0}


def _ratio(won: int, lost: int) -> float:
    return won / lost if lost else float("inf") if won else 0.0


def _apply(stats: dict, m: dict, need: int):
    a, b = m["p1"], m["p2"]
    if a not in stats or b not in stats:
        return
    if m["walkover"]:
        games = [(11, 0)] * need if m["winner"] == a else [(0, 11)] * need
    else:
        games = m["scores"]
    sa, sb = stats[a], stats[b]
    for x, y in games:
        sa["pw"] += x; sa["pl"] += y; sb["pw"] += y; sb["pl"] += x
        if x > y:
            sa["gw"] += 1; sb["gl"] += 1
        else:
            sb["gw"] += 1; sa["gl"] += 1
    sa["p"] += 1; sb["p"] += 1
    win, lose = (sa, sb) if m["winner"] == a else (sb, sa)
    win["w"] += 1; win["mp"] += 2
    lose["l"] += 1; lose["mp"] += 0 if m["walkover"] else 1


def standings(players: list[int], matches: list[dict], best_of: int) -> list[tuple[int, dict]]:
    """
    1) очки: победа 2, поражение 1, неявка 0;
    2) при равенстве очков — только матчи между этими игроками: очки, соотношение партий, соотношение мячей;
    3) затем соотношение партий и мячей по всем матчам.
    """
    need = need_wins(best_of)
    stats = {p: _blank() for p in players}
    for m in matches:
        _apply(stats, m, need)

    order = sorted(players, key=lambda p: -stats[p]["mp"])
    result, i = [], 0
    while i < len(order):
        group = [p for p in order[i:] if stats[p]["mp"] == stats[order[i]]["mp"]]
        if len(group) > 1:
            h2h = {p: _blank() for p in group}
            for m in matches:
                if m["p1"] in h2h and m["p2"] in h2h:
                    _apply(h2h, m, need)
            group.sort(key=lambda p: (
                -h2h[p]["mp"],
                -_ratio(h2h[p]["gw"], h2h[p]["gl"]),
                -_ratio(h2h[p]["pw"], h2h[p]["pl"]),
                -_ratio(stats[p]["gw"], stats[p]["gl"]),
                -_ratio(stats[p]["pw"], stats[p]["pl"]),
                -stats[p]["w"],
            ))
        result += group
        i += len(group)
    return [(p, stats[p]) for p in result]


RULES_TEXT = (
    "📜 <b>Как считается турнир</b>\n\n"
    "<b>Формат.</b> Игроки делятся на лиги по уровню (самоощущение + стаж). "
    "Внутри лиги каждый играет с каждым по одному матчу — порядок свободный, "
    "бот подсказывает, с кем играть дальше.\n\n"
    "<b>Матч</b> — до {need} выигранных партий. <b>Партия</b> — до 11 очков; "
    "при 10:10 играют до разницы в 2 (12:10, 15:13…). Подача меняется каждые 2 розыгрыша, "
    "при 10:10 — каждый розыгрыш.\n\n"
    "<b>Очки в таблице</b> (как в ITTF):\n"
    "• победа — 2 очка\n• поражение — 1 очко\n• неявка (тех. поражение) — 0 очков\n\n"
    "<b>Если очков поровну</b>:\n"
    "1. очки в матчах только между этими игроками (личные встречи);\n"
    "2. соотношение выигранных/проигранных партий в этих матчах;\n"
    "3. соотношение выигранных/проигранных мячей в этих матчах;\n"
    "4. то же по всем матчам.\n\n"
    "<b>Колонки</b>: И — игр, В — побед, П — поражений, О — очков, "
    "Парт — партии (выиграно:проиграно), Мячи — разница мячей.\n\n"
    "Турнирный результат засчитывается только после подтверждения судьёй или админом."
)
