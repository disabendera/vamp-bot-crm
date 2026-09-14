import asyncio
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
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(sheet_url.strip(), json=payload, allow_redirects=True) as resp:
                text = await resp.text()
                if resp.status in (200, 201, 302):
                    logger.info(f"Ответ от Google Таблицы (заявка #{payload.get('interview_id')}): {text}")
                    return True
                else:
                    logger.error(f"Ошибка отправки в Google Таблицу ({resp.status}): {text}")
                    return False
    except asyncio.TimeoutError:
        logger.warning(f"Превышено время ожидания ответа от Google Таблицы (>45s) ({sheet_url}), но данные в таблицу могли добавиться.")
        return True
    except Exception as e:
        logger.exception(f"Исключение при отправке в Google Таблицу ({sheet_url}): {e}")
        return False

