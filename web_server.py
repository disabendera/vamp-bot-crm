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

    is_report_column = data.get("is_report_column") is True or "отчетник:" in str(new_status).lower()

    # Если отредактирован столбец Отчетник (ссылка или название отчётника)
    if is_report_column:
        import re
        url_match = re.search(r"https?://docs\.google\.com/spreadsheets/d/[^\s\"']+", str(new_status))
        if url_match:
            report_url = url_match.group(0)
            target = interview_id or model_name
            await db.update_interview_report_sheet(target, report_url)
            logger.info(f"Привязана ссылка на отчётник модели ({target}): {report_url}")
        
        # Служебное изменение отчётника: НЕ слать спам агенту в Telegram
        return web.json_response({"ok": True, "type": "report_sheet_update"})



    interview = None

    if interview_id:
        try:
            interview = await db.update_interview_app_status(int(interview_id), str(new_status))
        except ValueError:
            pass

    if not interview and model_name:
        interview = await db.update_interview_app_status_by_model(model_name, str(new_status))

    if not interview:
        return web.json_response({"error": "Interview not found"}, status=404)


    agent_tg_id = interview["tg_id"]
    partner_name = interview["partner"] or "-"
    real_id = interview["id"]

    # Уведомляем агента
    msg_text = (
        f"🔔 <b>Обновление статуса заявки!</b>\n\n"
        f"📋 <b>Заявка №{real_id}</b> ({partner_name})\n"
        f"📌 Новый статус: <b>{new_status}</b>"
    )


    try:
        await bot.send_message(agent_tg_id, msg_text, parse_mode="HTML")
        logger.info(f"Агенту {agent_tg_id} отправлено уведомление об изменении статуса заявки №{interview_id}")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление агенту {agent_tg_id}: {e}")

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
