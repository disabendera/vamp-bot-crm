import logging
import aiohttp

logger = logging.getLogger(__name__)


async def send_interview_to_sheet(sheet_url: str, payload: dict) -> bool:
    """
    Отправляет JSON с данными новой заявки в Google Apps Script конкретного партнёра.
    """
    if not sheet_url or not sheet_url.strip():
        logger.warning("sheet_url не указан, пропуск отправки в Google Таблицу")
        return False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(sheet_url.strip(), json=payload, timeout=15) as resp:
                text = await resp.text()
                if resp.status in (200, 201, 302):
                    logger.info(f"Ответ от Google Таблицы (заявка #{payload.get('interview_id')}): {text}")
                    return True
                else:
                    logger.error(f"Ошибка отправки в Google Таблицу ({resp.status}): {text}")
                    return False
    except Exception as e:
        logger.exception(f"Исключение при отправке в Google Таблицу ({sheet_url}): {e}")
        return False

