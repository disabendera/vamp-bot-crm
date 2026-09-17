from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import db
import keyboards as kb

router = Router()

STATUS_TITLES = {"active": "Активные ✅", "dropped": "Слив 🚫"}


class SearchForm(StatesGroup):
    query = State()


async def can_search(tg_id: int) -> bool:
    user = await db.get_user(tg_id)
    return bool(user and user["role"] not in ("pending", "banned"))


async def can_view_models(tg_id: int) -> bool:
    user = await db.get_user(tg_id)
    return bool(user and user["role"] in ("leader", "mentor", "admin"))


async def render_agent_card(agent, with_model_buttons: bool = True):
    team = await db.get_team(agent["team_id"]) if agent["team_id"] else None
    summary = await db.models_summary(agent["tg_id"])
    active_cnt, active_shifts = summary.get("active", (0, 0))
    dropped_cnt, _ = summary.get("dropped", (0, 0))
    # стата-воронка за всё время
    breakdown = await db.analytics_interviews("1970-01-01", "2100-01-01", tg_id=agent["tg_id"])
    records = sum(breakdown.values())
    regs = sum(breakdown.get(st, 0) for st in db.REG_STATUSES)
    shifts = await db.shifts_funnel_by_agent("1970-01-01", "2100-01-01")
    s1, s2 = shifts.get(agent["tg_id"], (0, 0))
    role_name = {"agent": "🧑 Агент", "leader": "👑 Лидер", "mentor": "🎓 Наставник",
                 "admin": "⚙️ Администратор", "pending": "⏳ Заявка", "banned": "⛔ Отклонён"}
    path = f"👥 Команда «{team['name']}»" if team else "🧍 Соло"
    agent_code = db.get_agent_code(agent)
    text = (
        f"🔍 <b>АНКЕТА АГЕНТА</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>{agent['full_name'] or '-'}</b>\n"
        f"🔢 Агент ID: <code>{agent_code}</code>\n"
        f"🎭 Роль: {role_name.get(agent['role'], agent['role'])}\n"
        f"🧭 Путь: {path}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ Активные модели - <b>{active_cnt}</b>\n"
        f"🚫 Слив - <b>{dropped_cnt}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 <b>Стата за всё время</b>\n"
        f"Записи: <b>{records}</b>\n"
        f"├ Регистрации: <b>{regs}</b> ({_card_pct(records, regs)}%)\n"
        f"├ Первая смена: <b>{s1 or 0}</b> ({_card_pct(regs, s1 or 0)}%)\n"
        f"└ Вторая смена: <b>{s2 or 0}</b> ({_card_pct(s1 or 0, s2 or 0)}%)"
    )
    markup = None
    if with_model_buttons:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Активные ✅", callback_data=f"aview:{agent['tg_id']}:active"),
            InlineKeyboardButton(text="Слив 🚫", callback_data=f"aview:{agent['tg_id']}:dropped"),
        ]])
    return text, markup


def _card_pct(prev: int, cur: int) -> int:
    return round(cur / prev * 100) if prev else 0


@router.message(F.text == "🔍 Поиск агента")
async def search_start(message: Message, state: FSMContext):
    if not await can_search(message.from_user.id):
        return
    await db.refresh_user_info(
        message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    await state.set_state(SearchForm.query)
    await message.answer(
        "🔍 Отправьте внутренний ID агента (например 1001), Telegram ID или @юзернейм:",
        reply_markup=kb.cancel_kb,
    )


@router.message(SearchForm.query)
async def search_run(message: Message, state: FSMContext):
    agent = await db.find_agent(message.text or "")
    if not agent:
        return await message.answer(
            "❌ Агент не найден. Проверьте номер/айди/юзернейм и попробуйте ещё раз",
            reply_markup=kb.cancel_kb,
        )
    await state.clear()
    text, markup = await render_agent_card(
        agent, with_model_buttons=await can_view_models(message.from_user.id))
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.startswith("aview:"))
async def agent_models(call: CallbackQuery):
    if not await can_view_models(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    _, tg_id, status = call.data.split(":")
    models = await db.get_models(int(tg_id), status)
    if not models:
        await call.message.answer(f"{STATUS_TITLES[status]}: список пуст")
    else:
        lines = [
            f"{i}. <b>{m['name']}</b>"
            f"{' ' + m['phone'] if m['phone'] else ''}"
            f"{' @' + m['username'] if m['username'] else ''}"
            f" - {m['shifts']} смен(-ы)"
            for i, m in enumerate(models, 1)
        ]
        await call.message.answer(
            f"{STATUS_TITLES[status]}:\n\n" + "\n".join(lines), parse_mode="HTML"
        )
    await call.answer()


# ---------- Аналитика ----------

from datetime import datetime, timedelta



AN_MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
AN_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

import calendar as _an_calendar


def an_calendar_kb(year: int, month: int, stage: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{AN_MONTHS[month - 1]} {year}", callback_data="acal:noop")]]
    rows.append([InlineKeyboardButton(text=d, callback_data="acal:noop") for d in AN_WEEKDAYS])
    for week in _an_calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if not day:
                row.append(InlineKeyboardButton(text=" ", callback_data="acal:noop"))
            else:
                row.append(InlineKeyboardButton(
                    text=str(day), callback_data=f"acal:day:{stage}:{year}:{month}:{day}"))
        rows.append(row)
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    rows.append([
        InlineKeyboardButton(text="<", callback_data=f"acal:nav:{stage}:{prev_y}:{prev_m}"),
        InlineKeyboardButton(text="Отмена", callback_data="icancel"),
        InlineKeyboardButton(text=">", callback_data=f"acal:nav:{stage}:{next_y}:{next_m}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня", callback_data="an:per:1"),
         InlineKeyboardButton(text="7 дней", callback_data="an:per:7")],
        [InlineKeyboardButton(text="30 дней", callback_data="an:per:30"),
         InlineKeyboardButton(text="Всё время", callback_data="an:per:all")],
        [InlineKeyboardButton(text="📆 Свой период", callback_data="an:per:custom")],
        [InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")],
    ])


def status_filter_kb(selected: set[int] | None = None) -> InlineKeyboardMarkup:
    selected = selected or set()
    rows = [[InlineKeyboardButton(text="Все заявки", callback_data="an:st:all")]]
    row = []
    for i, st in enumerate(db.APP_STATUSES):
        mark = "✅ " if i in selected else ""
        row.append(InlineKeyboardButton(text=f"{mark}{st}", callback_data=f"an:st:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="📊 Показать", callback_data="an:apply")])
    rows.append([InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "📊 Аналитика")
async def an_start(message: Message, state: FSMContext):
    user = await db.get_user(message.from_user.id)
    if not user or user["role"] in ("pending", "banned"):
        return
    await state.clear()
    if user["role"] == "agent":
        # агент видит аналитику по себе
        await state.update_data(scope="agent", scope_id=user["tg_id"],
                                scope_name=user["full_name"] or str(user["tg_id"]))
        await message.answer("📊 Кого анализируем?", reply_markup=position_pick_kb())
        return
    if user["role"] == "leader":
        team = await db.get_team_by_leader(message.from_user.id)
        if not team:
            return await message.answer("У вас пока нет команды")
        await state.update_data(scope="team", scope_id=team["id"], scope_name=team["name"])
        await message.answer("📊 Кого анализируем?", reply_markup=position_pick_kb())
        return
    teams = await db.get_teams()
    buttons = []
    if user["role"] == "admin":
        buttons.append([InlineKeyboardButton(text="🌐 Вся сеть", callback_data="an:scope:network")])
        buttons.append([
            InlineKeyboardButton(text="🏆 Топ команд", callback_data="antop:teams"),
            InlineKeyboardButton(text="🏆 Топ соло-агентов", callback_data="antop:solo"),
        ])
    buttons += [
        [InlineKeyboardButton(text=f"👥 {t['name']}", callback_data=f"an:team:{t['id']}")]
        for t in teams
    ]
    if len(buttons) == 0:
        return await message.answer("Команд пока нет")
    buttons.append([InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")])
    await message.answer("📊 Выберите охват:",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data == "an:scope:network")
async def an_scope_network(call: CallbackQuery, state: FSMContext):
    user = await db.get_user(call.from_user.id)
    if not user or user["role"] != "admin":
        return await call.answer("Нет прав", show_alert=True)
    await state.update_data(scope="network", scope_id=0, scope_name="Вся сеть")
    await call.message.answer("📊 Кого анализируем?", reply_markup=position_pick_kb())
    await call.answer()


def position_pick_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💃 Модели", callback_data="an:pos:model"),
         InlineKeyboardButton(text="👨 Операторы", callback_data="an:pos:oper")],
        [InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")],
    ])


@router.callback_query(F.data.startswith("an:team:"))
async def an_team_pick(call: CallbackQuery, state: FSMContext):
    team = await db.get_team(int(call.data.split(":")[2]))
    if not team:
        return await call.answer("Команда не найдена", show_alert=True)
    await state.update_data(scope="team", scope_id=team["id"], scope_name=team["name"])
    await call.message.answer("📊 Кого анализируем?", reply_markup=position_pick_kb())
    await call.answer()


@router.callback_query(F.data.startswith("an:pos:"))
async def an_position_pick(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("scope"):
        return await call.answer("Начните заново: 📊 Аналитика", show_alert=True)
    position = "Модель" if call.data.endswith("model") else "Оператор"
    await state.update_data(position=position)
    await call.message.answer("📆 Выберите период:", reply_markup=period_kb())
    await call.answer()


@router.callback_query(F.data.startswith("an:per:"))
async def an_period(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("scope"):
        return await call.answer("Начните заново: 📊 Аналитика", show_alert=True)
    kind = call.data.split(":")[2]
    if kind == "custom":
        today = datetime.utcnow().date()
        await call.message.answer(
            "📆 Выберите <b>начало</b> периода:",
            parse_mode="HTML",
            reply_markup=an_calendar_kb(today.year, today.month, "f"),
        )
        return await call.answer()
    today = datetime.utcnow().date()
    if kind == "all":
        dfrom, dto, label = "1970-01-01", "2100-01-01", "всё время"
    else:
        days = int(kind)
        dfrom = (today - timedelta(days=days - 1)).isoformat()
        dto = (today + timedelta(days=1)).isoformat()
        label = "сегодня" if days == 1 else f"последние {days} дней"
    await state.update_data(dfrom=dfrom, dto=dto, period_label=label)
    await call.message.answer(
        "🎛 Выберите статусы (можно несколько), затем нажмите «📊 Показать»:",
        reply_markup=status_filter_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "acal:noop")
async def acal_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("acal:nav:"))
async def acal_nav(call: CallbackQuery):
    _, _, stage, year, month = call.data.split(":")
    await call.message.edit_reply_markup(
        reply_markup=an_calendar_kb(int(year), int(month), stage))
    await call.answer()


@router.callback_query(F.data.startswith("acal:day:"))
async def acal_day(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("scope"):
        return await call.answer("Начните заново: 📊 Аналитика", show_alert=True)
    _, _, stage, year, month, day = call.data.split(":")
    picked = datetime(int(year), int(month), int(day)).date()
    if stage == "f":
        await state.update_data(custom_from=picked.isoformat())
        await call.message.edit_text(
            f"📆 Начало периода: <b>{picked.strftime('%d.%m.%Y')}</b>", parse_mode="HTML")
        await call.message.answer(
            "📆 Теперь выберите <b>конец</b> периода:",
            parse_mode="HTML",
            reply_markup=an_calendar_kb(picked.year, picked.month, "t"),
        )
    else:
        d_from_str = data.get("custom_from")
        if not d_from_str:
            return await call.answer("Сначала выберите начало периода", show_alert=True)
        d_from = datetime.fromisoformat(d_from_str).date()
        if picked < d_from:
            return await call.answer(
                "❌ Конец периода раньше начала. Выберите другую дату", show_alert=True)
        await state.update_data(
            dfrom=d_from.isoformat(),
            dto=(picked + timedelta(days=1)).isoformat(),
            period_label=f"{d_from.strftime('%d.%m.%Y')} - {picked.strftime('%d.%m.%Y')}",
        )
        await call.message.edit_text(
            f"📆 Конец периода: <b>{picked.strftime('%d.%m.%Y')}</b>", parse_mode="HTML")
        await call.message.answer(
            "🎛 Выберите статусы (можно несколько), затем нажмите «📊 Показать»:",
            reply_markup=status_filter_kb(),
        )
    await call.answer()


@router.callback_query(F.data.startswith("an:st:"))
async def an_status_toggle(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("scope") or not data.get("dfrom"):
        return await call.answer("Начните заново: 📊 Аналитика", show_alert=True)
    pick = call.data.split(":")[2]
    if pick == "all":
        # «Все заявки» - сразу показываем отчёт без фильтра
        return await _an_render(call, state, selected=None)
    selected = set(data.get("selected", []))
    idx = int(pick)
    if idx in selected:
        selected.discard(idx)
    else:
        selected.add(idx)
    await state.update_data(selected=list(selected))
    await call.message.edit_reply_markup(reply_markup=status_filter_kb(selected))
    await call.answer()


@router.callback_query(F.data == "an:apply")
async def an_apply(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("scope") or not data.get("dfrom"):
        return await call.answer("Начните заново: 📊 Аналитика", show_alert=True)
    selected = set(data.get("selected", []))
    if not selected:
        return await call.answer("Выберите хотя бы один статус или «Все заявки»", show_alert=True)
    await _an_render(call, state, selected=selected)


SEP = "━━━━━━━━━━━━━━━"

STATUS_EMOJI = {
    "Отказ в назначении": "❌",
    "Отказ со стороны кандидата": "🙅‍♀️",
    "Отказ в работе": "⛔",
    "Перенос": "🔁",
    "Регистрация": "📝",
    "Не подтверждена": "⏳",
    "Назначено": "📌",
    "Слив": "🚫",
    "Активна": "✅",
}


def _bar(count: int, total: int) -> str:
    if total <= 0:
        return "▱" * 10 + " 0%"
    pct = round(count / total * 100)
    filled = round(count / total * 10)
    return "▰" * filled + "▱" * (10 - filled) + f" {pct}%"


async def _an_render(call: CallbackQuery, state: FSMContext, selected: set[int] | None):
    data = await state.get_data()
    position = data.get("position")
    pos_emoji = "💃" if position == "Модель" else "👨"
    pos_word = "Модели" if position == "Модель" else "Операторы"

    if data["scope"] == "agent":
        breakdown = await db.analytics_interviews(
            data["dfrom"], data["dto"], tg_id=data["scope_id"], position=position)
        title = "МОЯ АНАЛИТИКА"
    elif data["scope"] == "network":
        breakdown = await db.analytics_interviews(
            data["dfrom"], data["dto"], position=position)
        title = "🌐 АНАЛИТИКА ВСЕЙ СЕТИ"
    else:
        breakdown = await db.analytics_interviews(
            data["dfrom"], data["dto"], team_id=data["scope_id"], position=position)
        title = f"АНАЛИТИКА · «{data['scope_name']}»"

    total = sum(breakdown.values())

    head = (f"📊 <b>{title}</b>\n"
            f"{SEP}\n"
            f"{pos_emoji} Раздел: <b>{pos_word}</b>\n"
            f"📆 Период: <b>{data['period_label']}</b>\n"
            f"{SEP}")

    if total == 0:
        body = "\n\n🤷 За выбранный период заявок нет"
    else:
        if selected is None:
            shown = [st for st in db.APP_STATUSES if st in breakdown]
            summary = f"\n\n📝 Всего заявок: <b>{total}</b>\n"
        else:
            shown = [db.APP_STATUSES[i] for i in sorted(selected)]
            count = sum(breakdown.get(st, 0) for st in shown)
            summary = (f"\n\n🎯 По выбранным статусам: <b>{count}</b>\n"
                       f"📝 Всего за период: <b>{total}</b>\n")
        lines = []
        for st in shown:
            c = breakdown.get(st, 0)
            emoji = STATUS_EMOJI.get(st, "▪️")
            lines.append(f"{emoji} {st} - <b>{c}</b>\n{_bar(c, total)}")
        body = summary + "\n" + "\n\n".join(lines)

    await state.clear()
    await call.message.answer(f"{head}{body}", parse_mode="HTML")
    await call.answer()


# ---------- Топы в аналитике: команды и соло-агенты (только админ) ----------

from datetime import timedelta as _an_td

def _top_pct(prev: int, cur: int) -> int:
    return round(cur / prev * 100) if prev else 0


def _top_funnel(rec: int, reg: int, s1: int, s2: int) -> str:
    return (f"Записи: <b>{rec}</b>\n"
            f"├ Регистрации: <b>{reg}</b> ({_top_pct(rec, reg)}%)\n"
            f"├ Первая смена: <b>{s1}</b> ({_top_pct(reg, s1)}%)\n"
            f"└ Вторая смена: <b>{s2}</b> ({_top_pct(s1, s2)}%)")


async def _is_adm(tg_id: int) -> bool:
    user = await db.get_user(tg_id)
    return bool(user and user["role"] == "admin")


@router.callback_query(F.data.startswith("antop:"))
async def antop_kind(call: CallbackQuery):
    if not await _is_adm(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    kind = call.data.split(":")[1]
    await call.message.answer(
        "📆 За какой период?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Сегодня", callback_data=f"antp:{kind}:1"),
             InlineKeyboardButton(text="7 дней", callback_data=f"antp:{kind}:7")],
            [InlineKeyboardButton(text="30 дней", callback_data=f"antp:{kind}:30"),
             InlineKeyboardButton(text="Всё время", callback_data=f"antp:{kind}:all")],
            [InlineKeyboardButton(text="📆 Свой период", callback_data=f"antpc:{kind}")],
            [InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("antp:"))
async def antop_render(call: CallbackQuery):
    if not await _is_adm(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    _, kind, per = call.data.split(":")
    today = datetime.utcnow().date()
    if per == "all":
        dfrom, dto, label = "1970-01-01", "2100-01-01", "всё время"
    else:
        days = int(per)
        dfrom = (today - _an_td(days=days - 1)).isoformat()
        dto = (today + _an_td(days=1)).isoformat()
        label = "сегодня" if days == 1 else f"последние {days} дней"
    await _render_top(call, kind, dfrom, dto, label)


async def _render_top(call: CallbackQuery, kind: str, dfrom: str, dto: str, label: str):
    crit = "rec"  # сортировка по записям
    items = []
    if kind == "solo":
        rows = await db.top_agents(dfrom, dto, limit=100, solo_only=True)
        shifts = await db.shifts_funnel_by_agent(dfrom, dto)
        title = "🏆 <b>ТОП СОЛО-АГЕНТОВ</b>"
        for r in rows:
            s1, s2 = shifts.get(r["tg_id"], (0, 0))
            agent_code = db.get_agent_code(r)
            items.append({
                "name": f"{r['full_name'] or '-'} (ID: <code>{agent_code}</code>)",
                "rec": r["records"], "reg": r["regs"] or 0,
                "s1": s1 or 0, "s2": s2 or 0,
            })
    else:
        rows = await db.top_teams(dfrom, dto, limit=100)
        shifts = await db.shifts_funnel_by_team(dfrom, dto)
        title = "🏆 <b>ТОП КОМАНД</b>"
        for r in rows:
            if r["team_id"] is None:
                continue  # соло в топ команд не входят - у них свой топ
            s1, s2 = shifts.get(r["team_id"], (0, 0))
            items.append({
                "name": r["team_name"],
                "rec": r["records"], "reg": r["regs"] or 0,
                "s1": s1 or 0, "s2": s2 or 0,
            })

    items.sort(key=lambda x: x[crit], reverse=True)
    items = items[:10]

    if not items:
        body = "🤷 За выбранный период данных нет"
    else:
        body = "\n\n".join(
            f"<b>{i}. {it['name']}</b>\n{_top_funnel(it['rec'], it['reg'], it['s1'], it['s2'])}"
            for i, it in enumerate(items, 1)
        )
    await call.message.answer(
        f"{title}\n{SEP}\n"
        f"📆 Период: <b>{label}</b>\n"
        f"{SEP}\n\n{body}",
        parse_mode="HTML",
    )
    await call.answer()


# --- календарь свободного периода для топов ---

def tcal_kb(year: int, month: int, stage: str, tail: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{AN_MONTHS[month - 1]} {year}", callback_data="acal:noop")]]
    rows.append([InlineKeyboardButton(text=d, callback_data="acal:noop") for d in AN_WEEKDAYS])
    for week in _an_calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if not day:
                row.append(InlineKeyboardButton(text=" ", callback_data="acal:noop"))
            else:
                row.append(InlineKeyboardButton(
                    text=str(day),
                    callback_data=f"tcal:day:{stage}:{tail}:{year}:{month}:{day}"))
        rows.append(row)
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    rows.append([
        InlineKeyboardButton(text="<", callback_data=f"tcal:nav:{stage}:{tail}:{prev_y}:{prev_m}"),
        InlineKeyboardButton(text="Отмена", callback_data="icancel"),
        InlineKeyboardButton(text=">", callback_data=f"tcal:nav:{stage}:{tail}:{next_y}:{next_m}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("antpc:"))
async def antop_custom(call: CallbackQuery):
    if not await _is_adm(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    kind = call.data.split(":")[1]
    today = datetime.utcnow().date()
    await call.message.answer(
        "📆 Выберите <b>начало</b> периода:",
        parse_mode="HTML",
        reply_markup=tcal_kb(today.year, today.month, "f", kind),
    )
    await call.answer()


@router.callback_query(F.data.startswith("tcal:nav:"))
async def tcal_nav(call: CallbackQuery):
    parts = call.data.split(":")
    stage = parts[2]
    if stage == "f":
        kind, year, month = parts[3], int(parts[4]), int(parts[5])
        tail = kind
    else:
        kind, fromc, year, month = parts[3], parts[4], int(parts[5]), int(parts[6])
        tail = f"{kind}:{fromc}"
    await call.message.edit_reply_markup(reply_markup=tcal_kb(year, month, stage, tail))
    await call.answer()


@router.callback_query(F.data.startswith("tcal:day:"))
async def tcal_day(call: CallbackQuery):
    if not await _is_adm(call.from_user.id):
        return await call.answer("Нет прав", show_alert=True)
    parts = call.data.split(":")
    stage = parts[2]
    if stage == "f":
        kind, year, month, day = parts[3], int(parts[4]), int(parts[5]), int(parts[6])
        d_from = datetime(year, month, day).date()
        await call.message.edit_text(
            f"📆 Начало периода: <b>{d_from.strftime('%d.%m.%Y')}</b>", parse_mode="HTML")
        await call.message.answer(
            "📆 Теперь выберите <b>конец</b> периода:",
            parse_mode="HTML",
            reply_markup=tcal_kb(d_from.year, d_from.month, "t",
                                 f"{kind}:{d_from.strftime('%Y%m%d')}"),
        )
    else:
        kind, fromc = parts[3], parts[4]
        year, month, day = int(parts[5]), int(parts[6]), int(parts[7])
        d_from = datetime.strptime(fromc, "%Y%m%d").date()
        d_to = datetime(year, month, day).date()
        if d_to < d_from:
            return await call.answer("❌ Конец периода раньше начала. Выберите другую дату",
                                     show_alert=True)
        await call.message.edit_text(
            f"📆 Конец периода: <b>{d_to.strftime('%d.%m.%Y')}</b>", parse_mode="HTML")
        label = f"{d_from.strftime('%d.%m.%Y')} - {d_to.strftime('%d.%m.%Y')}"
        await _render_top(call, kind, d_from.isoformat(),
                          (d_to + _an_td(days=1)).isoformat(), label)
    await call.answer()


# ---------- Раздел «Модели»: поиск моделей + аналитика ----------

class MSearch(StatesGroup):
    query = State()


@router.message(F.text == "💵 Модели")
async def models_section(message: Message, state: FSMContext):
    user = await db.get_user(message.from_user.id)
    if not user or user["role"] not in ("leader", "mentor", "admin"):
        return
    await state.clear()
    rows = [[InlineKeyboardButton(text="💵 Мои модели", callback_data="mymodels")]]
    if user["role"] == "leader":
        rows.append([InlineKeyboardButton(text="👥 Модели команды", callback_data="teammodels")])
    rows += [
        [InlineKeyboardButton(text="🔍 Поиск модели", callback_data="msearch")],
        [InlineKeyboardButton(text="📊 Аналитика", callback_data="an:open")],
        [InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")],
    ]
    await message.answer(
        "💵 Раздел «Модели»:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "mymodels")
async def my_models_open(call: CallbackQuery):
    user = await db.get_user(call.from_user.id)
    if not user or user["role"] in ("pending", "banned"):
        return await call.answer("Нет доступа", show_alert=True)
    await call.message.answer("💵 Выбери статус моделей:", reply_markup=kb.models_status_kb)
    await call.answer()


@router.callback_query(F.data == "an:open")
async def an_open(call: CallbackQuery, state: FSMContext):
    """Запуск аналитики из раздела «Модели»"""
    user = await db.get_user(call.from_user.id)
    if not user or user["role"] in ("pending", "banned"):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    if user["role"] == "agent":
        await state.update_data(scope="agent", scope_id=user["tg_id"],
                                scope_name=user["full_name"] or str(user["tg_id"]))
        await call.message.answer("📊 Кого анализируем?", reply_markup=position_pick_kb())
        return await call.answer()
    if user["role"] == "leader":
        team = await db.get_team_by_leader(call.from_user.id)
        if not team:
            await call.answer()
            return await call.message.answer("У вас пока нет команды")
        await state.update_data(scope="team", scope_id=team["id"], scope_name=team["name"])
        await call.message.answer("📊 Кого анализируем?", reply_markup=position_pick_kb())
        return await call.answer()
    # наставник/админ - выбор команды/сети как обычно
    teams = await db.get_teams()
    buttons = []
    if user["role"] == "admin":
        buttons.append([InlineKeyboardButton(text="🌐 Вся сеть", callback_data="an:scope:network")])
        buttons.append([
            InlineKeyboardButton(text="🏆 Топ команд", callback_data="antop:teams"),
            InlineKeyboardButton(text="🏆 Топ соло-агентов", callback_data="antop:solo"),
        ])
    buttons += [
        [InlineKeyboardButton(text=f"👥 {t['name']}", callback_data=f"an:team:{t['id']}")]
        for t in teams
    ]
    if not buttons:
        await call.answer()
        return await call.message.answer("Команд пока нет")
    buttons.append([InlineKeyboardButton(text="🔴 Отмена", callback_data="icancel")])
    await call.message.answer("📊 Выберите охват:",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@router.callback_query(F.data == "msearch")
async def model_search_start(call: CallbackQuery, state: FSMContext):
    user = await db.get_user(call.from_user.id)
    if not user or user["role"] not in ("leader", "mentor", "admin"):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(MSearch.query)
    await call.message.answer(
        "🔍 Отправьте спец-номер модели, ФИО или номер телефона:",
        reply_markup=kb.cancel_kb,
    )
    await call.answer()


@router.message(MSearch.query)
async def model_search_run(message: Message, state: FSMContext):
    viewer = await db.get_user(message.from_user.id)
    # охват: агент - свои, лидер - команда, наставник/админ - все
    tg_scope, team_scope = None, None
    if viewer["role"] == "agent":
        tg_scope = viewer["tg_id"]
    elif viewer["role"] == "leader":
        team = await db.get_team_by_leader(viewer["tg_id"])
        if team:
            team_scope = team["id"]
        else:
            tg_scope = viewer["tg_id"]
    models = await db.search_models(message.text or "", tg_id=tg_scope, team_id=team_scope)
    if not models:
        return await message.answer(
            "❌ Модель не найдена. Проверьте спец-номер, ФИО или телефон и попробуйте ещё раз",
            reply_markup=kb.cancel_kb,
        )
    await state.clear()
    status_titles = {"active": "Активные ✅", "dropped": "Слив 🚫"}
    for m in models:
        team = await db.get_team(m["owner_team"]) if m["owner_team"] else None
        binding = f"👥 Команда «{team['name']}»" if team else "🧍 Соло"
        own_hint = f"\nУправление: /model_{m['model_code']}" if m["owner_tg"] == viewer["tg_id"] else ""
        await message.answer(
            f"💵 <b>МОДЕЛЬ #{m['model_code']}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Имя: <b>{m['name']}</b>\n"
            f"📞 Телефон: {m['phone'] or '-'}\n"
            f"🟢 Telegram: {'@' + m['username'] if m['username'] else '-'}\n"
            f"Статус: <b>{status_titles.get(m['status'], m['status'])}</b>\n"
            f"Смен: <b>{m['shifts']}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Агент: {m['owner_name'] or '-'} (ID: {m.get('owner_no', '-')})\n"
            f"Привязка: {binding}"
            f"{own_hint}",
            parse_mode="HTML",
        )


# ---------- Модели команды (лидер) ----------

@router.callback_query(F.data == "teammodels")
async def team_models_pick(call: CallbackQuery):
    team = await db.get_team_by_leader(call.from_user.id)
    if not team:
        return await call.answer("У вас нет команды", show_alert=True)
    await call.message.answer(
        f"👥 Модели команды «{team['name']}» - выберите статус:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Активные ✅", callback_data="tmodels:active"),
             InlineKeyboardButton(text="Слив 🚫", callback_data="tmodels:dropped")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("tmodels:"))
async def team_models_list(call: CallbackQuery):
    team = await db.get_team_by_leader(call.from_user.id)
    if not team:
        return await call.answer("У вас нет команды", show_alert=True)
    status = call.data.split(":")[1]
    models = [m for m in await db.team_models(team["id"]) if m["status"] == status]
    title = "Активные ✅" if status == "active" else "Слив 🚫"
    if not models:
        await call.message.answer(f"👥 «{team['name']}» · {title}: список пуст")
        return await call.answer()
    lines = [
        f"{i}. <b>{m['name']}</b>"
        f" · ID <code>{m['model_code']}</code>"
        f"{' ' + m['phone'] if m['phone'] else ''}"
        f"{' @' + m['username'] if m['username'] else ''}"
        f" - {m['shifts']} смен(-ы)\n"
        f"   агент: {m['owner_name'] or '-'} (№{m['owner_no']})"
        for i, m in enumerate(models, 1)
    ]
    text = (f"👥 <b>Модели команды «{team['name']}»</b> · {title}\n"
            f"━━━━━━━━━━━━━━━\n" + "\n".join(lines))
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()
