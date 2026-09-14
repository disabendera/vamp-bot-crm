import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)

import db
import keyboards as kb
from services.posts import serialize_post, send_post

router = Router()


class MForms(StatesGroup):
    material_title = State()
    material_content = State()
    chat_link = State()
    balance = State()
    terms = State()
    offers = State()
    material_edit = State()


async def is_staff(tg_id: int) -> bool:
    user = await db.get_user(tg_id)
    return bool(user and user["role"] in ("mentor", "admin"))


# ---------- Двухэтапное подтверждение удаления ----------

def confirm_delete_kb(confirm_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=confirm_cb)],
        [InlineKeyboardButton(text="🔴 Отмена", callback_data="delabort")],
    ])


@router.callback_query(F.data == "delabort")
async def delete_abort(call: CallbackQuery):
    await call.message.edit_text("🔴 Удаление отменено")
    await call.answer()


# ---------- Одобрение / отклонение заявок ----------

@router.callback_query(F.data.startswith("approve:"))
async def approve(call: CallbackQuery, bot: Bot):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    tg_id = int(call.data.split(":")[1])
    await db.set_role(tg_id, "agent")
    await db.set_onboarded(tg_id, 0)
    await call.message.edit_text(call.message.html_text + "\n\n✅ Принят", parse_mode="HTML")
    try:
        from aiogram.types import ReplyKeyboardRemove
        await bot.send_message(
            tg_id,
            "🎉 Ваша заявка одобрена! Добро пожаловать\n\n"
            "Сейчас пройдём короткое знакомство - меню откроется после него",
            reply_markup=ReplyKeyboardRemove(),
        )
        from handlers.user import send_onb_step
        await send_onb_step(bot, tg_id, 1)
    except Exception:
        pass
    await call.answer("Принят ✅")


@router.callback_query(F.data.startswith("reject:"))
async def reject(call: CallbackQuery, bot: Bot):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    tg_id = int(call.data.split(":")[1])
    await db.set_role(tg_id, "banned")
    await call.message.edit_text(call.message.html_text + "\n\n🔴 Отклонён", parse_mode="HTML")
    try:
        await bot.send_message(tg_id, "К сожалению, ваша заявка отклонена")
    except Exception:
        pass
    await call.answer("Отклонён 🚫")


# ---------- Панель наставника ----------

@router.message(F.text == "❇️ Настройка")
async def panel(message: Message):
    if not await is_staff(message.from_user.id):
        return
    await message.answer("❇️ Настройка:", reply_markup=kb.mentor_panel_kb)


@router.callback_query(F.data == "panel:pending")
async def panel_pending(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    pending = await db.get_users_by_role("pending")
    if not pending:
        await call.message.answer("📥 Новых заявок нет")
    for u in pending:
        agent_code = db.get_agent_code(u)
        await call.message.answer(
            f"🟢 <b>ЗАЯВКА НА ВСТУПЛЕНИЕ</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 Имя: <b>{u['full_name'] or '-'}</b>\n"
            f"✅ Агент ID: <code>{agent_code}</code>\n"
            f"🟢 Юзернейм: @{u['username'] or '-'}\n"
            f"🟩 Telegram ID: <code>{u['tg_id']}</code>",
            parse_mode="HTML",
            reply_markup=kb.approve_kb(u["tg_id"]),
        )
    await call.answer()


@router.callback_query(F.data == "panel:interviews")
async def panel_interviews(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    rows = await db.get_new_interviews()
    if not rows:
        await call.message.answer("📝 Новых заявок на собеседование нет")
    for r in rows:
        app_status = r["app_status"] if "app_status" in r.keys() and r["app_status"] else "Не подтверждена"
        agent_code = db.get_agent_code(r) or r["id"]
        await call.message.answer(
            f"📝 #{r['id']} от Агента ID {agent_code}:\n\n{r['text']}\n\n"
            f"🎛 Статус: <b>{app_status}</b>\n"
            f"Закрыть заявку: /close_{r['id']}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔄 Изменить статус", callback_data=f"ist:{r['id']}")
            ]]),
        )
    await call.answer()


@router.callback_query(F.data.startswith("ist:"))
async def interview_status_menu(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    interview_id = int(call.data.split(":")[1])
    rows, row = [], []
    for i, st in enumerate(db.APP_STATUSES):
        row.append(InlineKeyboardButton(text=st, callback_data=f"sts:{interview_id}:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    await call.message.answer(
        f"🎛 Новый статус для заявки #{interview_id}:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sts:"))
async def interview_status_set(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    _, interview_id, idx = call.data.split(":")
    status = db.APP_STATUSES[int(idx)]
    await db.set_app_status(int(interview_id), status)
    await call.message.edit_text(f"✅ Заявке #{interview_id} установлен статус: <b>{status}</b>",
                                 parse_mode="HTML")
    await call.answer("Статус обновлён ✅")


@router.message(F.text.regexp(r"^/close_(\d+)$"))
async def close_interview(message: Message):
    if not await is_staff(message.from_user.id):
        return
    interview_id = int(message.text.split("_")[1])
    await db.close_interview(interview_id)
    await message.answer(f"✅ Заявка #{interview_id} закрыта")


# ---------- Уроки: список, просмотр, правка ----------

@router.callback_query(F.data == "panel:lessons")
async def panel_lessons(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    materials = await db.get_materials()
    rows = [
        [InlineKeyboardButton(text=f"📗 {mt['title']}", callback_data=f"lesson:{mt['id']}")]
        for mt in materials
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить урок", callback_data="panel:add_material")])
    await call.message.answer(
        "📗 Уроки - выберите для просмотра и правки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("lesson:"))
async def panel_lesson_view(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    material = await db.get_material(int(call.data.split(":")[1]))
    if not material:
        return await call.answer("Урок не найден", show_alert=True)
    await call.message.answer(
        f"📗 <b>{material['title']}</b>\n━━━━━━━━━━━━━━━\n{material['content']}",
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"lessonedit:{material['id']}"),
             InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delmat:{material['id']}")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("lessonedit:"))
async def panel_lesson_edit(call: CallbackQuery, state: FSMContext):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(MForms.material_edit)
    await state.update_data(edit_material_id=int(call.data.split(":")[1]))
    await call.message.answer("Отправьте новый текст урока одним сообщением:",
                              reply_markup=kb.cancel_kb)
    await call.answer()


@router.message(MForms.material_edit)
async def panel_lesson_edit_save(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await db.update_material(data["edit_material_id"], message.text)
    await message.answer("✅ Текст урока обновлён")


# ---------- Просмотр постов с кнопкой правки ----------

@router.callback_query(F.data == "panel:terms_view")
async def panel_terms_view(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await send_post(
        call.bot, call.message.chat.id, await db.get_setting("network_terms"),
        header="✳️ <b>УСЛОВИЯ СЕТИ · ТЕКУЩИЙ ПОСТ</b>\n━━━━━━━━━━━━━━━\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✏️ Изменить", callback_data="panel:set_terms")
        ]]),
    )
    await call.answer()


@router.callback_query(F.data == "panel:offers_view")
async def panel_offers_view(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await send_post(
        call.bot, call.message.chat.id, await db.get_setting("partner_offers"),
        header="💼 <b>ОФФЕРЫ ПАРТНЁРКИ · ТЕКУЩИЙ ПОСТ</b>\n━━━━━━━━━━━━━━━\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✏️ Изменить", callback_data="panel:set_offers")
        ]]),
    )
    await call.answer()


@router.callback_query(F.data == "panel:chat_view")
async def panel_chat_view(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    link = await db.get_setting("agents_chat_link") or "Ссылка ещё не задана"
    await call.message.answer(
        f"🟢 <b>Чат агентов - текущая ссылка</b>\n━━━━━━━━━━━━━━━\n{link}",
        disable_web_page_preview=True, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✏️ Изменить", callback_data="panel:set_chat")
        ]]),
    )
    await call.answer()


# ---------- Добавление урока ----------

@router.callback_query(F.data == "panel:add_material")
async def add_material_start(call: CallbackQuery, state: FSMContext):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(MForms.material_title)
    await call.message.answer("Введите название урока:", reply_markup=kb.cancel_kb)
    await call.answer()


@router.message(MForms.material_title)
async def add_material_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(MForms.material_content)
    await message.answer("Теперь отправьте текст урока (можно со ссылками):", reply_markup=kb.cancel_kb)


@router.message(MForms.material_content)
async def add_material_content(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await db.add_material(data["title"], message.text)
    await message.answer("✅ Урок добавлен в раздел «📗 Обучение»")


# ---------- Удаление урока ----------

@router.callback_query(F.data == "panel:del_material")
async def del_material_list(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    materials = await db.get_materials()
    if not materials:
        await call.message.answer("Уроков пока нет")
    else:
        buttons = [
            [InlineKeyboardButton(text=f"🗑 {m['title']}", callback_data=f"delmat:{m['id']}")]
            for m in materials
        ]
        await call.message.answer(
            "Выберите урок для удаления:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    await call.answer()


@router.callback_query(F.data.startswith("delmat:"))
async def del_material_ask(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    material = await db.get_material(int(call.data.split(":")[1]))
    if not material:
        return await call.answer("Урок не найден", show_alert=True)
    await call.message.answer(
        f"⚠️ <b>Удалить урок «{material['title']}»?</b>\n"
        f"Действие необратимо",
        parse_mode="HTML",
        reply_markup=confirm_delete_kb(f"cfdelmat:{material['id']}"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cfdelmat:"))
async def del_material_confirm(call: CallbackQuery):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await db.delete_material(int(call.data.split(":")[1]))
    await call.message.edit_text("🗑 Урок удалён")
    await call.answer("Удалено ✅")


# ---------- Ссылка на чат агентов ----------

@router.callback_query(F.data == "panel:set_chat")
async def set_chat_start(call: CallbackQuery, state: FSMContext):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(MForms.chat_link)
    await call.message.answer("Отправьте ссылку-приглашение на чат агентов:", reply_markup=kb.cancel_kb)
    await call.answer()


@router.message(MForms.chat_link)
async def set_chat_save(message: Message, state: FSMContext):
    await state.clear()
    await db.set_setting("agents_chat_link", message.text.strip())
    await message.answer("✅ Ссылка на чат агентов сохранена")


# ---------- Условия сети ----------

@router.callback_query(F.data == "panel:set_terms")
async def set_terms_start(call: CallbackQuery, state: FSMContext):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    current = await db.get_setting("network_terms")
    if current:
        await send_post(call.bot, call.message.chat.id, current, header="Сейчас:\n")
    await state.set_state(MForms.terms)
    await call.message.answer("Отправьте новый пост «Условия сети» одним сообщением: текст, файл, фото или гифку (с подписью):", reply_markup=kb.cancel_kb)
    await call.answer()


@router.message(MForms.terms)
async def set_terms_save(message: Message, state: FSMContext):
    await state.clear()
    await db.set_setting("network_terms", serialize_post(message))
    await message.answer("✅ Условия сети сохранены. Агенты увидят их по кнопке «✳️ Условия сети»")


# ---------- Офферы партнёрки ----------

@router.callback_query(F.data == "panel:set_offers")
async def set_offers_start(call: CallbackQuery, state: FSMContext):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    current = await db.get_setting("partner_offers")
    if current:
        await send_post(call.bot, call.message.chat.id, current, header="Сейчас:\n")
    await state.set_state(MForms.offers)
    await call.message.answer("Отправьте новый пост «Офферы партнёрки» одним сообщением: текст, файл, фото или гифку (с подписью):", reply_markup=kb.cancel_kb)
    await call.answer()


@router.message(MForms.offers)
async def set_offers_save(message: Message, state: FSMContext):
    await state.clear()
    await db.set_setting("partner_offers", serialize_post(message))
    await message.answer("✅ Офферы партнёрки сохранены. Агенты увидят их в «✳️ Условия работы»")


# ---------- Начисление баланса ----------

@router.callback_query(F.data == "panel:add_balance")
async def add_balance_start(call: CallbackQuery, state: FSMContext):
    if not await is_staff(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(MForms.balance)
    await call.message.answer("Формат: <code>tg_id сумма</code>\nНапример: 123456789 50", parse_mode="HTML", reply_markup=kb.cancel_kb)
    await call.answer()


@router.message(MForms.balance)
async def add_balance_save(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    try:
        tg_id_str, amount_str = message.text.split()
        tg_id, amount = int(tg_id_str), float(amount_str)
    except ValueError:
        return await message.answer("❌ Неверный формат. Нужно: tg_id сумма")
    if not await db.get_user(tg_id):
        return await message.answer("❌ Пользователь не найден в базе")
    await db.add_balance(tg_id, amount)
    await message.answer(f"✅ Начислено {amount} пользователю {tg_id}")
    try:
        await bot.send_message(tg_id, f"💰 Вам начислено: {amount}")
    except Exception:
        pass


# ---------- Раздел «Управление» (только админ) ----------

class AForms(StatesGroup):
    shop_post = State()
    broadcast = State()
    partner_name = State()
    partner_chat_id = State()
    partner_sheet_url = State()
    edit_partner_chat_id = State()
    edit_partner_sheet_url = State()
    edit_partner_single_topic = State()
    mentor_id = State()
    leader_id = State()
    leader_team_name = State()
    path_query = State()
    role_query = State()
    role_team_name = State()
    block_query = State()
    agent_partner_query = State()


async def is_admin(tg_id: int) -> bool:
    user = await db.get_user(tg_id)
    return bool(user and user["role"] == "admin")


@router.message(F.text == "⚙️ Управление")
async def admin_panel(message: Message):
    if not await is_admin(message.from_user.id):
        return
    await message.answer("⚙️ Управление:", reply_markup=kb.admin_panel_kb)


@router.callback_query(F.data == "adm:mark_paid")
async def adm_mark_paid_ask(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await call.message.answer(
        "⚠️ <b>Подтвердить выплату всем агентам?</b>\n\n"
        "Текущие балансы будут обнулены.\n"
        "Заработок за всё время сохранится в профилях.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да, выплата сделана", callback_data="adm:mark_paid_confirm"),
            InlineKeyboardButton(text="🔴 Отмена", callback_data="adm:mark_paid_cancel"),
        ]]),
    )
    await call.answer()


@router.callback_query(F.data == "adm:mark_paid_cancel")
async def adm_mark_paid_cancel(call: CallbackQuery):
    await call.message.edit_text("🔴 Выплата отменена")
    await call.answer()


@router.callback_query(F.data == "adm:mark_paid_confirm")
async def adm_mark_paid_confirm(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    total = await db.mark_all_balances_paid()
    await call.message.edit_text(
        f"✅ Выплата отмечена.\n\nОбнулено текущих балансов: <b>${total:.2f}</b>\n"
        "Заработок за всё время сохранён.",
        parse_mode="HTML",
    )
    await call.answer("Баланс обнулён ✅")


@router.callback_query(F.data == "adm:export_main_history")
async def adm_export_main_history(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("❌ Доступно только администратору", show_alert=True)

    rows = await db.get_main_sheet_history()
    if not rows:
        return await call.answer("📋 Изменений в листах «Заявки» и «Запуски» пока нет", show_alert=True)

    await call.answer("⏳ Формирую файл истории...")

    import csv
    import io
    from aiogram.types import BufferedInputFile
    from datetime import datetime

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["ID Заявки", "Модель", "Лист", "Колонка", "Старое значение", "Новое значение", "Редактор (Email)", "Дата и время"])

    for r in rows:
        writer.writerow([
            r["interview_id"],
            r["model_name"],
            r["sheet_name"],
            r["col_title"],
            r["old_value"],
            r["new_value"],
            r["user_email"],
            r["created_at"]
        ])

    filename = f"main_sheet_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    csv_bytes = output.getvalue().encode("utf-8-sig")
    document = BufferedInputFile(csv_bytes, filename=filename)

    await call.message.answer_document(
        document=document,
        caption=(
            f"📜 <b>Логи основной таблицы</b>\n"
            f"Листы: «Заявки» и «Запуски»\n"
            f"Всего изменений: <b>{len(rows)}</b>"
        ),
        parse_mode="HTML"
    )


# --- партнёры ---

def agent_partners_kb(agent_tg_id: int, partners, assigned) -> InlineKeyboardMarkup:
    assigned_ids = {p["id"] for p in assigned}
    buttons = [
        [InlineKeyboardButton(
            text=f"{'✅' if p['id'] in assigned_ids else '⬜'} {p['name']}",
            callback_data=f"agpart:{agent_tg_id}:{p['id']}",
        )]
        for p in partners
    ]
    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"agpart_done:{agent_tg_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "adm:agent_partners")
async def adm_agent_partners(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.agent_partner_query)
    await call.message.answer(
        "🔗 Отправьте внутренний ID агента, Telegram ID или @юзернейм:",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.agent_partner_query)
async def adm_agent_partners_find(message: Message, state: FSMContext):
    agent = await db.find_agent(message.text or "")
    if not agent:
        return await message.answer("❌ Агент не найден. Попробуйте ещё раз", reply_markup=kb.cancel_kb)
    await state.clear()
    partners = await db.get_partners()
    if not partners:
        return await message.answer("❌ Сначала добавьте хотя бы одного партнёра", reply_markup=kb.admin_menu)
    assigned = await db.get_agent_partners(agent["tg_id"])
    assigned_text = ", ".join(p["name"] for p in assigned) or "нет"
    await message.answer(
        f"🔗 <b>{agent['full_name'] or agent['tg_id']}</b> (ID: {db.get_agent_code(agent)})\n"
        f"Назначены по порядку: <b>{assigned_text}</b>\n\n"
        "Нажимайте на партнёров, чтобы добавить или убрать. Порядок добавления используется в ротации.",
        parse_mode="HTML",
        reply_markup=agent_partners_kb(agent["tg_id"], partners, assigned),
    )


@router.callback_query(F.data.startswith("agpart:"))
async def adm_agent_partner_toggle(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    _, agent_tg_id, partner_id = call.data.split(":")
    agent_tg_id, partner_id = int(agent_tg_id), int(partner_id)
    await db.toggle_agent_partner(agent_tg_id, partner_id)
    agent = await db.get_user(agent_tg_id)
    partners = await db.get_partners()
    assigned = await db.get_agent_partners(agent_tg_id)
    assigned_text = ", ".join(p["name"] for p in assigned) or "нет"
    await call.message.edit_text(
        f"🔗 <b>{agent['full_name'] or agent_tg_id}</b> (ID: {db.get_agent_code(agent)})\n"
        f"Назначены по порядку: <b>{assigned_text}</b>\n\n"
        "Нажимайте на партнёров, чтобы добавить или убрать. Порядок добавления используется в ротации.",
        parse_mode="HTML",
        reply_markup=agent_partners_kb(agent_tg_id, partners, assigned),
    )
    await call.answer()


@router.callback_query(F.data.startswith("agpart_done:"))
async def adm_agent_partner_done(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await call.message.edit_text("✅ Назначение партнёров сохранено")
    await call.answer()

@router.callback_query(F.data == "adm:add_partner")
async def adm_add_partner(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.partner_name)
    await call.message.answer("Введите название партнёра:", reply_markup=kb.cancel_kb)
    await call.answer()


@router.message(AForms.partner_name)
async def adm_add_partner_name(message: Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(partner_name=name)
    await state.set_state(AForms.partner_chat_id)
    skip_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭ Пропустить")], [KeyboardButton(text="🔴 Отмена")]],
        resize_keyboard=True,
    )
    await message.answer(
        f"Партнёр: <b>{name}</b>\n\n"
        f"Отправьте Telegram Chat ID (начинается с -100...) или ссылку на любой топик партнёра:\n"
        f"<i>(Или нажмите «⏭ Пропустить», если чат не требуется)</i>",
        parse_mode="HTML",
        reply_markup=skip_kb,
    )


@router.message(AForms.partner_chat_id)
async def adm_add_partner_chat(message: Message, state: FSMContext):
    chat_id = ""
    if message.text != "⏭ Пропустить":
        parsed_chat, _ = db.parse_topic_input(message.text)
        if parsed_chat:
            chat_id = parsed_chat
        elif message.forward_from_chat:
            chat_id = str(message.forward_from_chat.id)
        else:
            chat_id = message.text.strip()
    await state.update_data(partner_chat_id=chat_id)
    await state.set_state(AForms.partner_sheet_url)
    skip_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭ Пропустить")], [KeyboardButton(text="🔴 Отмена")]],
        resize_keyboard=True,
    )
    await message.answer(
        "Отправьте URL вебхука Google Apps Script для таблицы этого партнёра:\n"
        "<i>(Или нажмите «⏭ Пропустить»)</i>",
        parse_mode="HTML",
        reply_markup=skip_kb,
    )


@router.message(AForms.partner_sheet_url)
async def adm_add_partner_sheet(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    sheet_url = "" if message.text == "⏭ Пропустить" else message.text.strip()
    name = data["partner_name"]
    chat_id = data.get("partner_chat_id", "")
    partner_id = await db.add_partner(name, chat_id=chat_id, sheet_url=sheet_url)
    await message.answer(
        f"✅ Партнёр «{name}» успешно добавлен!\n\n"
        f"Задайте топики этапов в «⚙️ Управление» ➔ «🤝 Партнёры».",
        reply_markup=kb.admin_menu,
    )


@router.callback_query(F.data == "adm:partners")
async def adm_partners(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    partners = await db.get_partners()
    if not partners:
        await call.message.answer("Партнёров нет. Добавьте через «➕ Добавить партнёра»")
    else:
        for pt in partners:
            pt_dict = dict(pt)
            chat_str = f"<code>{pt_dict.get('chat_id')}</code>" if pt_dict.get('chat_id') else "❌ Не настроен"
            sheet_str = f"<code>{pt_dict.get('sheet_url')}</code>" if pt_dict.get('sheet_url') else "❌ Не настроен"
            
            app_t = pt_dict.get("topic_applications") or "❌"
            conf_t = pt_dict.get("topic_confirmations") or "❌"
            sobes_t = pt_dict.get("topic_sobes") or "❌"
            reg_t = pt_dict.get("topic_registration") or "❌"
            s1_t = pt_dict.get("topic_shift1") or "❌"
            s2_t = pt_dict.get("topic_shift2") or "❌"
            canc_t = pt_dict.get("topic_cancelled") or "❌"

            text = (
                f"🤝 <b>Партнёр: {pt['name']}</b> (ID: {pt['id']})\n"
                f"💬 Chat ID: {chat_str}\n"
                f"📊 Webhook Таблицы: {sheet_str}\n\n"
                f"📌 <b>Топики этапов:</b>\n"
                f"1️⃣ Заявки: <code>{app_t}</code>\n"
                f"2️⃣ Подтверждения: <code>{conf_t}</code>\n"
                f"3️⃣ Прошла собес: <code>{sobes_t}</code>\n"
                f"4️⃣ Регистрация: <code>{reg_t}</code>\n"
                f"5️⃣ 1-я смена: <code>{s1_t}</code>\n"
                f"6️⃣ 2-я смена: <code>{s2_t}</code>\n"
                f"7️⃣ Слив: <code>{canc_t}</code>"
            )
            buttons = [
                [InlineKeyboardButton(text="✏️ Чат ID", callback_data=f"setpchat:{pt['id']}"),
                 InlineKeyboardButton(text="✏️ Sheet URL", callback_data=f"setpsheet:{pt['id']}")],
                [InlineKeyboardButton(text="✏️ 1. Заявки", callback_data=f"setptop:{pt['id']}:topic_applications"),
                 InlineKeyboardButton(text="✏️ 2. Подтверждения", callback_data=f"setptop:{pt['id']}:topic_confirmations")],
                [InlineKeyboardButton(text="✏️ 3. Прошла собес", callback_data=f"setptop:{pt['id']}:topic_sobes"),
                 InlineKeyboardButton(text="✏️ 4. Регистрация", callback_data=f"setptop:{pt['id']}:topic_registration")],
                [InlineKeyboardButton(text="✏️ 5. 1-я смена", callback_data=f"setptop:{pt['id']}:topic_shift1"),
                 InlineKeyboardButton(text="✏️ 6. 2-я смена", callback_data=f"setptop:{pt['id']}:topic_shift2")],
                [InlineKeyboardButton(text="✏️ 7. Слив", callback_data=f"setptop:{pt['id']}:topic_cancelled")],
                [InlineKeyboardButton(text="🗑 Удалить партнёра", callback_data=f"delpart:{pt['id']}")]
            ]
            await call.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@router.callback_query(F.data.startswith("setptop:"))
async def set_ptopic_start(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    parts = call.data.split(":")
    partner_id = int(parts[1])
    topic_key = parts[2]
    
    topic_labels = {
        "topic_applications": "1. Заявки",
        "topic_confirmations": "2. Подтверждения",
        "topic_sobes": "3. Прошла собес",
        "topic_registration": "4. Регистрация",
        "topic_shift1": "5. Первая смена",
        "topic_shift2": "6. Вторая смена",
        "topic_cancelled": "7. Слив"
    }
    label = topic_labels.get(topic_key, topic_key)
    
    await state.update_data(edit_partner_id=partner_id, edit_topic_key=topic_key)
    await state.set_state(AForms.edit_partner_single_topic)
    await call.message.answer(
        f"Отправьте ссылку на топик <b>«{label}»</b> (например <code>https://t.me/c/1234567890/45</code>)\n"
        f"или просто числовой ID топика (например <code>45</code>):\n"
        f"<i>(Отправьте 0 для сброса)</i>",
        parse_mode="HTML",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.edit_partner_single_topic)
async def set_ptopic_save(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    partner_id = data["edit_partner_id"]
    topic_key = data["edit_topic_key"]
    
    val = message.text.strip()
    if val == "0":
        await db.update_partner_topic(partner_id, topic_key, None)
        return await message.answer("✅ Топик сброшен", reply_markup=kb.admin_menu)
        
    chat_id, topic_id = db.parse_topic_input(val)
    if topic_id is None:
        return await message.answer("❌ Не удалось распознать топик. Отправьте ссылку из Telegram или числовой ID.")
        
    await db.update_partner_topic(partner_id, topic_key, topic_id, chat_id=chat_id)
    await message.answer(f"✅ Топик обновлен: ID <code>{topic_id}</code>" + (f" (Chat ID: <code>{chat_id}</code>)" if chat_id else ""),
                         parse_mode="HTML", reply_markup=kb.admin_menu)


@router.callback_query(F.data.startswith("setpchat:"))
async def set_pchat_start(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    partner_id = int(call.data.split(":")[1])
    await state.update_data(edit_partner_id=partner_id)
    await state.set_state(AForms.edit_partner_chat_id)
    await call.message.answer(
        "Отправьте новый Telegram Chat ID (начинается с -100...) или ссылку на любой топик партнёра:",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.edit_partner_chat_id)
async def set_pchat_save(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    partner_id = data["edit_partner_id"]
    parsed_chat, _ = db.parse_topic_input(message.text)
    if parsed_chat:
        chat_id = parsed_chat
    elif message.forward_from_chat:
        chat_id = str(message.forward_from_chat.id)
    else:
        chat_id = message.text.strip()
    await db.update_partner_info(partner_id, chat_id=chat_id)
    user = await db.get_user(message.from_user.id)
    await message.answer(f"✅ Chat ID партнёра обновлён: <code>{chat_id}</code>", parse_mode="HTML", reply_markup=kb.admin_menu)


@router.callback_query(F.data.startswith("setpsheet:"))
async def set_psheet_start(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    partner_id = int(call.data.split(":")[1])
    await state.update_data(edit_partner_id=partner_id)
    await state.set_state(AForms.edit_partner_sheet_url)
    await call.message.answer(
        "Отправьте новый URL вебхука Google Apps Script для этого партнёра:",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.edit_partner_sheet_url)
async def set_psheet_save(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    partner_id = data["edit_partner_id"]
    sheet_url = message.text.strip()
    await db.update_partner_info(partner_id, sheet_url=sheet_url)
    user = await db.get_user(message.from_user.id)
    await message.answer("✅ Google Sheet Webhook URL партнёра обновлён", reply_markup=kb.admin_menu)



# --- наставники ---

@router.callback_query(F.data == "adm:add_mentor")
async def adm_add_mentor(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.mentor_id)
    await call.message.answer(
        "Отправьте Telegram ID пользователя, которого назначить наставником.\n"
        "Он должен был хотя бы раз нажать /start в боте.\n"
        "Узнать ID можно в его профиле в боте или через @userinfobot",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.mentor_id)
async def adm_add_mentor_save(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    text = message.text.strip()
    if not text.isdigit():
        return await message.answer("❌ Нужен числовой Telegram ID. Попробуйте ещё раз через «⚙️ Управление»")
    tg_id = int(text)
    user = await db.get_user(tg_id)
    if not user:
        return await message.answer("❌ Пользователь не найден. Он должен сначала нажать /start в боте")
    await db.set_role(tg_id, "mentor")
    await message.answer(f"✅ {user['full_name'] or tg_id} назначен наставником")
    try:
        await bot.send_message(tg_id, "🎓 Вам выдана роль наставника!", reply_markup=kb.mentor_menu)
    except Exception:
        pass


@router.callback_query(F.data == "adm:demote")
async def adm_demote_list(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    mentors = await db.get_users_by_role("mentor")
    if not mentors:
        await call.message.answer("Наставников нет")
    else:
        buttons = [
            [InlineKeyboardButton(
                text=f"⬇️ {m['full_name']} (ID: {m.get('agent_code') or (1000 + m['id'])})",
                callback_data=f"demote:{m['tg_id']}",
            )]
            for m in mentors
        ]
        await call.message.answer(
            "Нажмите на наставника, чтобы снять роль:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    await call.answer()


@router.callback_query(F.data.startswith("demote:"))
async def adm_demote(call: CallbackQuery, bot: Bot):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    tg_id = int(call.data.split(":")[1])
    await db.set_role(tg_id, "agent")
    await call.message.edit_text("✅ Роль наставника снята, пользователь снова агент")
    try:
        await bot.send_message(tg_id, "Ваша роль изменена: вы снова агент", reply_markup=kb.main_menu)
    except Exception:
        pass
    await call.answer()


# --- лидеры и команды ---

@router.callback_query(F.data == "adm:add_leader")
async def adm_add_leader(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.leader_id)
    await call.message.answer(
        "👑 Отправьте внутренний ID агента (например 1001), Telegram ID или @юзернейм будущего лидера:",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.leader_id)
async def adm_add_leader_find(message: Message, state: FSMContext):
    agent = await db.find_agent(message.text or "")
    if not agent:
        return await message.answer("❌ Агент не найден. Попробуйте ещё раз", reply_markup=kb.cancel_kb)
    agent_code = db.get_agent_code(agent)
    await state.update_data(leader_tg_id=agent["tg_id"], leader_name=f"{agent['full_name']} (ID: {agent_code})")
    await state.set_state(AForms.leader_team_name)
    await message.answer(
        f"Лидер: <b>{agent['full_name']}</b> (ID: {agent_code})\n\n"
        f"Теперь введите название команды:",
        parse_mode="HTML",
        reply_markup=kb.cancel_kb,
    )


@router.message(AForms.leader_team_name)
async def adm_add_leader_save(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    team_name = message.text.strip()
    team_id = await db.add_team(team_name, data["leader_tg_id"])
    await db.set_role(data["leader_tg_id"], "leader")
    await db.set_team(data["leader_tg_id"], team_id)
    await message.answer(
        f"✅ Команда «{team_name}» создана, лидер - {data['leader_name']}"
    )
    try:
        await bot.send_message(
            data["leader_tg_id"],
            f"👑 Вы назначены лидером команды «{team_name}»!",
            reply_markup=kb.leader_menu,
        )
    except Exception:
        pass


@router.callback_query(F.data == "adm:agent_path")
async def adm_agent_path(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.path_query)
    await call.message.answer(
        "🧭 Отправьте внутренний ID агента (например 1001), Telegram ID или @юзернейм:",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.path_query)
async def adm_agent_path_find(message: Message, state: FSMContext):
    agent = await db.find_agent(message.text or "")
    if not agent:
        return await message.answer("❌ Агент не найден. Попробуйте ещё раз", reply_markup=kb.cancel_kb)
    await state.clear()
    teams = await db.get_teams()
    buttons = [[InlineKeyboardButton(text="🧍 Соло", callback_data=f"patha:{agent['tg_id']}:0")]]
    buttons += [
        [InlineKeyboardButton(text=f"👥 {t['name']}", callback_data=f"patha:{agent['tg_id']}:{t['id']}")]
        for t in teams
    ]
    team = await db.get_team(agent["team_id"]) if agent["team_id"] else None
    current_path = f"команда «{team['name']}»" if team else "соло"
    await message.answer(
        f"🧭 Агент: <b>{agent['full_name'] or agent['tg_id']}</b> (№{agent['id']})\n"
        f"Сейчас: {current_path}\n\n"
        f"Выберите путь:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("patha:"))
async def adm_agent_path_set(call: CallbackQuery, bot: Bot):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    _, tg_id, team_id = call.data.split(":")
    tg_id, team_id = int(tg_id), int(team_id)
    if team_id == 0:
        await db.set_team(tg_id, None)
        await call.message.edit_text("✅ Агент переведён в соло")
        note = "🧭 Вы переведены на путь: соло"
    else:
        team = await db.get_team(team_id)
        await db.set_team(tg_id, team_id)
        await call.message.edit_text(f"✅ Агент добавлен в команду «{team['name']}»")
        note = f"🧭 Вы добавлены в команду «{team['name']}»!"
    try:
        await bot.send_message(tg_id, note)
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "adm:teams")
async def adm_teams(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    teams = await db.get_teams()
    if not teams:
        await call.message.answer("Команд пока нет. Создайте через «👑 Назначить лидера»")
    else:
        buttons = [
            [InlineKeyboardButton(text=f"👥 {t['name']}", callback_data=f"teamv:{t['id']}")]
            for t in teams
        ]
        await call.message.answer(
            "👥 Команды:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    await call.answer()


@router.callback_query(F.data.startswith("teamv:"))
async def adm_team_view(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    team = await db.get_team(int(call.data.split(":")[1]))
    if not team:
        return await call.answer("Команда не найдена", show_alert=True)
    leader = await db.get_user(team["leader_tg_id"])
    agents = await db.team_agents(team["id"])
    models = await db.team_models(team["id"])

    agent_lines = [
        f"{i}. Агент ID {a.get('agent_code') or (1000 + a['id'])} {a['full_name'] or '-'}"
        f"{' 👑' if a['tg_id'] == team['leader_tg_id'] else ''}"
        for i, a in enumerate(agents, 1)
    ] or ["- пусто -"]

    active = [m for m in models if m["status"] == "active"]
    dropped = [m for m in models if m["status"] == "dropped"]
    model_lines = [
        f"{i}. <b>{m['name']}</b> - {m['shifts']} смен(-ы) (агент ID {m['owner_no']})"
        for i, m in enumerate(active, 1)
    ] or ["- пусто -"]

    text = (
        f"👥 <b>КОМАНДА «{team['name'].upper()}»</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👑 Лидер: <b>{leader['full_name'] if leader else '-'}</b> "
        f"(ID: {leader.get('agent_code') if leader else '-'})\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🧑 <b>Агенты - {len(agents)}</b>\n" + "\n".join(agent_lines) +
        f"\n━━━━━━━━━━━━━━━\n"
        f"💃 <b>Модели:</b> ✅ активные - {len(active)} · 🚫 слив - {len(dropped)}\n" +
        "\n".join(model_lines)
    )
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await call.message.answer(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Удалить команду", callback_data=f"delteam:{team['id']}")
        ]]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("delteam:"))
async def delete_team_ask(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    team = await db.get_team(int(call.data.split(":")[1]))
    if not team:
        return await call.answer("Команда не найдена", show_alert=True)
    await call.message.answer(
        f"🗑 Удалить команду «{team['name']}»?\nАгенты этой команды перейдут в статус «соло»",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delteamc:{team['id']}"),
            InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel"),
        ]]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("delteamc:"))
async def delete_team_confirm(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    team_id = int(call.data.split(":")[1])
    await db.delete_team(team_id)
    await call.message.edit_text("✅ Команда удалена, агенты переведены в соло")
    await call.answer()


# ---------- Управление ролями (только админ) ----------

ROLE_TITLES = {"agent": "🧑 Агент", "leader": "👑 Лидер",
               "mentor": "🎓 Наставник", "admin": "⚙️ Админ"}

ROLE_MENUS = {"agent": "main_menu", "leader": "leader_menu",
              "mentor": "mentor_menu", "admin": "admin_menu"}


def role_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧑 Агент", callback_data=f"setrole:{tg_id}:agent"),
         InlineKeyboardButton(text="👑 Лидер", callback_data=f"setrole:{tg_id}:leader")],
        [InlineKeyboardButton(text="🎓 Наставник", callback_data=f"setrole:{tg_id}:mentor"),
         InlineKeyboardButton(text="⚙️ Админ", callback_data=f"setrole:{tg_id}:admin")],
    ])


@router.callback_query(F.data == "adm:roles")
async def adm_roles(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.role_query)
    await call.message.answer(
        "🎭 Отправьте внутренний ID агента (например 1001), Telegram ID или @юзернейм:",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.role_query)
async def adm_roles_find(message: Message, state: FSMContext):
    agent = await db.find_agent(message.text or "")
    if not agent:
        return await message.answer("❌ Пользователь не найден. Попробуйте ещё раз",
                                    reply_markup=kb.cancel_kb)
    await state.clear()
    current = ROLE_TITLES.get(agent["role"], agent["role"])
    agent_code = db.get_agent_code(agent)
    await message.answer(
        f"🎭 <b>{agent['full_name']}</b> (ID: {agent_code})\n"
        f"Текущая роль: <b>{current}</b>\n\n"
        f"Выберите новую роль (текущую роль можно забрать, выдав «Агент»):",
        parse_mode="HTML",
        reply_markup=role_kb(agent["tg_id"]),
    )


@router.callback_query(F.data.startswith("setrole:"))
async def adm_set_role(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    _, tg_id, role = call.data.split(":")
    tg_id = int(tg_id)
    if tg_id == call.from_user.id and role != "admin":
        return await call.answer("Нельзя снять роль с самого себя - иначе потеряете доступ к управлению",
                                 show_alert=True)
    target = await db.get_user(tg_id)
    if not target:
        return await call.answer("Пользователь не найден", show_alert=True)
    if role == "leader" and not await db.get_team_by_leader(tg_id):
        # у лидера должна быть команда - создаём
        await state.set_state(AForms.role_team_name)
        await state.update_data(role_tg_id=tg_id,
                                role_name_target=target["full_name"] or str(tg_id))
        await call.message.answer("👑 Введите название команды для нового лидера:",
                                  reply_markup=kb.cancel_kb)
        return await call.answer()
    await db.set_role(tg_id, role)
    title = ROLE_TITLES[role]
    await call.message.edit_text(f"✅ Роль обновлена: <b>{title}</b>", parse_mode="HTML")
    try:
        await bot.send_message(tg_id, f"🎭 Ваша роль изменена: {title}",
                               reply_markup=getattr(kb, ROLE_MENUS[role]))
    except Exception:
        pass
    await call.answer("Готово ✅")


@router.message(AForms.role_team_name)
async def adm_set_role_leader(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    team_name = message.text.strip()
    team_id = await db.add_team(team_name, data["role_tg_id"])
    await db.set_role(data["role_tg_id"], "leader")
    await db.set_team(data["role_tg_id"], team_id)
    await message.answer(
        f"✅ {data['role_name_target']} - теперь 👑 Лидер команды «{team_name}»"
    )
    try:
        await bot.send_message(data["role_tg_id"],
                               f"👑 Вы назначены лидером команды «{team_name}»!",
                               reply_markup=kb.leader_menu)
    except Exception:
        pass


# ---------- Блокировка агента (только админ) ----------

@router.callback_query(F.data == "adm:block")
async def adm_block(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.block_query)
    await call.message.answer(
        "🚷 Отправьте номер агента, Telegram ID или @юзернейм:",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.block_query)
async def adm_block_find(message: Message, state: FSMContext):
    agent = await db.find_agent(message.text or "")
    if not agent:
        return await message.answer("❌ Пользователь не найден. Попробуйте ещё раз",
                                    reply_markup=kb.cancel_kb)
    await state.clear()
    if agent["role"] == "banned":
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"unblock:{agent['tg_id']}")
        ]])
        status = "⛔ Заблокирован"
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⛔ Заблокировать", callback_data=f"blockask:{agent['tg_id']}")
        ]])
        status = "🟢 Активен"
    await message.answer(
        f"🚷 <b>{agent['full_name'] or agent['tg_id']}</b> (№{agent['id']}, @{agent['username'] or '-'})\n"
        f"Статус: <b>{status}</b>",
        parse_mode="HTML",
        reply_markup=markup,
    )


@router.callback_query(F.data.startswith("blockask:"))
async def adm_block_ask(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    tg_id = int(call.data.split(":")[1])
    if tg_id == call.from_user.id:
        return await call.answer("Нельзя заблокировать самого себя", show_alert=True)
    agent = await db.get_user(tg_id)
    if not agent:
        return await call.answer("Пользователь не найден", show_alert=True)
    await call.message.answer(
        f"⚠️ <b>Заблокировать {agent['full_name'] or tg_id}?</b>\n\n"
        f"Он полностью потеряет доступ к боту: меню, заявки, модели, аналитика\n"
        f"Разблокировать можно в любой момент здесь же",
        parse_mode="HTML",
        reply_markup=confirm_delete_kb(f"cfblock:{tg_id}"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cfblock:"))
async def adm_block_confirm(call: CallbackQuery, bot: Bot):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    tg_id = int(call.data.split(":")[1])
    if tg_id == call.from_user.id:
        return await call.answer("Нельзя заблокировать самого себя", show_alert=True)
    await db.set_role(tg_id, "banned")
    await call.message.edit_text("⛔ Агент заблокирован")
    try:
        await bot.send_message(tg_id, "⛔ Ваш доступ к боту заблокирован администратором")
    except Exception:
        pass
    await call.answer("Заблокирован ⛔")


@router.callback_query(F.data.startswith("unblock:"))
async def adm_unblock(call: CallbackQuery, bot: Bot):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    tg_id = int(call.data.split(":")[1])
    await db.set_role(tg_id, "agent")
    await call.message.edit_text("✅ Агент разблокирован, роль: агент")
    try:
        await bot.send_message(tg_id, "✅ Ваш доступ восстановлен! Нажмите /start",
                               reply_markup=kb.main_menu)
    except Exception:
        pass
    await call.answer("Разблокирован ✅")


# ---------- Топы: воронка по агентам и командам (только админ) ----------

from datetime import datetime as _dt, timedelta as _td

TOP_SEP = "━━━━━━━━━━━━━━━"


def _pct(prev: int, cur: int) -> int:
    return round(cur / prev * 100) if prev else 0


def _funnel_line(records: int, regs: int, s1: int, s2: int) -> str:
    return (f"Записи: <b>{records}</b>\n"
            f"├ Регистрации: <b>{regs}</b> ({_pct(records, regs)}%)\n"
            f"├ Первая смена: <b>{s1}</b> ({_pct(regs, s1)}%)\n"
            f"└ Вторая смена: <b>{s2}</b> ({_pct(s1, s2)}%)")


@router.callback_query(F.data == "adm:tops")
async def adm_tops(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await call.message.answer(
        "🏆 Топы - по кому строим?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 По агентам", callback_data="top:agents"),
             InlineKeyboardButton(text="👥 По командам", callback_data="top:teams")],
            [InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("top:"))
async def adm_tops_kind(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    kind = call.data.split(":")[1]
    await call.message.answer(
        "📆 За какой период?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Сегодня", callback_data=f"topper:{kind}:1"),
             InlineKeyboardButton(text="7 дней", callback_data=f"topper:{kind}:7")],
            [InlineKeyboardButton(text="30 дней", callback_data=f"topper:{kind}:30"),
             InlineKeyboardButton(text="Всё время", callback_data=f"topper:{kind}:all")],
            [InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("topper:"))
async def adm_tops_render(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    _, kind, per = call.data.split(":")
    today = _dt.utcnow().date()
    if per == "all":
        dfrom, dto, label = "1970-01-01", "2100-01-01", "всё время"
    else:
        days = int(per)
        dfrom = (today - _td(days=days - 1)).isoformat()
        dto = (today + _td(days=1)).isoformat()
        label = "сегодня" if days == 1 else f"последние {days} дней"

    if kind == "agents":
        rows = await db.top_agents(dfrom, dto, solo_only=True)
        shifts = await db.shifts_funnel_by_agent(dfrom, dto)
        title = "🏆 <b>ТОП АГЕНТОВ</b>"
        lines = []
        for i, r in enumerate(rows, 1):
            s1, s2 = shifts.get(r["tg_id"], (0, 0))
            lines.append(
                f"<b>{i}. {r['full_name'] or '-'}</b> "
                f"(№{r['agent_no']}, @{r['username'] or '-'}, ID: <code>{r['tg_id']}</code>)\n"
                f"{_funnel_line(r['records'], r['regs'] or 0, s1 or 0, s2 or 0)}"
            )
    else:
        rows = await db.top_teams(dfrom, dto)
        shifts = await db.shifts_funnel_by_team(dfrom, dto)
        title = "🏆 <b>ТОП КОМАНД</b>"
        lines = []
        for i, r in enumerate(rows, 1):
            s1, s2 = shifts.get(r["team_id"], (0, 0))
            lines.append(
                f"<b>{i}. {r['team_name']}</b>\n"
                f"{_funnel_line(r['records'], r['regs'] or 0, s1 or 0, s2 or 0)}"
            )

    if not lines:
        body = "🤷 За выбранный период данных нет"
    else:
        body = "\n\n".join(lines)
    await call.message.answer(
        f"{title}\n{TOP_SEP}\n📆 Период: <b>{label}</b>\n{TOP_SEP}\n\n{body}",
        parse_mode="HTML",
    )
    await call.answer()


# ---------- Магазин: пост (только админ) ----------

@router.callback_query(F.data == "adm:shop_view")
async def adm_shop_view(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await send_post(
        call.bot, call.message.chat.id, await db.get_setting("shop_post"),
        header="🛒 <b>МАГАЗИН · ТЕКУЩИЙ ПОСТ</b>\n━━━━━━━━━━━━━━━\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✏️ Изменить", callback_data="adm:shop_set")
        ]]),
    )
    await call.answer()


@router.callback_query(F.data == "adm:shop_set")
async def adm_shop_set(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.shop_post)
    await call.message.answer(
        "Отправьте новый пост «Магазин» одним сообщением: текст, файл, фото или гифку (с подписью):",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.shop_post)
async def adm_shop_save(message: Message, state: FSMContext):
    await state.clear()
    await db.set_setting("shop_post", serialize_post(message))
    await message.answer("✅ Пост «Магазин» сохранён. Агенты увидят его по кнопке «🛒 Магазин»")


# ---------- Рассылка всем пользователям (только админ, с подтверждением) ----------

@router.callback_query(F.data == "adm:broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await state.set_state(AForms.broadcast)
    total = len(await db.get_all_active_tg_ids())
    await call.message.answer(
        f"📣 <b>РАССЫЛКА</b>\n━━━━━━━━━━━━━━━\n"
        f"Получателей: <b>{total}</b>\n\n"
        f"Отправьте сообщение для рассылки: текст, фото, файл или гифку с подписью. "
        f"Оно уйдёт всем ровно в таком виде",
        parse_mode="HTML",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(AForms.broadcast)
async def adm_broadcast_preview(message: Message, state: FSMContext):
    await state.update_data(bc_chat_id=message.chat.id, bc_message_id=message.message_id)
    await state.set_state(None)
    await message.answer("👀 Так увидят рассылку получатели:")
    await message.bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    total = len(await db.get_all_active_tg_ids())
    await message.answer(
        f"⚠️ <b>Отправить рассылку {total} пользователям?</b>\nОтменить после отправки нельзя",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, отправить", callback_data="bc_send")],
            [InlineKeyboardButton(text="🔴 Отмена", callback_data="bc_cancel")],
        ]),
    )


@router.callback_query(F.data == "bc_cancel")
async def adm_broadcast_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("🔴 Рассылка отменена")
    await call.answer()


@router.callback_query(F.data == "bc_send")
async def adm_broadcast_send(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    data = await state.get_data()
    await state.clear()
    if not data.get("bc_message_id"):
        return await call.answer("Сообщение для рассылки не найдено, начните заново", show_alert=True)
    await call.message.edit_text("📣 Рассылка запущена, это займёт немного времени…")
    sent, failed = 0, 0
    for tg_id in await db.get_all_active_tg_ids():
        if tg_id == call.from_user.id:
            continue
        try:
            await bot.copy_message(tg_id, data["bc_chat_id"], data["bc_message_id"])
            sent += 1
        except Exception:
            failed += 1  # заблокировал бота / удалил аккаунт
        await asyncio.sleep(0.05)  # защита от лимитов Telegram
    await call.message.answer(
        f"✅ <b>РАССЫЛКА ЗАВЕРШЕНА</b>\n━━━━━━━━━━━━━━━\n"
        f"Доставлено: <b>{sent}</b>\nНе доставлено: <b>{failed}</b>",
        parse_mode="HTML",
    )
    await call.answer()


# ---------- Партнёры: команды-дубли (только админ) ----------

@router.message(Command("addpartner"))
async def add_partner(message: Message):
    if not await is_admin(message.from_user.id):
        return
    name = message.text.replace("/addpartner", "", 1).strip()
    if not name:
        return await message.answer("Формат: /addpartner Название партнёра")
    await db.add_partner(name)
    await message.answer(f"✅ Партнёр «{name}» добавлен")


@router.message(Command("partners"))
async def list_partners(message: Message):
    if not await is_admin(message.from_user.id):
        return
    partners = await db.get_partners()
    if not partners:
        return await message.answer("Партнёров нет. Добавить: /addpartner Название")
    buttons = [
        [InlineKeyboardButton(text=f"🗑 {pt['name']}", callback_data=f"delpart:{pt['id']}")]
        for pt in partners
    ]
    await message.answer(
        "🤝 Партнёры (нажмите, чтобы удалить):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("delpart:"))
async def delete_partner_ask(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    partner = await db.get_partner(int(call.data.split(":")[1]))
    if not partner:
        return await call.answer("Партнёр не найден", show_alert=True)
    await call.message.answer(
        f"⚠️ <b>Удалить партнёра «{partner['name']}»?</b>\n"
        f"Действие необратимо",
        parse_mode="HTML",
        reply_markup=confirm_delete_kb(f"cfdelpart:{partner['id']}"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("cfdelpart:"))
async def delete_partner_confirm(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    await db.delete_partner(int(call.data.split(":")[1]))
    await call.message.edit_text("🗑 Партнёр удалён")
    await call.answer("Удалено ✅")


# ---------- Назначение наставника (только админ) ----------

@router.message(Command("mentor"))
async def make_mentor(message: Message, bot: Bot):
    user = await db.get_user(message.from_user.id)
    if not user or user["role"] != "admin":
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Формат: /mentor tg_id")
    tg_id = int(parts[1])
    if not await db.get_user(tg_id):
        return await message.answer("Пользователь не найден. Он должен сначала нажать /start")
    await db.set_role(tg_id, "mentor")
    await message.answer(f"✅ Пользователь {tg_id} назначен наставником")
    try:
        await bot.send_message(tg_id, "🎓 Вам выдана роль наставника!", reply_markup=kb.mentor_menu)
    except Exception:
        pass


@router.message(Command("demote"))
async def demote(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user or user["role"] != "admin":
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("Формат: /demote tg_id")
    await db.set_role(int(parts[1]), "agent")
    await message.answer("✅ Роль снята, пользователь снова агент")
