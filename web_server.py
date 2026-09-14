import logging
from aiohttp import web
from aiogram import Bot

import db
from config import WEBHOOK_PORT, WEBHOOK_SECRET

logger = logging.getLogger(__name__)


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

    interview_id = data.get("interview_id")
    model_name = data.get("model_name", "").strip()
    new_status = data.get("new_status") or data.get("status")

    # Сохраняем изменение в историю таблицы
    try:
        await db.add_sheet_history_entry(
            interview_id=str(interview_id or ""),
            model_name=model_name,
            sheet_name=str(data.get("sheet_name") or ""),
            col_title=str(data.get("col_title") or ""),
            old_value=str(data.get("old_value") or ""),
            new_value=str(data.get("new_value") or new_status or ""),
            user_email=str(data.get("user_email") or "")
        )
    except Exception as hist_err:
        logger.error(f"Ошибка логирования истории изменений: {hist_err}")

    # Логовые события из основной таблицы не должны запускать уведомления и смену статуса.
    if data.get("log_only") is True:
        return web.json_response({"ok": True, "type": "history_only"})

    is_report_column = data.get("is_report_column") is True or "отчетник:" in str(new_status).lower()

    # Если отредактирован столбец Отчетник (ссылка или название отчётника)
    if is_report_column:
        import re
        url_match = re.search(r"https?://docs\.google\.com/spreadsheets/d/[^\s\"']+", str(new_status))
        if url_match:
            if interview_id:
                report_url = url_match.group(0)
                await db.update_interview_report_sheet(interview_id, report_url)
                logger.info(f"Привязана ссылка на отчётник заявки №{interview_id}: {report_url}")
            else:
                logger.warning("Ссылка на отчётник получена без interview_id, привязка пропущена")
        
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

    interview = None

    if interview_id:
        try:
            interview = await db.update_interview_app_status(int(interview_id), str(new_status))
        except ValueError:
            pass

    if not interview:
        return web.json_response({"error": "Interview not found"}, status=404)

    interview_dict = dict(interview)
    agent_tg_id = interview_dict["tg_id"]
    partner_name = interview_dict.get("partner") or "-"
    real_id = interview_dict["id"]

    # Кнопка подтверждения отправляется ТОЛЬКО при статусе "Принято" (и НЕ "Не принято")
    is_accepted = ("принят" in status_lower or "принято" in status_lower) and "не принят" not in status_lower and "не принято" not in status_lower

    status_reason = str(data.get("status_reason") or "").strip()

    msg_text = (
        f"🔔 <b>Обновление статуса заявки!</b>\n\n"
        f"📋 <b>Заявка №{real_id}</b>\n"
        f"📌 Новый статус: <b>{new_status}</b>"
    )
    if status_reason:
        msg_text += f"\n💬 <b>Причина статуса:</b> {status_reason}"

    try:
        if is_accepted:
            from datetime import datetime, timezone, timedelta
            msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)
            sobes_date = interview_dict.get("sobes_date") or ""
            sobes_time = interview_dict.get("sobes_time") or ""
            text = interview_dict.get("text") or ""
            
            sobes_dt = db.parse_interview_datetime(sobes_date, sobes_time, text)
            
            if sobes_dt and (sobes_dt - msk_now) > timedelta(hours=6):
                # До собеса более 6 часов -> шлем инфо-сообщение без кнопок, кнопки придут за 6 часов
                info_msg = (
                    f"🤝 <b>Партнёр принял заявку №{real_id}!</b>\n\n"
                    f"📅 Собеседование: <b>{sobes_date or '-'} в {sobes_time or '-'} МСК</b>\n"
                    f"⏳ Кнопки подтверждения откроются в боте за 6 часов до собеседования."
                )
                await bot.send_message(agent_tg_id, info_msg, parse_mode="HTML")
            else:
                # До собеса менее 6 часов (или время прошло) -> слаем кнопки сразу
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"agent_confirm:{real_id}"),
                    InlineKeyboardButton(text="🚫 Отклонить", callback_data=f"agent_reject:{real_id}"),
                ]])
                await bot.send_message(agent_tg_id, msg_text, parse_mode="HTML", reply_markup=confirm_kb)
                await db.mark_confirm_sent(real_id)
        else:
            await bot.send_message(agent_tg_id, msg_text, parse_mode="HTML")
            
        logger.info(f"Агенту {agent_tg_id} отправлено уведомление об изменении статуса заявки №{real_id}")
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
                        topic_header = f"✅ <b>Заявка №{real_id} — Прошла собес</b>"
                    elif "регистрац" in status_lower:
                        target_topic = p_dict.get("topic_registration")
                        topic_header = f"🚀 <b>Заявка №{real_id} на регистрации</b>"
                    elif "подтвержд" in status_lower:
                        target_topic = p_dict.get("topic_confirmations")
                        topic_header = f"📋 <b>Новая заявка на собеседование №{real_id}</b>"
                    elif any(word in status_lower for word in ("слив", "отмен", "отклон", "не принят", "отказ")):
                        target_topic = p_dict.get("topic_cancelled")
                        topic_header = f"❌ <b>Заявка №{real_id} — Слив / Отмена</b>"
                        if status_reason:
                            topic_header += f"\n💬 <b>Причина статуса:</b> {status_reason}"

                    if topic_header:
                        p_msg_text = await db.format_anketa_topic_message(interview_dict, topic_header)
                        p_kwargs = {"message_thread_id": target_topic} if target_topic else {}
                        await bot.send_message(p_dict["chat_id"], p_msg_text, parse_mode="HTML", **p_kwargs)
        except Exception as err:
            logger.error(f"Не удалось отправить анкету в топик партнёра: {err}")

    return web.json_response({"ok": True, "interview_id": interview_id, "new_status": new_status})


async def start_web_server(bot: Bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/webhook/sheet_status", handle_sheet_status)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"🌐 HTTP Webhook сервер запущен на порту {WEBHOOK_PORT} (/webhook/sheet_status)")
