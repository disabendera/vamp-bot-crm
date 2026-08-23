from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# --- Главное меню (зелёная тема) ---
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🟢 Чат агентов")],
        [KeyboardButton(text="✅ Запись на собеседование"), KeyboardButton(text="📗 Обучение")],
        [KeyboardButton(text="💳 Кошелек"), KeyboardButton(text="✳️ Условия работы")],
        [KeyboardButton(text="💵 Мои модели"), KeyboardButton(text="📊 Аналитика")],
    ],
    resize_keyboard=True,
)

search_row = [KeyboardButton(text="🔍 Поиск агента")]

# --- Меню лидера: + поиск агентов ---
leader_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="✅ Запись на собеседование")],
        [KeyboardButton(text="💳 Кошелек")],
        [KeyboardButton(text="💵 Модели")],
        [KeyboardButton(text="🔍 Поиск агента")],
    ],
    resize_keyboard=True,
)

mentor_menu = ReplyKeyboardMarkup(
    keyboard=main_menu.keyboard + [
        [KeyboardButton(text="❇️ Настройка")],
    ],
    resize_keyboard=True,
)

# --- Меню админа: поиск + панель наставника + управление ---
admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📊 Аналитика")],
        [KeyboardButton(text="🔍 Поиск агента")],
        [KeyboardButton(text="❇️ Настройка"), KeyboardButton(text="⚙️ Управление")],
    ],
    resize_keyboard=True,
)

# --- Раздел «Управление» (только админ) ---
admin_panel_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🎭 Роли (выдать/забрать)", callback_data="adm:roles")],
    [InlineKeyboardButton(text="🚷 Блокировка агента", callback_data="adm:block")],
    [InlineKeyboardButton(text="👑 Назначить лидера (создать команду)", callback_data="adm:add_leader")],
    [InlineKeyboardButton(text="🧭 Путь агента (соло/команда)", callback_data="adm:agent_path")],
    [InlineKeyboardButton(text="👥 Команды", callback_data="adm:teams")],
    [InlineKeyboardButton(text="🏆 Топы (воронка)", callback_data="adm:tops")],
    [InlineKeyboardButton(text="➕ Добавить партнёра", callback_data="adm:add_partner")],
    [InlineKeyboardButton(text="🤝 Партнёры (удаление)", callback_data="adm:partners")],
    [InlineKeyboardButton(text="🎓 Назначить наставника", callback_data="adm:add_mentor")],
    [InlineKeyboardButton(text="⬇️ Снять наставника", callback_data="adm:demote")],
])


def approve_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"approve:{tg_id}"),
        InlineKeyboardButton(text="🚫 Отклонить", callback_data=f"reject:{tg_id}"),
    ]])


models_status_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Активные ✅", callback_data="models:active")],
    [InlineKeyboardButton(text="Слив 🚫", callback_data="models:dropped")],
])


def models_page_kb(status: str, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"mpage:{status}:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"mpage:{status}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(text="◀️ К статусам", callback_data="models:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_card_kb(model_id: int, status: str) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton(text="Слив 🚫", callback_data=f"mstat:{model_id}:dropped")
        if status == "active"
        else InlineKeyboardButton(text="Активна ✅", callback_data=f"mstat:{model_id}:active")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Смена", callback_data=f"shift:{model_id}:1"),
         InlineKeyboardButton(text="➖ Смена", callback_data=f"shift:{model_id}:-1")],
        [toggle],
    ])


def materials_kb(materials) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📗 {m['title']}", callback_data=f"mat:{m['id']}")]
        for m in materials
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows or [[
        InlineKeyboardButton(text="Пока пусто", callback_data="noop")
    ]])


mentor_panel_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🟢 Заявки на вступление", callback_data="panel:pending")],
    [InlineKeyboardButton(text="✅ Заявки на собеседование", callback_data="panel:interviews")],
    [InlineKeyboardButton(text="📗 Уроки", callback_data="panel:lessons")],
    [InlineKeyboardButton(text="✳️ Условия сети", callback_data="panel:terms_view")],
    [InlineKeyboardButton(text="💼 Офферы партнёрки", callback_data="panel:offers_view")],
    [InlineKeyboardButton(text="🟢 Чат агентов", callback_data="panel:chat_view")],
])


# --- Подменю «Условия работы» (заменяет главное меню) ---
work_terms_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💼 Офферы партнёрки")],
        [KeyboardButton(text="✳️ Условия сети")],
        [KeyboardButton(text="🔴 Отмена")],
    ],
    resize_keyboard=True,
)

# --- Универсальная инлайн-кнопка отмены для шагов ввода ---
cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel"),
]])
