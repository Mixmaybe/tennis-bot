"""Статусы «готов играть», кто готов, приглашения, стол, QR, помощь."""
import io

import qrcode
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import config
import db
import services
import ui
from ui import ikb, name

router = Router()

@router.message(Command("qr"))
async def qr_cmd(m: Message, bot: Bot):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=office"
    img = qrcode.make(link, box_size=12, border=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    await m.answer_photo(BufferedInputFile(buf.getvalue(), "tennis_bot_qr.png"),
                         caption=f"🏓 Офисный настольный теннис\nСканируй и регистрируйся: {link}")


async def need_user(m: Message) -> dict | None:
    u = await db.user(m.chat.id)
    if not u:
        await m.answer("Сначала зарегистрируйся: /start")
    return u


# ---------- статус ----------

@router.message(F.text == ui.B_STATUS)
async def my_status(m: Message, state: FSMContext):
    await state.clear()
    u = await need_user(m)
    if not u:
        return
    rows = [[(title, f"st:{code}")] for code, (title, _, _) in ui.STATUSES.items()]
    rows.append([("⚪️ Не ищу игру", "st:off")])
    await m.answer(f"Твой статус сейчас: {ui.status(u)[1]}\n\nВыбери новый:", reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("st:"))
async def set_status(c: CallbackQuery, bot: Bot):
    code = c.data.split(":")[1]
    uid = c.from_user.id
    if code == "off":
        await db.ex("UPDATE users SET status=NULL, ready_at=NULL, status_until=NULL WHERE tg_id=?", uid)
        await c.answer("Статус снят")
        await c.message.edit_text("Статус: ⚪️ не ищу игру")
        return
    _, delay, window = ui.STATUSES[code]
    ready_at = db.now() + delay * 60
    await db.ex("UPDATE users SET status=?, ready_at=?, status_until=? WHERE tg_id=?",
                code, ready_at, ready_at + window * 60, uid)
    u = await db.user(uid)
    await c.answer("Статус обновлён")
    await c.message.edit_text(f"Статус: {ui.status(u)[1]}\n\n{await services.tables_text()}")
    # Предложить турнирного соперника, если он тоже ищет игру, и сообщить ему
    for mt, opp in await services.next_matches(uid):
        if ui.status(opp)[0]:
            await c.message.answer(
                f"🏆 Твой турнирный соперник <b>{ui.full(opp)}</b> тоже {ui.status(opp)[1]}!",
                reply_markup=ikb([[(f"📨 Пригласить {opp['name']}", f"inv:{opp['tg_id']}")]]))
            await services.notify(bot, opp["tg_id"],
                                  f"🏆 Твой турнирный соперник <b>{ui.full(u)}</b> {ui.status(u)[1]}.",
                                  ikb([[(f"📨 Пригласить {u['name']}", f"inv:{uid}")]]))
            break
    else:
        await who_ready(c.message, uid)


# ---------- кто готов ----------

@router.message(F.text == ui.B_WHO)
async def who_cmd(m: Message, state: FSMContext):
    await state.clear()
    if await need_user(m):
        await who_ready(m, m.chat.id)


async def who_ready(m: Message, uid: int):
    users = [u for u in await db.users() if u["tg_id"] != uid and ui.status(u)[0]]
    users.sort(key=lambda u: ui.status(u)[2])
    opponents = {opp["tg_id"] for _, opp in await services.next_matches(uid)}
    text = await services.tables_text() + "\n\n"
    if not users:
        await m.answer(text + "Пока никто не ищет игру 😴\nПоставь свой статус — тебя увидят другие.")
        return
    lines = ["👥 <b>Готовы играть:</b>"]
    rows = []
    for u in users[:15]:
        trophy = " 🏆 твой соперник по турниру" if u["tg_id"] in opponents else ""
        lines.append(f"• <b>{ui.full(u)}</b> — {ui.status(u)[1]}{trophy}")
        rows.append([(f"📨 Пригласить {u['name']}", f"inv:{u['tg_id']}")])
    await m.answer(text + "\n".join(lines), reply_markup=ikb(rows))


# ---------- приглашения ----------

@router.callback_query(F.data.startswith("inv:"))
async def invite(c: CallbackQuery, bot: Bot):
    target = int(c.data.split(":")[1])
    me = await db.user(c.from_user.id)
    if not me:
        await c.answer("Сначала /start", show_alert=True)
        return
    is_t = any(opp["tg_id"] == target for _, opp in await services.next_matches(me["tg_id"]))
    kind = "турнирный матч 🏆" if is_t else "партию 🏓"
    await services.notify(bot, target, f"📨 <b>{ui.full(me)}</b> приглашает тебя сыграть {kind}!\n\n"
                                       f"{await services.tables_text()}",
                          ikb([[("✅ Иду!", f"inva:{me['tg_id']}"), ("⏰ Позже", f"invl:{me['tg_id']}"),
                                ("❌ Не могу", f"invd:{me['tg_id']}")]]))
    await c.answer("Приглашение отправлено ✉️", show_alert=True)


@router.callback_query(F.data.regexp(r"^inv[ald]:"))
async def invite_answer(c: CallbackQuery, bot: Bot):
    kind, inviter = c.data.split(":")
    me = await db.user(c.from_user.id)
    answers = {
        "inva": ("✅ <b>{n}</b> принял(а) приглашение и идёт к столу!", "Отлично, удачной игры! 🏓"),
        "invl": ("⏰ <b>{n}</b> сможет чуть позже.", "Ок, сообщил."),
        "invd": ("❌ <b>{n}</b> сейчас не может.", "Ок, сообщил."),
    }
    to_inviter, to_me = answers[kind]
    await services.notify(bot, int(inviter), to_inviter.format(n=name(me)))
    if kind == "inva":
        await db.ex("UPDATE users SET status=NULL, ready_at=NULL, status_until=NULL WHERE tg_id IN (?,?)",
                    me["tg_id"], int(inviter))
        to_me += f"\nНе забудь «{ui.B_LIVE}», чтобы стол отметился занятым."
    await c.answer()
    await c.message.edit_text(c.message.html_text + f"\n\n{to_me}")


# ---------- стол ----------

@router.message(F.text == ui.B_TABLE)
async def table_cmd(m: Message, state: FSMContext):
    await state.clear()
    if await need_user(m):
        await show_tables(m, m.chat.id)


async def show_tables(m: Message, uid: int, edit: bool = False):
    rows = []
    for tb in await services.tables():
        suffix = f" {tb['table_no']}" if config.TABLES > 1 else ""
        if not tb["busy"]:
            rows.append([(f"Занять{suffix} на 15 мин", f"tb:{tb['table_no']}:15"),
                         (f"на 30 мин", f"tb:{tb['table_no']}:30")])
        elif tb["busy_by"] == uid or db.is_admin(await db.user(uid)):
            rows.append([(f"🟢 Освободить стол{suffix}", f"tb:{tb['table_no']}:0")])
    text = await services.tables_text()
    ready = sum(1 for u in await db.users() if u["tg_id"] != uid and ui.status(u)[0])
    if ready:
        text += f"\n\n👥 Готовы играть: {ready} чел. — «{ui.B_WHO}»"
    if edit:
        await m.edit_text(text, reply_markup=ikb(rows))
    else:
        await m.answer(text, reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("tb:"))
async def table_action(c: CallbackQuery):
    _, no, minutes = c.data.split(":")
    no, minutes = int(no), int(minutes)
    u = await db.user(c.from_user.id)
    tb = next((t for t in await services.tables() if t["table_no"] == no), None)
    if minutes == 0:
        await services.free_table(no)
        await c.answer("Стол свободен")
    elif tb and tb["busy"]:
        await c.answer("Стол уже заняли 😕", show_alert=True)
    else:
        await services.occupy(no, u["tg_id"], minutes, f"{u['name']} играет")
        await c.answer(f"Стол занят на {minutes} мин")
    await show_tables(c.message, c.from_user.id, edit=True)
