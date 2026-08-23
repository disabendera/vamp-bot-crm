import asyncio
import calendar as _calendar
import re
from datetime import date

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
                           InlineKeyboardButton, InputMediaPhoto,
                           ReplyKeyboardMarkup, KeyboardButton)

import db
import keyboards as kb
from config import ADMIN_ID
from services.google_sheets import send_interview_to_sheet

router = Router()

STATUS_TITLES = {"active": "Активные ✅", "dropped": "Слив 🚫"}


class Forms(StatesGroup):
    wallet = State()
    interview_position = State()
    interview_partner = State()
    interview_form = State()
    interview_date = State()
    interview_time = State()
    interview_comment = State()
    interview_photos = State()
    interview_confirm = State()
    model_name = State()
    model_phone = State()
    model_username = State()


def menu_for(role: str):
    if role == "admin":
        return kb.admin_menu
    if role == "mentor":
        return kb.mentor_menu
    if role == "leader":
        return kb.leader_menu
    return kb.main_menu


async def require_access(message: Message) -> object | None:
    """Возвращает user, если доступ есть; иначе отвечает и возвращает None"""
    user = await db.get_user(message.from_user.id)
    if not user or user["role"] == "banned":
        await message.answer("⛔ Доступ закрыт. Нажмите /start, чтобы подать заявку")
        return None
    await db.refresh_user_info(
        message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    if user["role"] == "pending":
        await message.answer("⏳ Ваша заявка на рассмотрении у наставника. Ожидайте")
        return None
    return user


# ---------- /start и заявка ----------

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    tg_id = message.from_user.id
    user = await db.get_user(tg_id)

    if user is None:
        role = "admin" if tg_id == ADMIN_ID else "pending"
        await db.create_user(tg_id, message.from_user.username, message.from_user.full_name, role)
        if role == "admin":
            await message.answer("👑 Вы администратор. Добро пожаловать!", reply_markup=kb.admin_menu)
            return
        # уведомляем наставников о новой заявке
        staff = await db.get_staff_ids()
        text = (f"📥 Новая заявка на вступление\n"
                f"Имя: {message.from_user.full_name}\n"
                f"Юзернейм: @{message.from_user.username or '-'}\n"
                f"ID: {tg_id}")
        for sid in staff:
            try:
                await bot.send_message(sid, text, reply_markup=kb.approve_kb(tg_id))
            except Exception:
                pass
        await message.answer(
            "🔒 Это закрытый бот. Заявка отправлена наставнику - "
            "как только её одобрят, вам откроется доступ"
        )
        return

    if user["role"] == "pending":
        await message.answer("⏳ Заявка ещё на рассмотрении. Ожидайте решения наставника")
    elif user["role"] == "banned":
        await message.answer("⛔ Доступ отклонён")
    else:
        await message.answer("Главное меню:", reply_markup=menu_for(user["role"]))


# ---------- Профиль ----------

@router.message(F.text == "👤 Профиль")
async def profile(message: Message):
    user = await require_access(message)
    if not user:
        return
    team = await db.get_team(user["team_id"]) if user["team_id"] else None
    agent_name = f"Команда {team['name']}" if team else (user["full_name"] or "")
    wallet = user["wallet"] or "не указаны - кнопка «💳 Кошелек»"
    pending = user["pending"] if "pending" in user.keys() else 0.0
    await message.answer(
        f"» <b>Твой профиль</b>\n\n"
        f"👤 <b>Агент:</b> {agent_name}\n\n"
        f"✅ <b>Агентский айди:</b> <code>{user['tg_id']}</code>\n\n"
        f"💳 <b>Кошелек:</b> <code>{wallet}</code>\n\n"
        f"💵 <b>USDT:</b> {user['balance']} ({pending} за эту неделю)\n\n"
        f"📗 Выплаты средств теперь доступны от суммы 50$, "
        f"всё, что меньше, остаётся в накоплениях\n\n"
        f"👉 <a href=\"https://www.bestchange.com/\">КАК ПОМЕНЯТЬ КРИПТУ НА ВАЛЮТУ?</a>\n\n"
        f"🟢 Выплаты производятся в долларах USDT BEP-20 "
        f"<a href=\"https://www.binance.com/ru/square/post/950859\">(как можно получить кошелек?)</a>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ---------- Чат агентов ----------

@router.message(F.text == "🟢 Чат агентов")
async def agents_chat(message: Message):
    user = await require_access(message)
    if not user:
        return
    link = await db.get_setting("agents_chat_link")
    if link:
        await message.answer(f"🟢 Чат агентов: {link}")
    else:
        await message.answer("Ссылка на чат агентов ещё не настроена. Обратитесь к наставнику")


# ---------- Условия работы ----------

@router.message(F.text == "✳️ Условия работы")
async def work_terms(message: Message):
    user = await require_access(message)
    if not user:
        return
    await message.answer("✳️ Условия работы - выберите раздел:", reply_markup=kb.work_terms_menu)


@router.message(F.text == "💼 Офферы партнёрки")
async def show_offers(message: Message):
    user = await require_access(message)
    if not user:
        return
    text = await db.get_setting("partner_offers")
    if text:
        await message.answer(f"💼 <b>Офферы партнёрки</b>\n\n{text}",
                             parse_mode="HTML", disable_web_page_preview=True)
    else:
        await message.answer("💼 Офферы партнёрки скоро появятся здесь")


@router.message(F.text == "✳️ Условия сети")
async def show_network_terms(message: Message):
    user = await require_access(message)
    if not user:
        return
    text = await db.get_setting("network_terms")
    if text:
        await message.answer(f"✳️ <b>Условия сети</b>\n\n{text}",
                             parse_mode="HTML", disable_web_page_preview=True)
    else:
        await message.answer("✳️ Условия сети скоро появятся здесь")


# ---------- Запись на собеседование ----------

MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

FORM_PROMPT = (
    "🟢 Отправьте одним сообщением (12 строк):\n"
    "1) Имя\n"
    "2) Возраст\n"
    "3) Номер\n"
    "4) Телеграм\n"
    "5) Гражданство\n"
    "6) Проживание (страна, город)\n"
    "7) Проживает с кем\n"
    "8) Модель телефона\n"
    "9) Второе устройство\n"
    "10) Готова приступить (когда)\n"
    "11) Сколько готова уделять времени\n"
    "12) Понимание сферы (оценка из 10)"
)

CANCEL_BTN = "🔴 Отмена"

position_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💃 Модель"), KeyboardButton(text="👨 Оператор")],
        [KeyboardButton(text=CANCEL_BTN)],
    ],
    resize_keyboard=True,
)

cancel_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=CANCEL_BTN)]],
    resize_keyboard=True,
)

comment_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="⏭ Пропустить")], [KeyboardButton(text=CANCEL_BTN)]],
    resize_keyboard=True,
)

photos_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="✅ Готово")], [KeyboardButton(text=CANCEL_BTN)]],
    resize_keyboard=True,
)

confirm_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📤 Отправить")], [KeyboardButton(text=CANCEL_BTN)]],
    resize_keyboard=True,
)


def partners_menu(partners) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=f"🤝 {pt['name']}")] for pt in partners]
    rows.append([KeyboardButton(text=CANCEL_BTN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def build_calendar(year: int, month: int) -> InlineKeyboardMarkup:
    today = date.today()
    rows = [[InlineKeyboardButton(text=f"{MONTHS_RU[month - 1]} {year}", callback_data="cal:noop")]]
    rows.append([InlineKeyboardButton(text=d, callback_data="cal:noop") for d in WEEKDAYS_RU])
    for week in _calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if not day:
                row.append(InlineKeyboardButton(text=" ", callback_data="cal:noop"))
            elif date(year, month, day) < today:
                row.append(InlineKeyboardButton(text="✖️", callback_data="cal:past"))
            else:
                row.append(InlineKeyboardButton(text=str(day), callback_data=f"cal:day:{year}:{month}:{day}"))
        rows.append(row)
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    rows.append([
        InlineKeyboardButton(text="<", callback_data=f"cal:nav:{prev_y}:{prev_m}"),
        InlineKeyboardButton(text="Отмена", callback_data="icancel"),
        InlineKeyboardButton(text=">", callback_data=f"cal:nav:{next_y}:{next_m}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_time_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for hour in range(8, 24):
        row.append(InlineKeyboardButton(text=f"{hour:02d}:00", callback_data=f"itime:{hour:02d}:00"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "✅ Запись на собеседование")
async def interview_start(message: Message, state: FSMContext):
    user = await require_access(message)
    if not user:
        return
    await state.clear()
    await state.set_state(Forms.interview_position)
    await message.answer("✅ Кого записываем на собеседование?", reply_markup=position_menu)


@router.message(Forms.interview_position, F.text.in_({"💃 Модель", "👨 Оператор"}))
async def interview_position(message: Message, state: FSMContext):
    position = "Модель" if "Модель" in message.text else "Оператор"
    await state.update_data(position=position)
    if position == "Модель":
        partners = await db.get_partners()
        if not partners:
            await state.clear()
            user = await db.get_user(message.from_user.id)
            return await message.answer(
                "Партнёры пока не настроены. Обратитесь к администратору",
                reply_markup=menu_for(user["role"]),
            )
        await state.set_state(Forms.interview_partner)
        await message.answer("🤝 Выберите партнёра:", reply_markup=partners_menu(partners))
    else:
        await state.update_data(partner="-")
        await state.set_state(Forms.interview_form)
        await message.answer(
            f"👨 Позиция: <b>Оператор</b>\n\n{FORM_PROMPT}",
            parse_mode="HTML",
            reply_markup=cancel_menu,
        )


@router.message(Forms.interview_position)
async def interview_position_invalid(message: Message):
    await message.answer("Выберите позицию кнопкой ниже 👇", reply_markup=position_menu)


@router.message(Forms.interview_partner)
async def interview_partner(message: Message, state: FSMContext):
    name = (message.text or "").removeprefix("🤝 ").strip()
    partners = {pt["name"]: pt for pt in await db.get_partners()}
    if name not in partners:
        return await message.answer(
            "Выберите партнёра кнопкой ниже 👇",
            reply_markup=partners_menu(list(partners.values())),
        )
    await state.update_data(partner=name)
    await state.set_state(Forms.interview_form)
    await message.answer(
        f"🤝 Партнёр: <b>{name}</b>\n\n{FORM_PROMPT}",
        parse_mode="HTML",
        reply_markup=cancel_menu,
    )


@router.message(Forms.interview_form)
async def interview_form(message: Message, state: FSMContext):
    lines = [l.strip() for l in (message.text or "").split("\n") if l.strip()]
    if len(lines) < 12:
        return await message.answer(
            f"❌ Получено строк: {len(lines)}, а нужно 12 - по одной на каждый пункт.\n\n{FORM_PROMPT}"
        )
    raw_age_str = lines[1].strip()
    if raw_age_str.isdigit():
        age_val = int(raw_age_str)
    else:
        # удаляем нумерацию вида "2)" или "2." в начале строки
        age_line = re.sub(r"^\d+[).]\s*", "", raw_age_str)
        age_match = re.search(r"\d+", age_line) or re.search(r"\d+", raw_age_str)
        age_val = int(age_match.group()) if age_match else None

    if age_val is None:
        return await message.answer(
            "❌ Не понял возраст в строке 2. Укажите возраст числом и отправьте анкету заново"
        )
    if age_val < 18:
        await state.clear()
        user = await db.get_user(message.from_user.id)
        return await message.answer(
            "🔞 Работаем только с совершеннолетними (18+). Заявка не может быть отправлена",
            reply_markup=menu_for(user["role"]),
        )
    await state.update_data(form=message.text)
    await state.set_state(Forms.interview_date)
    today = date.today()
    await message.answer("📅 Выберите дату:", reply_markup=build_calendar(today.year, today.month))


@router.message(Forms.interview_date)
async def interview_date_text(message: Message):
    await message.answer("Выберите дату в календаре выше 👆")


@router.callback_query(F.data == "cal:noop")
async def cal_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data == "cal:past")
async def cal_past(call: CallbackQuery):
    await call.answer("❌ Нельзя записывать на прошедшую дату", show_alert=True)


@router.callback_query(F.data.startswith("cal:nav:"))
async def cal_nav(call: CallbackQuery):
    _, _, year, month = call.data.split(":")
    await call.message.edit_reply_markup(reply_markup=build_calendar(int(year), int(month)))
    await call.answer()


@router.callback_query(F.data.startswith("cal:day:"))
async def cal_day(call: CallbackQuery, state: FSMContext):
    _, _, year, month, day = call.data.split(":")
    if date(int(year), int(month), int(day)) < date.today():
        return await call.answer("❌ Нельзя записывать на прошедшую дату", show_alert=True)
    chosen = f"{int(day):02d}.{int(month):02d}.{year}"
    await state.update_data(date=chosen)
    await state.set_state(Forms.interview_time)
    await call.message.edit_text(f"📅 Дата собеседования: <b>{chosen}</b>", parse_mode="HTML")
    await call.message.answer("🕐 Выберите время (МСК):", reply_markup=build_time_kb())
    await call.answer()


@router.message(Forms.interview_time)
async def interview_time_text(message: Message):
    await message.answer("Выберите время кнопкой выше 👆")


@router.callback_query(F.data.startswith("itime:"))
async def interview_time(call: CallbackQuery, state: FSMContext):
    _, hh, mm = call.data.split(":")
    await state.update_data(time=f"{hh}:{mm}")
    await state.set_state(Forms.interview_comment)
    await call.message.edit_text(f"🕐 Время собеседования: <b>{hh}:{mm} МСК</b>", parse_mode="HTML")
    await call.message.answer(
        "✍️ Добавьте краткое описание от себя одним сообщением:",
        reply_markup=comment_menu,
    )
    await call.answer()


async def after_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("position") == "Модель":
        await state.update_data(photos=[])
        await state.set_state(Forms.interview_photos)
        await message.answer(
            "📸 Отправьте от 1 до 3 фото модели.\n\n"
            "❗️ Важно: фото должны быть <b>в полный рост</b>",
            parse_mode="HTML",
            reply_markup=photos_menu,
        )
    else:
        await show_preview(message, state)


@router.message(Forms.interview_comment, F.text == "⏭ Пропустить")
async def interview_skip_comment(message: Message, state: FSMContext):
    await state.update_data(comment="")
    await after_comment(message, state)


@router.message(Forms.interview_comment)
async def interview_comment(message: Message, state: FSMContext):
    await state.update_data(comment=message.text.strip())
    await after_comment(message, state)


@router.message(Forms.interview_photos, F.photo)
async def interview_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) >= 3:
        return await message.answer("Уже есть 3 фото - нажмите «✅ Готово»",
                                    reply_markup=photos_menu)
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    if len(photos) == 3:
        await message.answer("📸 Получено 3/3 фото")
        await show_preview(message, state)
    else:
        await message.answer(
            f"📸 Фото {len(photos)}/3 получено. Отправьте ещё или нажмите «✅ Готово»",
            reply_markup=photos_menu,
        )


@router.message(Forms.interview_photos, F.text == "✅ Готово")
async def interview_photos_done(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("photos"):
        return await message.answer("Нужно хотя бы одно фото в полный рост 📸",
                                    reply_markup=photos_menu)
    await show_preview(message, state)


@router.message(Forms.interview_photos)
async def interview_photo_invalid(message: Message):
    await message.answer("Отправьте фото (от 1 до 3, в полный рост) или нажмите «✅ Готово»",
                         reply_markup=photos_menu)


async def show_preview(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Forms.interview_confirm)
    comment_block = f"\n\n✍️ <b>От агента:</b>\n{data['comment']}" if data.get("comment") else ""
    photos_line = f"\n📸 Фото: {len(data['photos'])} шт" if data.get("photos") else ""
    await message.answer(
        f"📋 <b>Проверьте заявку</b>\n\n"
        f"Позиция: <b>{data.get('position', '-')}</b>\n"
        f"🤝 Партнёр: <b>{data.get('partner', '-')}</b>\n"
        f"📅 Собес: <b>{data.get('date', '-')} в {data.get('time', '-')} МСК</b>"
        f"{photos_line}\n\n"
        f"{data['form']}"
        f"{comment_block}",
        parse_mode="HTML",
        reply_markup=confirm_menu,
    )


@router.message(Forms.interview_confirm, F.text == "📤 Отправить")
async def interview_send(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not data.get("form"):
        await state.clear()
        return await message.answer("Заявка устарела, начните заново")
    await state.clear()
    comment_block = f"\n\n✍️ От агента:\n{data['comment']}" if data.get("comment") else ""
    header = (f"Позиция: {data.get('position', '-')}\n"
              f"Собес: {data.get('date', '-')} в {data.get('time', '-')} МСК")
    full_text = f"{header}\n\n{data['form']}{comment_block}"
    
    interview_id = await db.add_interview(
        message.from_user.id, full_text, data.get("partner", ""), data.get("position", "")
    )
    photos = data.get("photos", [])
    staff_msg = (f"✅ Заявка на собеседование №{interview_id} → {data.get('partner', '-')}\n"
                 f"От агента: @{message.from_user.username or '-'} | ID: {message.from_user.id}\n\n{full_text}")
    
    for sid in await db.get_staff_ids():
        try:
            if photos and len(staff_msg) <= 1024:
                media = [InputMediaPhoto(media=photos[0], caption=staff_msg)]
                media += [InputMediaPhoto(media=fid) for fid in photos[1:]]
                await bot.send_media_group(sid, media)
            elif photos:
                await bot.send_media_group(sid, [InputMediaPhoto(media=fid) for fid in photos])
                await bot.send_message(sid, staff_msg)
            else:
                await bot.send_message(sid, staff_msg)
        except Exception:
            pass

    # Интеграция с чатами и Google Таблицей (ТОЛЬКО для позиций "Модель")
    if data.get("position") == "Модель" and data.get("partner"):
        partner_obj = await db.get_partner_by_name(data.get("partner"))
        if partner_obj:
            # 1. Отправка в чат партнёра (если указан chat_id)
            if partner_obj["chat_id"]:
                partner_chat_id = partner_obj["chat_id"].strip()
                partner_msg = (
                    f"📋 <b>Новая заявка на собеседование №{interview_id}</b>\n"
                    f"👤 <b>Агент:</b> @{message.from_user.username or '-'} (ID: <code>{message.from_user.id}</code>)\n"
                    f"🤝 <b>Партнёр:</b> {partner_obj['name']}\n"
                    f"📅 <b>Дата и время:</b> {data.get('date', '-')} в {data.get('time', '-')} МСК\n\n"
                    f"<b>Анкета:</b>\n{data['form']}"
                    f"{comment_block}"
                )
                try:
                    if photos and len(partner_msg) <= 1024:
                        media = [InputMediaPhoto(media=photos[0], caption=partner_msg, parse_mode="HTML")]
                        media += [InputMediaPhoto(media=fid) for fid in photos[1:]]
                        await bot.send_media_group(partner_chat_id, media)
                    elif photos:
                        await bot.send_media_group(partner_chat_id, [InputMediaPhoto(media=fid) for fid in photos])
                        await bot.send_message(partner_chat_id, partner_msg, parse_mode="HTML")
                    else:
                        await bot.send_message(partner_chat_id, partner_msg, parse_mode="HTML")
                except Exception as err:
                    print(f"Ошибка при отправке в чат партнёра {partner_chat_id}: {err}")

            # 2. Отправка в Google Таблицу партнёра (если указан sheet_url)
            if partner_obj["sheet_url"]:
                sheet_payload = {
                    "interview_id": interview_id,
                    "created_at": str(date.today()),
                    "agent_username": message.from_user.username or "",
                    "agent_id": message.from_user.id,
                    "partner": partner_obj["name"],
                    "position": "Модель",
                    "date": data.get("date", ""),
                    "time": data.get("time", ""),
                    "form": data.get("form", ""),
                    "comment": data.get("comment", ""),
                    "status": "Не подтверждена",
                }
                asyncio.create_task(send_interview_to_sheet(partner_obj["sheet_url"], sheet_payload))

    user = await db.get_user(message.from_user.id)
    await message.answer(f"✅ Запись выполнена (Заявка №{interview_id})", reply_markup=menu_for(user["role"]))



@router.message(Forms.interview_confirm)
async def interview_confirm_text(message: Message):
    await message.answer("Нажмите «📤 Отправить» или «🔴 Отмена» 👇", reply_markup=confirm_menu)


@router.callback_query(F.data == "icancel")
async def interview_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    user = await db.get_user(call.from_user.id)
    role = user["role"] if user else "agent"
    await call.message.answer("🔴 Действие отменено", reply_markup=menu_for(role))
    await call.answer()


# ---------- Обучение ----------

@router.message(F.text == "📗 Обучение")
async def training(message: Message):
    user = await require_access(message)
    if not user:
        return
    materials = await db.get_materials()
    await message.answer("📗 Выберите урок:", reply_markup=kb.materials_kb(materials))


@router.callback_query(F.data.startswith("mat:"))
async def show_material(call: CallbackQuery):
    material = await db.get_material(int(call.data.split(":")[1]))
    if material:
        await call.message.answer(f"📗 <b>{material['title']}</b>\n\n{material['content']}", parse_mode="HTML")
    await call.answer()


# ---------- Реквизиты ----------

WALLET_INFO = (
    "<b>Кошелек для получения выплат</b>\n\n"
    "Текущий кошелек: <code>{current}</code>\n\n"
    "Если у вас еще нет адреса кошелька, воспользуйтесь одной из инструкций:\n\n"
    "👉 <a href=\"https://www.binance.com/ru/square/post/950859\">"
    "УЗНАТЬ КАК ПОЛУЧИТЬ АДРЕС КОШЕЛЬКА USDT BEP-20 на Trust Wallet?</a>\n\n"
    "👉 <a href=\"https://youtu.be/fKEAWs0w0r0\">"
    "КАК ВЫВОДИТЬ НА ЛЮБУЮ ВАЛЮТУ (карта/наличные)?</a>\n\n"
    "Обратите внимание: использование Telegram Wallet не рекомендуется. "
    "Для хранения и получения выплат лучше использовать надежные решения, "
    "такие как Trust Wallet, SafePal, MetaMask, Ledger "
    "(наиболее безопасный вариант - холодные кошельки)\n\n"
    "<b>Отправка адреса кошелька</b>\n\n"
    "Пришлите адрес своего кошелька в ответном сообщении в следующем формате:\n"
    "<code>0x........................</code>\n\n"
    "<b>Важная информация</b>\n\n"
    "⚠️ Если вы изменили адрес кошелька, обязательно отправьте новый адрес кошелька "
    "до 00:00 (UTC+3).\n\n"
    "Если адрес будет обновлен позже указанного времени (в том числе в ночь "
    "с субботы на воскресенье после 00:00 UTC+3), перевод может быть выполнен "
    "на ранее указанный кошелек\n\n"
    "PRIME PARTNERS не несет ответственности за выплаты, отправленные на неверно "
    "предоставленные или несвоевременно обновленные адреса кошельков"
)


@router.message(F.text == "💳 Кошелек")
async def wallet_start(message: Message, state: FSMContext):
    user = await require_access(message)
    if not user:
        return
    current = user["wallet"] or "не указаны"
    await state.set_state(Forms.wallet)
    await message.answer(
        WALLET_INFO.format(current=current),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=kb.cancel_kb,
    )


@router.message(Forms.wallet)
async def wallet_save(message: Message, state: FSMContext):
    wallet = (message.text or "").strip()
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", wallet):
        return await message.answer(
            "❌ Неверный формат адреса. Нужен адрес USDT BEP-20 вида:\n"
            "<code>0x</code> + 40 символов (буквы a–f и цифры).\n\n"
            "Проверьте адрес и отправьте ещё раз",
            parse_mode="HTML",
        )
    await state.clear()
    await db.set_wallet(message.from_user.id, wallet)
    await message.answer("✅ Кошелек сохранен")


# ---------- Мои модели ----------

PAGE_SIZE = 10


def render_models_page(models, status: str, page: int) -> str:
    title = "✅" if status == "active" else "🚫"
    head = ("💵 » <b>Модели с активным статусом</b> ✅"
            if status == "active" else "🚫 » <b>Модели со статусом «Слив»</b>")
    start = page * PAGE_SIZE
    chunk = models[start:start + PAGE_SIZE]
    lines = []
    for i, m in enumerate(chunk, start=start + 1):
        phone = f" {m['phone']}" if m["phone"] else ""
        uname = f" @{m['username'].lstrip('@')}" if m["username"] else ""
        lines.append(f"{i}. <b>{m['name']}</b>{phone}{uname} - {m['shifts']} смен(-ы)")
    total_pages = (len(models) - 1) // PAGE_SIZE + 1
    return (f"{head}\n\n" + "\n".join(lines) +
            f"\n\nСтраница: {page + 1}/{total_pages}\n"
            f"Карточка модели: /model_НОМЕР (например /model_{chunk[0]['id']})")


async def send_models_list(target_message, tg_id: int, status: str, page: int, edit: bool = False):
    models = await db.get_models(tg_id, status)
    if not models:
        text, markup = f"{STATUS_TITLES[status]}: список пуст", None
    else:
        page = max(0, min(page, (len(models) - 1) // PAGE_SIZE))
        text = render_models_page(models, status, page)
        markup = kb.models_page_kb(
            status, page,
            has_prev=page > 0,
            has_next=(page + 1) * PAGE_SIZE < len(models),
        )
    if edit:
        await target_message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target_message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.message(F.text == "💵 Мои модели")
async def my_models(message: Message):
    user = await require_access(message)
    if not user:
        return
    await message.answer("💵 Выбери статус моделей:", reply_markup=kb.models_status_kb)


@router.callback_query(F.data.startswith("models:"))
async def models_by_status(call: CallbackQuery, state: FSMContext):
    action = call.data.split(":")[1]
    if action == "add":
        await state.set_state(Forms.model_name)
        await call.message.answer("1/3 - Введите имя модели:", reply_markup=kb.cancel_kb)
    elif action == "back":
        await call.message.edit_text("💵 Выбери статус моделей:", reply_markup=kb.models_status_kb)
    else:
        await send_models_list(call.message, call.from_user.id, action, page=0)
    await call.answer()


@router.callback_query(F.data.startswith("mpage:"))
async def models_page(call: CallbackQuery):
    _, status, page = call.data.split(":")
    await send_models_list(call.message, call.from_user.id, status, int(page), edit=True)
    await call.answer()


# --- добавление модели в 3 шага ---

@router.message(Forms.model_name)
async def model_add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(Forms.model_phone)
    await message.answer("2/3 - Телефон модели (или «-», если нет):", reply_markup=kb.cancel_kb)


@router.message(Forms.model_phone)
async def model_add_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    await state.update_data(phone="" if phone == "-" else phone)
    await state.set_state(Forms.model_username)
    await message.answer("3/3 - Юзернейм в Telegram (или «-», если нет):", reply_markup=kb.cancel_kb)


@router.message(Forms.model_username)
async def model_add_username(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    username = message.text.strip()
    await db.add_model(
        message.from_user.id, data["name"], data.get("phone", ""),
        "" if username == "-" else username.lstrip("@"),
    )
    await message.answer("✅ Модель добавлена со статусом «Активные»")


# --- карточка модели ---

@router.message(F.text.regexp(r"^/model_(\d+)$"))
async def model_card(message: Message):
    user = await require_access(message)
    if not user:
        return
    model_id = int(message.text.split("_")[1])
    m = await db.get_model_by_id(model_id, message.from_user.id)
    if not m:
        return await message.answer("Модель не найдена среди ваших")
    await message.answer(render_model_card(m), parse_mode="HTML",
                         reply_markup=kb.model_card_kb(m["id"], m["status"]))


def render_model_card(m) -> str:
    phone = m["phone"] or "-"
    uname = f"@{m['username']}" if m["username"] else "-"
    return (f"{STATUS_TITLES[m['status']]}\n\n"
            f"💵 <b>{m['name']}</b>\n"
            f"📞 Телефон: {phone}\n"
            f"🟢 Telegram: {uname}\n"
            f"🟩 Смен: {m['shifts']}")


@router.callback_query(F.data.startswith("shift:"))
async def model_shift(call: CallbackQuery):
    _, model_id, delta = call.data.split(":")
    await db.inc_shift(int(model_id), call.from_user.id, int(delta))
    m = await db.get_model_by_id(int(model_id), call.from_user.id)
    if m:
        await call.message.edit_text(render_model_card(m), parse_mode="HTML",
                                     reply_markup=kb.model_card_kb(m["id"], m["status"]))
    await call.answer("Смены обновлены ✅")


@router.callback_query(F.data.startswith("mstat:"))
async def model_status(call: CallbackQuery):
    _, model_id, status = call.data.split(":")
    await db.set_model_status(int(model_id), call.from_user.id, status)
    m = await db.get_model_by_id(int(model_id), call.from_user.id)
    if m:
        await call.message.edit_text(render_model_card(m), parse_mode="HTML",
                                     reply_markup=kb.model_card_kb(m["id"], m["status"]))
    await call.answer("Статус обновлён ✅")


# ---------- Ловушка для нераспознанных сообщений (ВСЕГДА последним) ----------

@router.message(StateFilter(None), F.text)
async def unknown_message(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user or user["role"] in ("pending", "banned"):
        await message.answer("🔒 Доступ закрыт. Нажмите /start, чтобы подать заявку")
        return
    await message.answer(
        "Не узнал команду 🤔 Похоже, у вас старое меню - вот актуальное:",
        reply_markup=menu_for(user["role"]),
    )


# ---------- Онбординг: порядок действий после входа ----------

ONB_SEP = "━━━━━━━━━━━━━━━"


class OnbForms(StatesGroup):
    wallet = State()


def _onb_kb(text: str, step: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data=f"onb:{step}")
    ]])


async def send_onb_step(bot: Bot, chat_id: int, step: int):
    if step == 1:
        terms = await db.get_setting("network_terms") or "Текст условий появится позже"
        await bot.send_message(
            chat_id,
            f"📋 <b>ШАГ 1/5 · УСЛОВИЯ РАБОТЫ СЕТИ</b>\n{ONB_SEP}\n{terms}",
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=_onb_kb("✅ Ознакомился", "2"),
        )
    elif step == 2:
        offers = await db.get_setting("partner_offers") or "Офферы появятся позже"
        await bot.send_message(
            chat_id,
            f"💼 <b>ШАГ 2/5 · ОФФЕРЫ СЕТИ</b>\n{ONB_SEP}\n{offers}",
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=_onb_kb("✅ Ознакомился", "3"),
        )
    elif step == 3:
        await bot.send_message(
            chat_id,
            f"💳 <b>ШАГ 3/5 · КОШЕЛЕК</b>\n{ONB_SEP}\n"
            f"Отправьте адрес своего кошелька USDT BEP-20 в формате:\n"
            f"<code>0x........................</code>\n\n"
            f"Если кошелька пока нет - инструкции есть в разделе «💳 Кошелек», "
            f"этот шаг можно пропустить и указать адрес позже",
            parse_mode="HTML",
            reply_markup=_onb_kb("⏭ Пропустить", "4"),
        )
    elif step == 4:
        link = await db.get_setting("agents_chat_link")
        text = (f"🟢 <b>ШАГ 4/5 · ЧАТ АГЕНТОВ</b>\n{ONB_SEP}\n" +
                (f"Вступите в наш чат агентов: {link}" if link
                 else "Ссылка на чат появится позже - её выдаст наставник"))
        await bot.send_message(
            chat_id, text, parse_mode="HTML",
            reply_markup=_onb_kb("✅ Далее", "5"),
        )
    elif step == 5:
        materials = await db.get_materials()
        await bot.send_message(
            chat_id,
            f"📗 <b>ШАГ 5/5 · ОБУЧЕНИЕ</b>\n{ONB_SEP}\n"
            f"Изучите уроки - это база для старта работы",
            parse_mode="HTML",
            reply_markup=kb.materials_kb(materials),
        )
        await bot.send_message(
            chat_id, "Когда изучите материалы - завершите вход:",
            reply_markup=_onb_kb("🏁 Завершить", "done"),
        )


@router.callback_query(F.data.startswith("onb:"))
async def onb_next(call: CallbackQuery, state: FSMContext, bot: Bot):
    step = call.data.split(":")[1]
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if step == "done":
        await state.clear()
        user = await db.get_user(call.from_user.id)
        await call.message.answer(
            f"🎉 <b>Готово! Вы прошли все шаги</b>\n{ONB_SEP}\n"
            f"Добро пожаловать в команду - главное меню внизу 👇",
            parse_mode="HTML",
            reply_markup=menu_for(user["role"] if user else "agent"),
        )
        return await call.answer("Онбординг завершён 🏁")
    step = int(step)
    if step == 3:
        await state.set_state(OnbForms.wallet)
    else:
        await state.clear()
    await send_onb_step(bot, call.from_user.id, step)
    await call.answer()


@router.message(OnbForms.wallet)
async def onb_wallet_save(message: Message, state: FSMContext, bot: Bot):
    wallet = (message.text or "").strip()
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", wallet):
        return await message.answer(
            "❌ Неверный формат адреса. Нужен адрес USDT BEP-20 вида:\n"
            "<code>0x</code> + 40 символов (буквы a-f и цифры)\n\n"
            "Проверьте и отправьте ещё раз, либо нажмите «⏭ Пропустить» выше",
            parse_mode="HTML",
        )
    await state.clear()
    await db.set_wallet(message.from_user.id, wallet)
    await message.answer("✅ Кошелек сохранен")
    await send_onb_step(bot, message.from_user.id, 4)
