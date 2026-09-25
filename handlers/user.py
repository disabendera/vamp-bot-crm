import asyncio
import calendar as _calendar
import logging
from html import escape
import re
from datetime import date

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
                           InlineKeyboardButton, InputMediaPhoto,
                           ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove)

import db
import keyboards as kb
from services.banners import reply_banner
from services.posts import send_post, parse_post, is_empty
from config import ADMIN_ID
from services.google_sheets import send_interview_to_sheet
from handlers.common import menu_for

logger = logging.getLogger(__name__)

router = Router()

STATUS_TITLES = {"active": "Активные ✅", "dropped": "Слив 🚫"}


class Forms(StatesGroup):
    wallet = State()
    interview_position = State()
    interview_partner = State()
    interview_form = State()
    interview_date = State()
    interview_time = State()
    interview_model_comment = State()
    interview_comment = State()
    interview_photos = State()
    interview_confirm = State()
    model_name = State()
    model_phone = State()
    model_username = State()


async def require_callback_access(call: CallbackQuery) -> bool:
    user = await db.get_user(call.from_user.id)
    if not user or user["role"] not in ("agent", "leader", "mentor", "admin"):
        await call.answer("Нет доступа", show_alert=True)
        return False
    return True


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
    if "onboarded" in user.keys() and not user["onboarded"]:
        await message.answer(
            "📋 Сначала пройдите вводные шаги выше 👆 - меню откроется после завершения",
            reply_markup=ReplyKeyboardRemove(),
        )
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
        u_obj = await db.get_user(tg_id)
        u_dict_obj = dict(u_obj) if u_obj else {}
        agent_code = u_dict_obj.get("agent_code") if u_dict_obj.get("agent_code") else (1000 + u_dict_obj.get("id", tg_id))
        text = (f"🟢 <b>НОВАЯ ЗАЯВКА НА ВСТУПЛЕНИЕ</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"👤 Имя: <b>{escape(message.from_user.full_name)}</b>\n"
                f"✅ Агент ID: <code>{agent_code}</code>\n"
                f"🟢 Юзернейм: @{message.from_user.username or '-'}\n"
                f"🟩 Telegram ID: <code>{tg_id}</code>")
        for sid in staff:
            try:
                await bot.send_message(sid, text, parse_mode="HTML", reply_markup=kb.approve_kb(tg_id))
            except Exception:
                logger.warning("Не удалось отправить/обработать уведомление", exc_info=True)
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
        if "onboarded" in user.keys() and not user["onboarded"]:
            await message.answer("📋 Продолжим вводные шаги:", reply_markup=ReplyKeyboardRemove())
            await send_onb_step(bot, message.from_user.id, 1)
            return
        await message.answer("Главное меню:", reply_markup=menu_for(user["role"]))


# ---------- Профиль ----------

@router.message(F.text == "👤 Профиль")
async def profile(message: Message):
    user = await require_access(message)
    if not user:
        return
    u_dict = dict(user)
    team = await db.get_team(u_dict["team_id"]) if u_dict.get("team_id") else None
    agent_name = f"Команда {team['name']}" if team else (u_dict.get("full_name") or "")
    wallet = u_dict.get("wallet") or "не указаны - кнопка «💳 Кошелек»"
    pending = await db.earned_last_days(message.from_user.id, 14)
    total_earned = u_dict.get("total_earned", 0.0)
    agent_code = u_dict.get("agent_code") if u_dict.get("agent_code") else (1000 + u_dict["id"])
    await reply_banner(
        message, "profile",
        f"👤 <b>Агент:</b> {escape(agent_name)}\n\n"
        f"✅ <b>Агентский айди:</b> <code>{agent_code}</code>\n\n"
        f"💳 <b>Кошелек:</b> <code>{escape(wallet)}</code>\n\n"
        f"💵 <b>Текущий баланс:</b> ${u_dict.get('balance', 0):.2f} (${pending:.2f} за 2 недели)\n"
        f"🏆 <b>Заработано за всё время:</b> ${total_earned:.2f}\n\n"
        f"📗 Выплаты средств теперь доступны от суммы 50$, "
        f"всё, что меньше, остаётся в накоплениях\n\n"
        f"👉 <a href=\"https://www.bestchange.com/\">КАК ПОМЕНЯТЬ КРИПТУ НА ВАЛЮТУ?</a>\n\n"
        f"🟢 Выплаты производятся в долларах USDT BEP-20 "
        f"<a href=\"https://www.binance.com/ru/square/post/950859\">(как можно получить кошелек?)</a>",
    )


# ---------- Чат агентов ----------

@router.message(F.text == "🟢 Чат агентов")
async def agents_chat(message: Message):
    user = await require_access(message)
    if not user:
        return
    link = await db.get_setting("agents_chat_link")
    if link:
        text = f"Вступайте в чат агентов - общение, вопросы, поддержка команды\n\n👉 {link}"
    else:
        text = "Ссылка на чат агентов ещё не настроена. Обратитесь к наставнику"
    await reply_banner(message, "chat", text)


# ---------- Магазин ----------

@router.message(F.text == "🛒 Магазин")
async def shop(message: Message):
    user = await require_access(message)
    if not user:
        return
    stored = await db.get_setting("shop_post")
    post = parse_post(stored)
    if post["type"] in ("text", "empty"):
        await reply_banner(message, "shop", post.get("text") or "Магазин скоро откроется")
    else:
        await reply_banner(message, "shop", "🛒 Магазин")
        await send_post(message.bot, message.chat.id, stored)


# ---------- Условия работы ----------

@router.message(F.text == "🤝 Партнерская сеть")
async def partner_network(message: Message):
    user = await require_access(message)
    if not user:
        return
    await message.answer("🤝 Партнерская сеть - выберите раздел:", reply_markup=kb.network_menu)


@router.message(F.text == "💼 Офферы партнёрки")
async def show_offers(message: Message):
    user = await require_access(message)
    if not user:
        return
    await send_post(message.bot, message.chat.id, await db.get_setting("partner_offers"),
                    header="💼 <b>ОФФЕРЫ ПАРТНЁРКИ</b>\n━━━━━━━━━━━━━━━\n",
                    empty_text="Офферы партнёрки скоро появятся здесь")


@router.message(F.text == "✳️ Условия сети")
async def show_network_terms(message: Message):
    user = await require_access(message)
    if not user:
        return
    await send_post(message.bot, message.chat.id, await db.get_setting("network_terms"),
                    header="✳️ <b>УСЛОВИЯ СЕТИ</b>\n━━━━━━━━━━━━━━━\n",
                    empty_text="Условия сети скоро появятся здесь")


# ---------- Запись на собеседование ----------

MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

FORM_PROMPT = (
    "🟢 Отправьте пошагово или одним сообщением (12 строк):\n"
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

FORM_QUESTIONS = [
    ("1️⃣ Имя", "1️⃣ Введите <b>Имя</b>:"),
    ("2️⃣ Возраст", "2️⃣ Укажите <b>Возраст</b> (числом):"),
    ("3️⃣ Номер", "3️⃣ Введите <b>Номер телефона</b> (номер/WhatsApp):"),
    ("4️⃣ Телеграм", "4️⃣ Введите <b>Телеграм</b> (например: @username):"),
    ("5️⃣ Гражданство", "5️⃣ Укажите <b>Гражданство</b>:"),
    ("6️⃣ Проживание", "6️⃣ Укажите <b>Страну и город проживания</b>:"),
    ("7️⃣ Проживает с кем", "7️⃣ Укажите, <b>с кем проживает</b>:"),
    ("8️⃣ Модель телефона", "8️⃣ Укажите <b>Модель телефона</b>:"),
    ("9️⃣ Второе устройство", "9️⃣ Укажите <b>Второе устройство</b> (ноутбук/ПК/планшет/нет):"),
    ("🔟 Готова приступить", "🔟 Укажите, <b>когда готова приступить к работе</b>:"),
    ("1️⃣1️⃣ Время на работу", "1️⃣1️⃣ Укажите, <b>сколько готова уделять времени</b>:"),
    ("1️⃣2️⃣ Понимание сферы", "1️⃣2️⃣ Оцените <b>понимание сферы</b> (от 1 до 10):"),
]

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


def build_time_kb(chosen_date: str = "", partner: str = "", occupied: set = None) -> InlineKeyboardMarkup:
    if occupied is None:
        occupied = set()
    
    from datetime import datetime, timezone, timedelta
    msk_now = datetime.now(timezone.utc) + timedelta(hours=3)
    today_str = msk_now.strftime("%d.%m.%Y")
    current_hour = msk_now.hour
    
    is_today = (chosen_date == today_str)
    
    rows, row = [], []
    for hour in range(8, 24):
        time_str = f"{hour:02d}:00"
        if is_today and hour <= current_hour:
            btn_text = f"✖️ {time_str}"
            cb_data = "itime:past"
        elif time_str in occupied:
            btn_text = f"❌ {time_str}"
            cb_data = "itime:busy"
        else:
            btn_text = time_str
            cb_data = f"itime:{hour:02d}:00"
            
        row.append(InlineKeyboardButton(text=btn_text, callback_data=cb_data))
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


async def start_form_filling(message: Message, state: FSMContext, header_prefix: str = ""):
    await state.update_data(form_answers=[], form_step=0)
    await state.set_state(Forms.interview_form)
    _, prompt_text = FORM_QUESTIONS[0]
    msg = f"{header_prefix}\n\n📝 <b>Заполнение анкеты (1/12)</b>\n\n{prompt_text}" if header_prefix else f"📝 <b>Заполнение анкеты (1/12)</b>\n\n{prompt_text}"
    await message.answer(
        msg,
        parse_mode="HTML",
        reply_markup=cancel_menu,
    )


@router.message(Forms.interview_position, F.text.in_({"💃 Модель", "👨 Оператор"}))
async def interview_position(message: Message, state: FSMContext):
    position = "Модель" if "Модель" in message.text else "Оператор"
    await state.update_data(position=position)
    if position == "Модель":
        partner = await db.reserve_next_agent_partner(message.from_user.id, advance=False)
        if not partner:
            await state.clear()
            user = await db.get_user(message.from_user.id)
            return await message.answer(
                "🔒 <b>Доступ к записи ещё не открыт</b>\n"
                "━━━━━━━━━━━━━━━\n"
                "Запросите доступ у наставника - после этого сможете записывать моделей на собеседование",
                parse_mode="HTML",
                reply_markup=menu_for(user["role"]),
            )
        await state.update_data(partner=partner["name"])
        await start_form_filling(message, state)
    else:
        await state.update_data(partner="-")
        await start_form_filling(message, state, "👨 Позиция: <b>Оператор</b>")


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
    await start_form_filling(message, state, f"🤝 Партнёр: <b>{name}</b>")


MODEL_COMMENT_QUESTIONS = [
    ("1️⃣ Какие вопросы задавала", "1️⃣ <b>Какие вопросы задавала:</b>"),
    ("2️⃣ Возражения или переживания", "2️⃣ <b>Были ли возражения или переживания</b> (какие возражения/переживания и как обработали/успокоили):"),
    ("3️⃣ Предлагали ли похожую работу", "3️⃣ <b>Предлагали ли похожую работу:</b>"),
    ("4️⃣ Описание девочки", "4️⃣ <b>Небольшое описание девочки</b> (как шла на контакт, что важно для нее в работе и т.д.):"),
]


async def start_comments_step(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("position") == "Модель":
        await state.update_data(model_comment_answers=[], model_comment_step=0)
        await state.set_state(Forms.interview_model_comment)
        _, prompt_text = MODEL_COMMENT_QUESTIONS[0]
        await message.answer(
            f"💬 <b>Комментарий по модели (1/4)</b>\n\n{prompt_text}",
            parse_mode="HTML",
            reply_markup=cancel_menu,
        )
    else:
        await state.set_state(Forms.interview_comment)
        await message.answer(
            "✍️ Добавьте краткое описание от себя одним сообщением (необязательно):",
            reply_markup=comment_menu,
        )


@router.message(Forms.interview_form)
async def interview_form(message: Message, state: FSMContext):
    raw_text = (message.text or "").strip()
    data = await state.get_data()
    answers = data.get("form_answers", [])
    step = data.get("form_step", 0)

    # Проверка возраста на 2-м шаге (индекс 1)
    if step == 1:
        age_match = re.search(r"\d+", raw_text)
        if not age_match:
            return await message.answer(
                "❌ Укажите возраст числом (например: 21):",
                reply_markup=cancel_menu,
            )
        age_val = int(age_match.group())
        if age_val < 18:
            return await message.answer(
                "🔞 Работаем только с совершеннолетними (18+). Пожалуйста, укажите корректный возраст (18+):",
                reply_markup=cancel_menu,
            )
        raw_text = str(age_val)

    answers.append(raw_text)
    next_step = step + 1

    if next_step < 12:
        await state.update_data(form_answers=answers, form_step=next_step)
        _, prompt_text = FORM_QUESTIONS[next_step]
        await message.answer(
            f"📝 <b>Заполнение анкеты ({next_step + 1}/12)</b>\n\n{prompt_text}",
            parse_mode="HTML",
            reply_markup=cancel_menu,
        )
    else:
        form_text = (
            f"1) Имя: {answers[0]}\n"
            f"2) Возраст: {answers[1]}\n"
            f"3) Номер: {answers[2]}\n"
            f"4) Телеграм: {answers[3]}\n"
            f"5) Гражданство: {answers[4]}\n"
            f"6) Проживание (страна, город): {answers[5]}\n"
            f"7) Проживает с кем: {answers[6]}\n"
            f"8) Модель телефона: {answers[7]}\n"
            f"9) Второе устройство: {answers[8]}\n"
            f"10) Готова приступить (когда): {answers[9]}\n"
            f"11) Сколько готова уделять времени: {answers[10]}\n"
            f"12) Понимание сферы (оценка из 10): {answers[11]}"
        )
        await state.update_data(form=form_text)
        await start_comments_step(message, state)


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
    
    data = await state.get_data()
    partner_name = data.get("partner", "")
    occupied = await db.get_occupied_times(chosen, partner_name)
    
    await call.message.answer("🕐 Выберите время (МСК):", reply_markup=build_time_kb(chosen, partner_name, occupied))
    await call.answer()


@router.callback_query(F.data == "itime:busy")
async def interview_time_busy(call: CallbackQuery):
    await call.answer("⚠️ Этот слот времени уже занят! Выберите другое свободное время", show_alert=True)


@router.callback_query(F.data == "itime:past")
async def interview_time_past(call: CallbackQuery):
    await call.answer("❌ Это время уже прошло сегодня. Выберите актуальное время", show_alert=True)


@router.message(Forms.interview_time)
async def interview_time_text(message: Message):
    await message.answer("Выберите время кнопкой выше 👆")


MODEL_COMMENT_TEMPLATE = (
    "1) Какие вопросы задавала:\n"
    "2) Были ли возражения или переживания:\n"
    "Возражение или переживание - \n"
    "Как обработал/как успокоил - \n"
    "3) Предлагали ли похожую работу:\n"
    "4) Небольшое описание девочки: как шла на контакт, что важно для нее в работе и тд:"
)


@router.callback_query(F.data.startswith("itime:"))
async def interview_time(call: CallbackQuery, state: FSMContext):
    _, hh, mm = call.data.split(":")
    await state.update_data(time=f"{hh}:{mm}")
    await call.message.edit_text(f"🕐 Время собеседования: <b>{hh}:{mm} МСК</b>", parse_mode="HTML")
    
    data = await state.get_data()
    if data.get("position") == "Модель":
        await state.update_data(photos=[])
        await state.set_state(Forms.interview_photos)
        await call.message.answer(
            "📸 Отправьте от 1 до 3 фото модели.\n\n"
            "❗️ Важно: фото должны быть <b>в полный рост</b>",
            parse_mode="HTML",
            reply_markup=photos_menu,
        )
    else:
        await show_preview(call.message, state)
    await call.answer()


@router.message(Forms.interview_model_comment)
async def interview_model_comment(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        return await message.answer(
            "❌ Пожалуйста, отправьте ответ сообщением",
            reply_markup=cancel_menu,
        )
    
    data = await state.get_data()
    answers = data.get("model_comment_answers", [])
    step = data.get("model_comment_step", 0)

    answers.append(text)
    next_step = step + 1

    if next_step < 4:
        await state.update_data(model_comment_answers=answers, model_comment_step=next_step)
        _, prompt_text = MODEL_COMMENT_QUESTIONS[next_step]
        await message.answer(
            f"💬 <b>Комментарий по модели ({next_step + 1}/4)</b>\n\n{prompt_text}",
            parse_mode="HTML",
            reply_markup=cancel_menu,
        )
    else:
        model_comment_text = (
            f"1) Какие вопросы задавала: {answers[0]}\n"
            f"2) Были ли возражения или переживания: {answers[1]}\n"
            f"3) Предлагали ли похожую работу: {answers[2]}\n"
            f"4) Небольшое описание девочки (как шла на контакт, что важно в работе): {answers[3]}"
        )
        await state.update_data(model_comment=model_comment_text)
        await state.set_state(Forms.interview_comment)
        await message.answer(
            "✍️ Добавьте краткое описание от себя одним сообщением (необязательно):",
            reply_markup=comment_menu,
        )


async def after_comment(message: Message, state: FSMContext):
    await state.set_state(Forms.interview_date)
    today = date.today()
    await message.answer(
        "📅 <b>Выберите дату собеседования:</b>",
        parse_mode="HTML",
        reply_markup=build_calendar(today.year, today.month)
    )


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
    
    comment_parts = []
    if data.get("model_comment"):
        comment_parts.append(f"💬 <b>Комментарий по модели:</b>\n{escape(data['model_comment'])}")
    if data.get("comment"):
        comment_parts.append(f"✍️ <b>От агента:</b>\n{escape(data['comment'])}")
        
    comment_block = ("\n\n" + "\n\n".join(comment_parts)) if comment_parts else ""
    photos_line = f"\n📸 Фото: {len(data['photos'])} шт" if data.get("photos") else ""
    await message.answer(
        f"📋 <b>Проверьте заявку</b>\n\n"
        f"Позиция: <b>{data.get('position', '-')}</b>\n"
        f"📅 Собес: <b>{data.get('date', '-')} в {data.get('time', '-')} МСК</b>"
        f"{photos_line}\n\n"
        f"{escape(data['form'])}"
        f"{comment_block}",
        parse_mode="HTML",
        reply_markup=confirm_menu,
    )


@router.message(Forms.interview_confirm, F.text == "📤 Отправить")
async def interview_send(message: Message, state: FSMContext, bot: Bot):
    user = await require_access(message)
    if not user:
        await state.clear()
        return
    data = await state.get_data()
    if not data.get("form"):
        await state.clear()
        return await message.answer("Заявка устарела, начните заново")
    await state.clear()

    # Очередь меняется только при отправке, отменённые анкеты её не сдвигают.
    if data.get("position") == "Модель":
        partner = await db.reserve_next_agent_partner(message.from_user.id)
        if not partner:
            return await message.answer(
                "🔒 Доступ к записи ещё не открыт. Запросите доступ у наставника",
                reply_markup=menu_for(user["role"]),
            )
        data["partner"] = partner["name"]
    
    comment_parts = []
    if data.get("model_comment"):
        comment_parts.append(f"💬 Комментарий по модели:\n{data['model_comment']}")
    if data.get("comment"):
        comment_parts.append(f"✍️ От агента:\n{data['comment']}")
        
    comment_block = ("\n\n" + "\n\n".join(comment_parts)) if comment_parts else ""
    header = (f"Позиция: {data.get('position', '-')}\n"
              f"Собес: {data.get('date', '-')} в {data.get('time', '-')} МСК")
    full_text = f"{header}\n\n{data['form']}{comment_block}"
    answers = data.get("form_answers", [])
    m_name = answers[0] if len(answers) > 0 else "Модель"
    m_phone = answers[2] if len(answers) > 2 else ""
    m_tg = answers[3] if len(answers) > 3 else ""

    model_code = await db.add_model_application(
        owner_tg_id=message.from_user.id,
        name=m_name,
        phone=m_phone,
        username=m_tg,
        text=full_text,
        partner=data.get("partner", ""),
        position=data.get("position", ""),
        sobes_date=data.get("date", ""),
        sobes_time=data.get("time", ""),
    )
    photos = data.get("photos", [])
    u_obj = await db.get_user(message.from_user.id)
    agent_code = db.get_agent_code(u_obj) or (1000 + message.from_user.id)
    staff_msg = (f"✅ Заявка на собеседование · ID модели {model_code} → {data.get('partner', '-')}\n"
                 f"От агента ID: {agent_code}\n\n{full_text}")
    
    for sid in await db.get_mentor_ids():
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
            logger.warning("Не удалось отправить/обработать уведомление", exc_info=True)

    # Интеграция с чатами и Google Таблицей (ТОЛЬКО для позиций "Модель")
    if data.get("position") == "Модель" and data.get("partner"):
        partner_obj = await db.get_partner_by_name(data.get("partner"))
        if partner_obj:
            p_dict = dict(partner_obj)
            # 1. Отправка в чат партнёра (если указан chat_id и топик)
            if p_dict.get("chat_id"):
                partner_chat_id = str(p_dict["chat_id"]).strip()
                topic_app = p_dict.get("topic_applications")
                partner_msg = (
                    f"📋 <b>Новая заявка на собеседование · ID модели {model_code}</b>\n"
                    f"👤 <b>Агент ID:</b> <code>{agent_code}</code>\n"
                    f"📅 <b>Дата и время:</b> {data.get('date', '-')} в {data.get('time', '-')} МСК\n\n"
                    f"<b>Анкета:</b>\n{escape(data['form'])}"
                    f"{escape(comment_block)}"
                )
                try:
                    kwargs = {}
                    if topic_app:
                        kwargs["message_thread_id"] = topic_app

                    if photos and len(partner_msg) <= 1024:
                        media = [InputMediaPhoto(media=photos[0], caption=partner_msg, parse_mode="HTML")]
                        media += [InputMediaPhoto(media=fid) for fid in photos[1:]]
                        await bot.send_media_group(partner_chat_id, media, **kwargs)
                    elif photos:
                        await bot.send_media_group(partner_chat_id, [InputMediaPhoto(media=fid) for fid in photos], **kwargs)
                        await bot.send_message(partner_chat_id, partner_msg, parse_mode="HTML", **kwargs)
                    else:
                        await bot.send_message(partner_chat_id, partner_msg, parse_mode="HTML", **kwargs)
                except Exception as err:
                    print(f"Ошибка при отправке в чат партнёра {partner_chat_id}: {err}")

            # 2. Отправка в Google Таблицу партнёра (если указан sheet_url)
            if partner_obj["sheet_url"]:
                sheet_payload = {
                    "model_code": model_code,
                    "created_at": str(date.today()),
                    "agent_username": f"Агент ID: {agent_code}",
                    "agent_id": agent_code,
                    "agent_code": agent_code,
                    "model_name": m_name,
                    "model_phone": m_phone,
                    "model_tg": m_tg,
                    "partner": partner_obj["name"],
                    "position": "Модель",
                    "date": data.get("date", ""),
                    "time": data.get("time", ""),
                    "form": data.get("form", ""),
                    "comment": data.get("comment", ""),
                    "status": "Принято",
                }
                asyncio.create_task(send_interview_to_sheet(partner_obj["sheet_url"], sheet_payload))

    user = await db.get_user(message.from_user.id)
    await message.answer(f"✅ Запись выполнена (ID модели: {model_code})", reply_markup=menu_for(user["role"]))



@router.message(Forms.interview_confirm)
async def interview_confirm_text(message: Message):
    await message.answer("Нажмите «📤 Отправить» или «🔴 Отмена» 👇", reply_markup=confirm_menu)


@router.callback_query(F.data == "icancel")
async def interview_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.warning("Не удалось отправить/обработать уведомление", exc_info=True)
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
    await reply_banner(message, "training", "Выберите урок 👇",
                       reply_markup=kb.materials_kb(materials))


@router.callback_query(F.data.startswith("mat:"))
async def show_material(call: CallbackQuery):
    material = await db.get_material(int(call.data.split(":")[1]))
    if material:
        await call.message.answer(f"📗 <b>{escape(material['title'])}</b>\n\n{material['content']}", parse_mode="HTML")
    await call.answer()


# ---------- Реквизиты ----------

WALLET_INFO = (
    "<b>Кошелек для выплат</b>\n\n"
    "Текущий кошелек: <code>{current}</code>\n\n"
    "Нет кошелька? Инструкции:\n\n"
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
    await reply_banner(
        message, "wallet",
        WALLET_INFO.format(current=current),
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
    head = (
        "✅ <b>Активные модели</b>\n"
        "Прошли регистрацию и готовы к работе"
        if status == "active" else
        "🚫 <b>Модели в сливе</b>\n"
        "Отработали минимум одну смену"
    )
    start = page * PAGE_SIZE
    chunk = models[start:start + PAGE_SIZE]
    lines = []
    for i, m in enumerate(chunk, start=start + 1):
        phone = m["phone"] or "-"
        username = f"@{m['username'].lstrip('@')}" if m["username"] else "-"
        lines.append(
            f"<b>{i}. {escape(m['name'])}</b>\n"
            f"🆔 ID модели: <code>{m['model_code']}</code>\n"
            f"📞 {escape(phone)}   🟢 {escape(username)}\n"
            f"⏱ Смен: <b>{m['shifts']}</b>"
        )
    total_pages = (len(models) - 1) // PAGE_SIZE + 1
    return (
        f"{head}\n\n" + "\n\n━━━━━━━━━━━━━━━\n\n".join(lines) +
        f"\n\n📄 Страница: {page + 1}/{total_pages}\n"
        f"Открыть карточку: /model_НОМЕР"
    )


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
    if not await require_callback_access(call):
        return
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
    if not await require_callback_access(call):
        return
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
                         reply_markup=kb.model_card_kb(m["model_code"], m["status"]))


def render_model_card(m) -> str:
    phone = m["phone"] or "-"
    uname = f"@{m['username']}" if m["username"] else "-"
    return (f"{STATUS_TITLES[m['status']]}\n\n"
            f"💵 <b>{escape(m['name'])}</b>\n"
            f"📞 Телефон: {escape(phone)}\n"
            f"🟢 Telegram: {escape(uname)}\n"
            f"🟩 Смен: {m['shifts']}")


@router.callback_query(F.data.startswith("shift:"))
async def model_shift(call: CallbackQuery):
    # ручная правка смен - только наставник/админ (старые кнопки у агентов не работают)
    viewer = await db.get_user(call.from_user.id)
    if not viewer or viewer["role"] not in ("mentor", "admin"):
        return await call.answer("Смены начисляются автоматически из отчётника", show_alert=True)
    _, model_id, delta = call.data.split(":")
    await db.inc_shift(int(model_id), call.from_user.id, int(delta))
    m = await db.get_model_by_id(int(model_id), call.from_user.id)
    if m:
        await call.message.edit_text(render_model_card(m), parse_mode="HTML",
                                     reply_markup=kb.model_card_kb(m["model_code"], m["status"]))
    await call.answer("Смены обновлены ✅")


@router.callback_query(F.data.startswith("mstat:"))
async def model_status(call: CallbackQuery):
    await call.answer("Статус модели ставит партнёр", show_alert=True)


@router.callback_query(F.data.startswith("agent_confirm:"))
async def agent_confirm_interview(call: CallbackQuery, bot: Bot):
    model_code = call.data.split(":")[1]
    model, changed = await db.respond_to_model_application(model_code, call.from_user.id, confirm=True)
    if not model:
        return await call.answer("Модель не найдена среди ваших или доступ закрыт", show_alert=True)
    if not changed:
        return await call.answer("Ответ уже получен или статус заявки изменился", show_alert=True)
    
    name = model["name"] or "Модель"
    
    partner_name = model["partner"]
    if partner_name and partner_name != "-":
        partner_obj = await db.get_partner_by_name(partner_name)
        if partner_obj:
            p_dict = dict(partner_obj)
            if p_dict.get("sheet_url"):
                update_payload = {
                    "action": "update_agent_confirmation",
                    "model_code": model_code,
                    "model_name": name,
                    "status": "✅ Подтверждено",
                }
                asyncio.create_task(send_interview_to_sheet(p_dict["sheet_url"], update_payload))
            
            # Отправка анкеты в Топик 2 (Подтверждения)
            if p_dict.get("chat_id"):
                topic_conf = p_dict.get("topic_confirmations")
                conf_header = f"✅ <b>ID МОДЕЛИ {model_code} · ПОДТВЕРЖДЕНА АГЕНТОМ</b>"
                conf_msg = await db.format_anketa_topic_message(model, conf_header)
                try:
                    kwargs = {"message_thread_id": topic_conf} if topic_conf else {}
                    await bot.send_message(p_dict["chat_id"], conf_msg, parse_mode="HTML", **kwargs)
                except Exception as e:
                    print(f"Ошибка отправки подтверждения в топик: {e}")
            
    await call.message.edit_text(
        f"✅ <b>МОДЕЛЬ {model_code} ПОДТВЕРЖДЕНА</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💚 <b>{escape(name)}</b> отмечена как подтверждённая агентом",
        parse_mode="HTML"
    )
    await call.answer("✅ Подтверждено!")


@router.callback_query(F.data.startswith("agent_reject:"))
async def agent_reject_interview(call: CallbackQuery, bot: Bot):
    model_code = call.data.split(":")[1]
    model, changed = await db.respond_to_model_application(model_code, call.from_user.id, confirm=False)
    if not model:
        return await call.answer("Модель не найдена среди ваших или доступ закрыт", show_alert=True)
    if not changed:
        return await call.answer("Ответ уже получен или статус заявки изменился", show_alert=True)
    
    partner_name = model["partner"]
    if partner_name and partner_name != "-":
        partner_obj = await db.get_partner_by_name(partner_name)
        if partner_obj:
            p_dict = dict(partner_obj)
            if p_dict.get("chat_id"):
                topic_canc = p_dict.get("topic_cancelled")
                canc_header = f"❌ <b>ID МОДЕЛИ {model_code} · Слив / Отклонена агентом</b>"
                canc_msg = await db.format_anketa_topic_message(model, canc_header)
                try:
                    kwargs = {"message_thread_id": topic_canc} if topic_canc else {}
                    await bot.send_message(p_dict["chat_id"], canc_msg, parse_mode="HTML", **kwargs)
                except Exception as e:
                    print(f"Ошибка отправки слива в топик: {e}")

    await call.message.edit_text(
        f"🔴 <b>ID МОДЕЛИ {model_code} · МОДЕЛЬ НЕ ПРИДЁТ</b>",
        parse_mode="HTML"
    )
    await call.answer("Отклонено")


# ---------- Ловушка для нераспознанных сообщений (ВСЕГДА последним) ----------

@router.message(F.chat.type == "private", StateFilter(None), F.text)
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
        await send_post(
            bot, chat_id, await db.get_setting("network_terms"),
            header=f"📋 <b>ШАГ 1/5 · УСЛОВИЯ РАБОТЫ СЕТИ</b>\n{ONB_SEP}\n",
            empty_text="Текст условий появится позже",
            reply_markup=_onb_kb("✅ Ознакомился", "2"),
        )
    elif step == 2:
        await send_post(
            bot, chat_id, await db.get_setting("partner_offers"),
            header=f"💼 <b>ШАГ 2/5 · ОФФЕРЫ СЕТИ</b>\n{ONB_SEP}\n",
            empty_text="Офферы появятся позже",
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
        logger.warning("Не удалось отправить/обработать уведомление", exc_info=True)
    if step == "done":
        await state.clear()
        await db.set_onboarded(call.from_user.id, 1)
        user = await db.get_user(call.from_user.id)
        await call.message.answer(
            f"🎉 <b>Готово! Вы прошли все шаги</b>\n{ONB_SEP}\n"
            f"Добро пожаловать в PRIME PARTNERS - главное меню внизу 👇",
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
