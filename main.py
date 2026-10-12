import asyncio
import logging
import os
from datetime import datetime, timezone

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "rftl.db")

router = Router()
dp = Dispatcher()
dp.include_router(router)


class ApplyForm(StatesGroup):
    nickname = State()
    position = State()
    rating = State()
    experience = State()
    looking_for = State()


class ClubForm(StatesGroup):
    name = State()


class InviteForm(StatesGroup):
    player_id = State()
    message = State()


class TransferForm(StatesGroup):
    player_name = State()
    from_club = State()
    to_club = State()
    terms = State()


POSITIONS = ["ВРТ", "ЦЗ", "ПЗ", "ЛЗ", "ЦОП", "ЦП", "ЦАП", "ЛВ", "ПВ", "ФРВ", "Другое"]


def main_menu(is_admin=False):
    rows = [
        [KeyboardButton(text="🔎 Найти клуб"), KeyboardButton(text="📋 Лента игроков")],
        [KeyboardButton(text="👤 Моя анкета"), KeyboardButton(text="✉️ Приглашения")],
        [KeyboardButton(text="🏆 Мой клуб"), KeyboardButton(text="📜 Трансферы")],
        [KeyboardButton(text="ℹ️ Помощь")]
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛡 Модерация")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clubs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            owner_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            nickname TEXT NOT NULL,
            position TEXT NOT NULL,
            rating REAL NOT NULL CHECK(rating >= 1.0 AND rating <= 7.0),
            experience TEXT NOT NULL,
            looking_for TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE TABLE IF NOT EXISTS invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            club_id INTEGER NOT NULL,
            player_user_id INTEGER NOT NULL,
            manager_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            created_at TEXT NOT NULL,
            FOREIGN KEY(club_id) REFERENCES clubs(id)
        );
        CREATE TABLE IF NOT EXISTS transfer_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT NOT NULL,
            from_club TEXT NOT NULL,
            to_club TEXT NOT NULL,
            offer_type TEXT NOT NULL,
            terms TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        """)
        await db.commit()


async def upsert_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users(user_id, username, display_name, created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
               display_name=excluded.display_name""",
            (user.id, user.username or "", user.full_name, now())
        )
        await db.commit()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_admin(user_id):
    return user_id in ADMIN_IDS


async def get_club_for_manager(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM clubs WHERE owner_id=? ORDER BY id LIMIT 1", (user_id,))
        return await cur.fetchone()


async def get_player_by_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM players WHERE user_id=?", (user_id,))
        return await cur.fetchone()


async def send_profile_card(target, row, manager_club=None):
    text = (
        f"👤 <b>{row['nickname']}</b>\n"
        f"⚽ Позиция: <b>{row['position']}</b>\n"
        f"⭐ Рейтинг: <b>{row['rating']:.1f}/7.0</b>\n"
        f"📚 Опыт: {row['experience']}\n"
        f"🔍 Ищет: {row['looking_for']}\n"
        f"🆔 ID игрока: <code>{row['user_id']}</code>"
    )
    if manager_club:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✉️ Пригласить", callback_data=f"invite:{row['user_id']}")
        ]])
        await target.answer(text, reply_markup=kb)
    else:
        await target.answer(text)


@router.message(CommandStart())
async def start(message: Message):
    await upsert_user(message.from_user)
    await message.answer(
        "⚽ <b>Добро пожаловать в RFTL Transfer Center!</b>\n\n"
        "Здесь игроки ищут клубы, менеджеры находят состав, а трансферные предложения хранятся в одном месте.\n\n"
        "• Подай анкету — после проверки модератором она появится в ленте.\n"
        "• Если ты менеджер клуба, зарегистрируй клуб или попроси администратора назначить тебя менеджером.\n"
        "• Рейтинг игроков — от <b>1.0 до 7.0</b>.",
        reply_markup=main_menu(is_admin(message.from_user.id))
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def help_cmd(message: Message):
    await message.answer(
        "<b>Команды RFTL</b>\n"
        "/apply — подать анкету игрока\n"
        "/market — лента одобренных игроков (для менеджеров)\n"
        "/myprofile — посмотреть свою анкету\n"
        "/registerclub Название — зарегистрировать клуб\n"
        "/club — информация о твоём клубе\n"
        "/invitations — мои приглашения\n"
        "/transfer_offer — создать трансферное предложение\n\n"
        "<b>Для модераторов</b>\n"
        "/moderation — очередь заявок\n"
        "/approve ID — одобрить анкету\n"
        "/reject ID — отклонить анкету\n"
        "/setmanager USER_ID Название клуба — назначить менеджера\n"
        "/pending_transfers — список трансферных предложений\n"
        "/transfer_decide ID approve|reject — решение по предложению"
    )


@router.message(Command("apply"))
@router.message(F.text == "🔎 Найти клуб")
async def apply_start(message: Message, state: FSMContext):
    await upsert_user(message.from_user)
    await state.clear()
    await state.set_state(ApplyForm.nickname)
    await message.answer("Давай оформим анкету. Какой у тебя игровой никнейм?")


@router.message(ApplyForm.nickname)
async def apply_nickname(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) > 40:
        return await message.answer("Напиши никнейм текстом, до 40 символов.")
    await state.update_data(nickname=message.text.strip())
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=p) for p in POSITIONS[i:i+3]] for i in range(0, len(POSITIONS), 3)],
        resize_keyboard=True, one_time_keyboard=True
    )
    await state.set_state(ApplyForm.position)
    await message.answer("На какой позиции играешь?", reply_markup=kb)


@router.message(ApplyForm.position)
async def apply_position(message: Message, state: FSMContext):
    if not message.text or message.text.strip() not in POSITIONS:
        return await message.answer("Выбери позицию кнопкой ниже.")
    await state.update_data(position=message.text.strip())
    await state.set_state(ApplyForm.rating)
    await message.answer("Укажи рейтинг от 1.0 до 7.0. Можно дробный, например 4.3.")


@router.message(ApplyForm.rating)
async def apply_rating(message: Message, state: FSMContext):
    try:
        rating = float((message.text or "").replace(",", "."))
    except ValueError:
        return await message.answer("Нужен числовой рейтинг, например 4.5.")
    if not 1.0 <= rating <= 7.0:
        return await message.answer("Рейтинг должен быть в диапазоне от 1.0 до 7.0.")
    await state.update_data(rating=rating)
    await state.set_state(ApplyForm.experience)
    await message.answer("Какой у тебя опыт? Например: новичок, 1 сезон, 3 сезона.")


@router.message(ApplyForm.experience)
async def apply_experience(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) > 120:
        return await message.answer("Напиши коротко, до 120 символов.")
    await state.update_data(experience=message.text.strip())
    await state.set_state(ApplyForm.looking_for)
    await message.answer("Какой клуб ищешь? Можно написать желаемый уровень или «любой клуб».")


@router.message(ApplyForm.looking_for)
async def apply_finish(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) > 160:
        return await message.answer("Напиши пожелания текстом, до 160 символов.")
    data = await state.get_data()
    stamp = now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO players(user_id,nickname,position,rating,experience,looking_for,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,'pending',?,?)
               ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname, position=excluded.position,
               rating=excluded.rating, experience=excluded.experience, looking_for=excluded.looking_for,
               status='pending', updated_at=excluded.updated_at""",
            (message.from_user.id, data["nickname"], data["position"], data["rating"],
             data["experience"], message.text.strip(), stamp, stamp)
        )
        await db.commit()
        cur = await db.execute("SELECT id FROM players WHERE user_id=?", (message.from_user.id,))
        player_id = (await cur.fetchone())[0]
    await state.clear()
    await message.answer(
        f"✅ Анкета отправлена на модерацию!\nНомер заявки: <b>#{player_id}</b>.\n"
        "Она появится в ленте только после одобрения.",
        reply_markup=main_menu(is_admin(message.from_user.id))
    )
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                f"🛡 Новая анкета #{player_id}: <b>{data['nickname']}</b>, {data['position']}, "
                f"{data['rating']:.1f}/7.0.\nОдобрить: /approve {player_id}\nОтклонить: /reject {player_id}"
            )
        except Exception:
            pass


@router.message(Command("myprofile"))
@router.message(F.text == "👤 Моя анкета")
async def my_profile(message: Message):
    row = await get_player_by_user(message.from_user.id)
    if not row:
        return await message.answer("Анкеты пока нет. Нажми «🔎 Найти клуб» или отправь /apply.")
    status_names = {"pending": "на проверке", "approved": "одобрена", "rejected": "отклонена"}
    await message.answer(
        f"👤 <b>{row['nickname']}</b>\nПозиция: {row['position']}\n"
        f"Рейтинг: {row['rating']:.1f}/7.0\nОпыт: {row['experience']}\n"
        f"Ищет: {row['looking_for']}\nСтатус: <b>{status_names.get(row['status'], row['status'])}</b>\n"
        f"ID анкеты: <code>{row['id']}</code>"
    )


@router.message(Command("moderation"))
@router.message(F.text == "🛡 Модерация")
async def moderation(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Эта команда доступна только модераторам.")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM players WHERE status='pending' ORDER BY created_at LIMIT 10")
        rows = await cur.fetchall()
    if not rows:
        return await message.answer("🎉 Очередь пуста — все заявки проверены.")
    for row in rows:
        await send_profile_card(message, row)
        await message.answer(f"Заявка #{row['id']}: /approve {row['id']} или /reject {row['id']}")


@router.message(Command("approve"))
async def approve(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Только модератор может одобрять анкеты.")
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Формат: /approve ID")
    pid = int(parts[1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM players WHERE id=?", (pid,))
        row = await cur.fetchone()
        if not row:
            return await message.answer("Анкета не найдена.")
        await db.execute("UPDATE players SET status='approved', updated_at=? WHERE id=?", (now(), pid))
        await db.commit()
    try:
        await message.bot.send_message(row["user_id"], "✅ Твоя анкета одобрена и теперь видна менеджерам клубов!")
    except Exception:
        pass
    await message.answer(f"✅ Анкета #{pid} одобрена.")


@router.message(Command("reject"))
async def reject(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Только модератор может отклонять анкеты.")
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Формат: /reject ID")
    pid = int(parts[1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM players WHERE id=?", (pid,))
        row = await cur.fetchone()
        if not row:
            return await message.answer("Анкета не найдена.")
        await db.execute("UPDATE players SET status='rejected', updated_at=? WHERE id=?", (now(), pid))
        await db.commit()
    try:
        await message.bot.send_message(row["user_id"], "❌ Анкета отклонена модератором. Можешь подать её заново через /apply.")
    except Exception:
        pass
    await message.answer(f"Анкета #{pid} отклонена.")


@router.message(Command("registerclub"))
async def register_club(message: Message):
    if len(ADMIN_IDS) and message.from_user.id not in ADMIN_IDS:
        # Any user may register one club; an existing owner cannot register another.
        pass
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return await message.answer("Формат: /registerclub Название клуба")
    name = parts[1].strip()[:60]
    existing = await get_club_for_manager(message.from_user.id)
    if existing:
        return await message.answer(f"Ты уже менеджер клуба: <b>{existing['name']}</b>.")
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO clubs(name,owner_id,created_at) VALUES(?,?,?)",
                             (name, message.from_user.id, now()))
            await db.commit()
        await message.answer(f"🏆 Клуб <b>{name}</b> зарегистрирован! Теперь можешь просматривать игроков через /market.")
    except aiosqlite.IntegrityError:
        await message.answer("Такое название клуба уже занято. Попробуй другое.")


@router.message(Command("club"))
@router.message(F.text == "🏆 Мой клуб")
async def my_club(message: Message):
    row = await get_club_for_manager(message.from_user.id)
    if not row:
        return await message.answer("Ты пока не менеджер клуба. Создай его: /registerclub Название клуба")
    await message.answer(f"🏆 <b>{row['name']}</b>\nМенеджер: <code>{row['owner_id']}</code>\nЛента игроков: /market")


@router.message(Command("setmanager"))
async def set_manager(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Только администратор может назначать менеджеров.")
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].isdigit():
        return await message.answer("Формат: /setmanager USER_ID Название клуба")
    user_id, name = int(parts[1]), parts[2].strip()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO clubs(name,owner_id,created_at) VALUES(?,?,?)", (name, user_id, now()))
            await db.commit()
        except aiosqlite.IntegrityError:
            return await message.answer("Название занято. Для назначения используй уникальное название клуба.")
    try:
        await message.bot.send_message(user_id, f"🏆 Тебя назначили менеджером клуба <b>{name}</b> в RFTL.")
    except Exception:
        pass
    await message.answer(f"Менеджер клуба <b>{name}</b> назначен: <code>{user_id}</code>.")


@router.message(Command("market"))
@router.message(F.text == "📋 Лента игроков")
async def market(message: Message):
    club = await get_club_for_manager(message.from_user.id)
    if not club:
        return await message.answer("Лента игроков доступна менеджерам клубов. Зарегистрируй клуб: /registerclub Название")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM players WHERE status='approved' ORDER BY updated_at DESC LIMIT 20"
        )
        rows = await cur.fetchall()
    if not rows:
        return await message.answer("Пока нет одобренных анкет. Загляни позже!")
    await message.answer(f"📋 <b>Лента игроков для клуба {club['name']}</b>\nНайдено анкет: {len(rows)}")
    for row in rows:
        await send_profile_card(message, row, manager_club=club)


@router.callback_query(F.data.startswith("invite:"))
async def invite_player(callback: CallbackQuery, state: FSMContext):
    club = await get_club_for_manager(callback.from_user.id)
    if not club:
        return await callback.answer("Эта кнопка доступна менеджерам клубов.", show_alert=True)
    player_id = int(callback.data.split(":", 1)[1])
    row = await get_player_by_user(player_id)
    if not row or row["status"] != "approved":
        return await callback.answer("Эта анкета уже недоступна.", show_alert=True)
    await state.update_data(invite_player_id=player_id, invite_club_id=club["id"])
    await state.set_state(InviteForm.message)
    await callback.message.answer(
        f"Напиши короткое приглашение для игрока <b>{row['nickname']}</b> от клуба <b>{club['name']}</b>."
    )
    await callback.answer()


@router.message(InviteForm.message)
async def invite_send(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) > 500:
        return await message.answer("Напиши приглашение текстом, до 500 символов.")
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM clubs WHERE id=? AND owner_id=?", (data["invite_club_id"], message.from_user.id))
        club = await cur.fetchone()
        cur = await db.execute("SELECT * FROM players WHERE user_id=? AND status='approved'", (data["invite_player_id"],))
        player = await cur.fetchone()
        if not club or not player:
            await state.clear()
            return await message.answer("Не удалось отправить приглашение: клуб или анкета уже недоступны.")
        cur = await db.execute(
            "INSERT INTO invitations(club_id,player_user_id,manager_id,message,status,created_at) VALUES(?,?,?,?, 'sent', ?)",
            (club["id"], player["user_id"], message.from_user.id, message.text.strip(), now())
        )
        invite_id = cur.lastrowid
        await db.commit()
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Интересно", callback_data=f"invite_reply:{invite_id}:yes"),
            InlineKeyboardButton(text="❌ Не интересно", callback_data=f"invite_reply:{invite_id}:no")
        ]])
        await message.bot.send_message(
            player["user_id"],
            f"📩 <b>Приглашение от клуба {club['name']}</b>\n\n{message.text.strip()}\n\n"
            "Это приглашение начать переговоры, не официальный трансфер.",
            reply_markup=kb
        )
        await message.answer(f"✅ Приглашение отправлено! Номер: #{invite_id}.")
    except Exception:
        await message.answer("Приглашение записано, но игроку не удалось написать. Пусть он сначала запустит бота командой /start.")
    await state.clear()


@router.callback_query(F.data.startswith("invite_reply:"))
async def invitation_reply(callback: CallbackQuery):
    _, invite_id_s, answer = callback.data.split(":")
    invite_id = int(invite_id_s)
    new_status = "interested" if answer == "yes" else "declined"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT i.*, c.name AS club_name FROM invitations i
               JOIN clubs c ON c.id=i.club_id WHERE i.id=? AND i.player_user_id=?""",
            (invite_id, callback.from_user.id)
        )
        row = await cur.fetchone()
        if not row:
            return await callback.answer("Приглашение не найдено.", show_alert=True)
        await db.execute("UPDATE invitations SET status=? WHERE id=?", (new_status, invite_id))
        await db.commit()
    try:
        await callback.bot.send_message(
            row["manager_id"],
            f"📩 Игрок ответил на приглашение #{invite_id} от клуба {row['club_name']}: "
            f"{'готов обсудить предложение' if answer == 'yes' else 'отказался'}."
        )
    except Exception:
        pass
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Ответ отправлен!")
    await callback.message.answer("Готово! Менеджер клуба получит твой ответ.")


@router.message(Command("invitations"))
@router.message(F.text == "✉️ Приглашения")
async def invitations(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT i.*, c.name AS club_name FROM invitations i
               JOIN clubs c ON c.id=i.club_id
               WHERE i.player_user_id=? OR i.manager_id=?
               ORDER BY i.created_at DESC LIMIT 10""",
            (message.from_user.id, message.from_user.id)
        )
        rows = await cur.fetchall()
    if not rows:
        return await message.answer("Пока приглашений нет.")
    status_map = {"sent": "ожидает ответа", "interested": "игрок заинтересован", "declined": "отказ"}
    for r in rows:
        side = "получено" if r["player_user_id"] == message.from_user.id else "отправлено"
        await message.answer(
            f"✉️ Приглашение #{r['id']} ({side})\nКлуб: {r['club_name']}\n"
            f"Статус: {status_map.get(r['status'], r['status'])}\nТекст: {r['message']}"
        )



@router.message(Command("transfer_offer"))
async def transfer_offer_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(TransferForm.player_name)
    await message.answer("Создадим трансферное предложение. Напиши имя игрока.")


@router.message(TransferForm.player_name)
async def transfer_offer_player(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) > 100:
        return await message.answer("Напиши имя игрока, до 100 символов.")
    await state.update_data(player_name=message.text.strip())
    await state.set_state(TransferForm.from_club)
    await message.answer("Из какого клуба уходит игрок?")


@router.message(TransferForm.from_club)
async def transfer_offer_from(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) > 80:
        return await message.answer("Напиши название текущего клуба.")
    await state.update_data(from_club=message.text.strip())
    await state.set_state(TransferForm.to_club)
    await message.answer("В какой клуб переходит игрок?")


@router.message(TransferForm.to_club)
async def transfer_offer_to(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) > 80:
        return await message.answer("Напиши название клуба-получателя.")
    await state.update_data(to_club=message.text.strip())
    await state.set_state(TransferForm.terms)
    await message.answer("Укажи тип и условия. Например: «Аренда до конца сезона, бесплатно».")


@router.message(TransferForm.terms)
async def transfer_offer_terms(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) > 500:
        return await message.answer("Условия должны быть текстом до 500 символов.")
    data = await state.get_data()
    offer_type = "аренда" if "аренд" in message.text.lower() else "трансфер"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO transfer_offers(player_name,from_club,to_club,offer_type,terms,status,created_at)
               VALUES(?,?,?,?,?,'pending',?)""",
            (data["player_name"], data["from_club"], data["to_club"], offer_type, message.text.strip(), now())
        )
        offer_id = cur.lastrowid
        await db.commit()
    await state.clear()
    await message.answer(f"📝 Предложение #{offer_id} отправлено администраторам на проверку.")
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                f"📝 Новое предложение #{offer_id}\nИгрок: {data['player_name']}\n"
                f"{data['from_club']} → {data['to_club']}\nТип: {offer_type}\n"
                f"Условия: {message.text.strip()}\n"
                f"Решение: /transfer_decide {offer_id} approve или reject"
            )
        except Exception:
            pass


@router.message(Command("pending_transfers"))
async def pending_transfers(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Только администратор может смотреть очередь трансферов.")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM transfer_offers WHERE status='pending' ORDER BY id DESC LIMIT 20")
        rows = await cur.fetchall()
    if not rows:
        return await message.answer("Нет предложений на проверке.")
    for r in rows:
        await message.answer(
            f"📝 Предложение #{r['id']}\nИгрок: {r['player_name']}\n"
            f"Откуда: {r['from_club']}\nКуда: {r['to_club']}\nТип: {r['offer_type']}\n"
            f"Условия: {r['terms']}\n/transfer_decide {r['id']} approve или reject"
        )


@router.message(Command("transfer_decide"))
async def transfer_decide(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Только администратор может принимать решения по предложениям.")
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ("approve", "reject"):
        return await message.answer("Формат: /transfer_decide ID approve|reject")
    tid = int(parts[1])
    status = "approved" if parts[2] == "approve" else "rejected"
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("UPDATE transfer_offers SET status=? WHERE id=? AND status='pending'", (status, tid))
        await db.commit()
        if cur.rowcount == 0:
            return await message.answer("Предложение не найдено или уже обработано.")
    await message.answer(f"Предложение #{tid}: {'одобрено' if status == 'approved' else 'отклонено'}.")
    if status == "approved":
        await message.answer("После проверки администратор может опубликовать новость о сделке вручную. Автопубликация будет следующим этапом.")


@router.message(F.text == "📜 Трансферы")
async def transfer_history(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM transfer_offers WHERE status='approved' ORDER BY id DESC LIMIT 10")
        rows = await cur.fetchall()
    if not rows:
        return await message.answer("Пока нет подтверждённых трансферных предложений.")
    for r in rows:
        await message.answer(
            f"⚽ <b>{r['player_name']}</b>\n{r['from_club']} → {r['to_club']}\n"
            f"{r['offer_type'].capitalize()}: {r['terms']}"
        )


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Добавь его в Variables на Railway.")
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
