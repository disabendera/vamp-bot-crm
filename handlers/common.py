from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import db
import keyboards as kb

router = Router()

# Кнопки главного меню - при нажатии сбрасывают любой незавершённый ввод
MENU_BUTTONS = {
    "👤 Профиль",
    "🟢 Чат агентов",
    "✅ Запись на собеседование",
    "📗 Обучение",
    "💳 Кошелек",
    "✳️ Условия работы",
    "💵 Мои модели",
    "❇️ Настройка",
    "⚙️ Управление",
    "🔍 Поиск агента",
    "📊 Аналитика",
    "💵 Модели",
    "💼 Офферы партнёрки",
    "✳️ Условия сети",
}


def menu_for(role: str):
    if role == "admin":
        return kb.admin_menu
    if role == "mentor":
        return kb.mentor_menu
    if role == "leader":
        return kb.leader_menu
    return kb.main_menu


# «🔴 Отмена» работает в любом состоянии: сброс ввода и возврат в главное меню
@router.message(F.text == "🔴 Отмена")
async def cancel_button(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    role = user["role"] if user else "agent"
    await message.answer("Главное меню:", reply_markup=menu_for(role))


@router.message(~StateFilter(None), F.text.in_(MENU_BUTTONS))
async def cancel_state_on_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Предыдущее действие отменено. Нажмите кнопку ещё раз 👇"
    )
