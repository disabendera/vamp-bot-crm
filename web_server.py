import logging
from aiohttp import web
from aiogram import Bot

import db
from config import WEBHOOK_PORT, WEBHOOK_SECRET

logger = logging.getLogger(__name__)

SEP = "━━━━━━━━━━━━━━━"


async def handle_sheet_status(request: web.Request) -> web.Response:
    bot: Bot = request.app["bot"]
    try:
        data = await request.json()
        logger.info(f"📥 Входящий вебхук от Google Таблицы: {data}")
    except Exception as e:
        logger.error(f" Ошибка парсинга JSON вебхука: {e}")
        return web.json_response({"error": "Invalid JSON"}, status=400)


    secret = data.get("secret") or request.headers.get("X-Webhook-Secret")
    if secret != WEBHOOK_SECRET:
        return web.json_response({"error": "Unauthorized"}, status=401)

    model_code = str(data.get("model_code") or "").strip()
    model_name = data.get("model_name", "").strip()
    new_status = data.get("new_status") or data.get("status")
    sheet_name = str(data.get("sheet_name") or "").strip().lower()
    col_title = str(data.get("col_title") or "").strip().lower()
    is_model_name_edit = (
        data.get("action") == "model_name_changed"
        or ("запуск" in sheet_name and ("фио" in col_title or "имя" in col_title))
    )

    # Сохраняем изменение в историю таблицы
    try:
        await db.add_sheet_history_entry(
            model_code=model_code,
            model_name=model_name,
            sheet_name=str(data.get("sheet_name") or ""),
            col_title=str(data.get("col_title") or ""),
            old_value=str(data.get("old_value") or ""),
            new_value=str(data.get("new_value") or new_status or ""),
            user_email=str(data.get("user_email") or "")
        )
    except Exception as hist_err:
        logger.error(f"Ошибка логирования истории изменений: {hist_err}")

    if is_model_name_edit:
        if not model_code or not model_name:
            return web.json_response({"error": "Model code and name are required"}, status=400)
        model = await db.update_model_name(model_code, model_name)
        if not model:
            return web.json_response({"error": "Model not found"}, status=404)
        return web.json_response({"ok": True, "type": "model_name_changed", "model_code": model["model_code"]})

    # Логовые события из основной таблицы не должны запускать уведомления и смену статуса.
    if data.get("log_only") is True:
        return web.json_response({"ok": True, "type": "history_only"})

    is_report_column = data.get("is_report_column") is True or "отчетник:" in str(new_status).lower()

    # Если отредактирован столбец Отчетник (ссылка или название отчётника)
    if is_report_column:
        import re
        url_match = re.search(r"https?://docs\.google\.com/spreadsheets/d/[^\s\"']+", str(new_status))
        if url_match:
            if model_code:
                report_url = url_match.group(0)
                await db.update_model_report_sheet(model_code, report_url)
                logger.info(f"Привязана ссылка на отчётник модели {model_code}: {report_url}")
            else:
                logger.warning("Ссылка на отчётник получена без model_code, привязка пропущена")
        
        # Служебное изменение отчётника: НЕ слать спам агенту в Telegram
        return web.json_response({"ok": True, "type": "report_sheet_update"})

    sheet_name = str(data.get("sheet_name") or "").strip().lower()
    status_lower = str(new_status).lower()

    # 🛑 Вторая страница ("Запуски"): агенту отправляем ТОЛЬКО обновления статусов/отчётника.
    # Служебные поля (Была ли смена, Причины и т.д.) остаются только для просмотра в таблице.
    if "запуск" in sheet_name:
        is_status_change = any(kw in status_lower for kw in ("статус", "подтвержд", "регистрац", "слив", "отмен", "принят", "отклон", "отказ", "причин", "собес"))
        if not is_status_change:
            logger.info(f"Игнорируем уведомление агенту со страницы 'Запуски' для поля: {new_status}")
            return web.json_response({"ok": True, "skipped": True})

    if not model_code:
        return web.json_response({"error": "Model code is required"}, status=400)

    model = await db.update_model_app_status(model_code, str(new_status))
    if not model:
        return web.json_response({"error": "Model not found"}, status=404)

    model_dict = dict(model)
    agent_tg_id = model_dict["owner_tg_id"]
    partner_name = model_dict.get("partner") or "-"
    real_id = model_dict["model_code"]

    # Кнопка подтверждения отправляется ТОЛЬКО при действительно принятом статусе.
    is_accepted = db.is_accepted_app_status(new_status)

    status_reason = str(data.get("status_reason") or "").strip()

    msg_text = (
        f"🟢 <b>СТАТУС МОДЕЛИ {real_id}</b>\n"
        f"{SEP}\n"
        f"✅ Новый статус: <b>{new_status}</b>"
    )
    if status_reason:
        msg_text += f"\n✳️ Причина: {status_reason}"

    try:
        if is_accepted:
            sobes_date = model_dict.get("sobes_date") or ""
            sobes_time = model_dict.get("sobes_time") or ""
            text = model_dict.get("application_text") or ""
            
            # Кнопки подтверждения приходят СРАЗУ после приёма партнёром.
            # За 6 часов до собеса reminder_service напомнит, если агент ещё не ответил.
            if True:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Модель придёт", callback_data=f"agent_confirm:{real_id}"),
                    InlineKeyboardButton(text="🚫 Не придёт", callback_data=f"agent_reject:{real_id}"),
                ]])
                from services.shift_tracker import extract_model_name_from_text
                model_nm = extract_model_name_from_text(text)
                confirm_text = (
                    f"🟢 <b>ПАРТНЁР ПРИНЯЛ МОДЕЛЬ {real_id}</b>\n"
                    f"{SEP}\n"
                    + (f"💚 Модель: <b>{model_nm}</b>\n" if model_nm else "")
                    + f"📗 Собеседование: <b>{sobes_date or '-'} в {sobes_time or '-'} МСК</b>\n"
                    f"{SEP}\n"
                    f"Свяжитесь с моделью и подтвердите, что она <b>придёт на собеседование</b> "
                    f"в назначенное время. Если модель не выходит на связь или отказалась - нажмите «Не придёт»"
                )
                await bot.send_message(agent_tg_id, confirm_text, parse_mode="HTML", reply_markup=confirm_kb)
                await db.mark_confirm_sent(real_id)
        else:
            await bot.send_message(agent_tg_id, msg_text, parse_mode="HTML")
            
        logger.info(f"Агенту {agent_tg_id} отправлено уведомление об изменении статуса модели {real_id}")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление агенту {agent_tg_id}: {e}")

    # Отправка анкеты в соответствующий топик партнёра (без кнопок!)
    if partner_name and partner_name != "-":
        try:
            partner_obj = await db.get_partner_by_name(partner_name)
            if partner_obj:
                p_dict = dict(partner_obj)
                if p_dict.get("chat_id"):
                    target_topic = None
                    topic_header = None

                    if "собес" in status_lower and "не прошла" not in status_lower and "не прошёл" not in status_lower:
                        target_topic = p_dict.get("topic_sobes")
                        topic_header = f"✅ <b>ID МОДЕЛИ {real_id} · ПРОШЛА СОБЕС</b>"
                    elif "регистрац" in status_lower:
                        target_topic = p_dict.get("topic_registration")
                        topic_header = f"📗 <b>ID МОДЕЛИ {real_id} · РЕГИСТРАЦИЯ</b>"
                    elif "подтвержд" in status_lower:
                        target_topic = p_dict.get("topic_confirmations")
                        topic_header = f"🟢 <b>ID МОДЕЛИ {real_id} · НА СОБЕСЕДОВАНИЕ</b>"
                    elif any(word in status_lower for word in ("слив", "отмен", "отклон", "не принят", "отказ")):
                        target_topic = p_dict.get("topic_cancelled")
                        topic_header = f"🔴 <b>ID МОДЕЛИ {real_id} · СЛИВ / ОТМЕНА</b>"
                        if status_reason:
                            topic_header += f"\n✳️ Причина: {status_reason}"

                    if topic_header:
                        p_msg_text = await db.format_anketa_topic_message(model_dict, topic_header)
                        p_kwargs = {"message_thread_id": target_topic} if target_topic else {}
                        await bot.send_message(p_dict["chat_id"], p_msg_text, parse_mode="HTML", **p_kwargs)
        except Exception as err:
            logger.error(f"Не удалось отправить анкету в топик партнёра: {err}")

    return web.json_response({"ok": True, "model_code": real_id, "new_status": new_status})


async def start_web_server(bot: Bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/webhook/sheet_status", handle_sheet_status)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"🌐 HTTP Webhook сервер запущен на порту {WEBHOOK_PORT} (/webhook/sheet_status)")
