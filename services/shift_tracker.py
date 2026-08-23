import asyncio
import csv
import io
import logging
import os
import re
import aiohttp
import gspread
from aiogram import Bot

import db

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_FILE = "service_account.json"
_gspread_client = None


def find_service_account_file() -> str | None:
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        return SERVICE_ACCOUNT_FILE
    # Ищем любой .json файл с ключом сервисного аккаунта в папке проекта
    for file in os.listdir("."):
        if file.endswith(".json") and ("gserviceaccount" in file or "service_account" in file or "key" in file or file.startswith("crm-bot")):
            return file
    return None


def get_gspread_client():
    global _gspread_client
    if _gspread_client is None:
        key_file = find_service_account_file()
        if key_file:
            try:
                _gspread_client = gspread.service_account(filename=key_file)
                logger.info(f"🔑 Авторизация gspread через {key_file} прошла успешно!")
            except Exception as e:
                logger.error(f" Ошибка авторизации gspread через {key_file}: {e}")
        else:
            logger.warning("⚠️ Файл ключа service_account.json не найден в папке бота!")
    return _gspread_client



def extract_sheet_id(url: str) -> str | None:
    if not url:
        return None
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    return match.group(1) if match else None


def extract_model_name_from_text(text: str) -> str:
    if not text:
        return "Модель"
    lines = text.split("\n")
    for line in lines:
        l = re.sub(r"^\d+[).]?\s*", "", line).strip()
        if len(l) > 1 and "позиция" not in l.lower() and "собес" not in l.lower():
            return l
    return lines[0] or "Модель"


async def fetch_sheet_rows(interview: dict, session: aiohttp.ClientSession) -> list[list[str]] | None:
    report_url = interview["report_sheet_url"]
    sheet_id = extract_sheet_id(report_url)
    if not sheet_id:
        return None

    gc = get_gspread_client()
    if gc:
        try:
            # Читаем через авторизованный Service Account (без ошибок 401!)
            sh = gc.open_by_key(sheet_id)
            worksheet = sh.sheet1
            rows = worksheet.get_all_values()
            return rows
        except Exception as e:
            logger.warning(f"gspread не смог открыть отчётник ID {sheet_id} ({e}), пробуем прямой HTTP экспорт...")

    # Fallback через прямой HTTP экспорт
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        async with session.get(csv_url, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                logger.warning(f"Не удалось получить CSV отчётника (ID: {interview['id']}, status: {resp.status}, URL: {csv_url})")
                return None
            content = await resp.text()
            reader = csv.reader(io.StringIO(content))
            return list(reader)
    except Exception as e:
        logger.error(f"Ошибка HTTP запроса отчётника {sheet_id}: {e}")
        return None


async def check_single_report_sheet(bot: Bot, session: aiohttp.ClientSession, interview: dict):
    rows = await fetch_sheet_rows(interview, session)
    if not rows:
        return

    model_name = extract_model_name_from_text(interview["text"])
    from datetime import datetime
    now = datetime.now()

    for row in rows:
        if not row or len(row) < 1:
            continue
        
        # 1. Извлекаем дату или число месяца (например "12" или "12.08.2026") из первой ячейки
        first_cell = str(row[0]).strip()
        shift_date = None
        
        date_match = re.search(r"\b(\d{1,2}\.\d{1,2}\.\d{2,4})\b", first_cell)
        if date_match:
            shift_date = date_match.group(1)
        elif first_cell.isdigit():
            day_num = int(first_cell)
            if 1 <= day_num <= 31:
                shift_date = f"{day_num:02d}.{now.month:02d}.{now.year}"
        
        if not shift_date:
            continue

        # 2. Проверяем, есть ли в этой строке зафиксированные данные (ненулевые числа в столбцах 2, 3, 4 и т.д.)
        has_shift_activity = False
        for col_val in row[1:]:
            clean_val = re.sub(r"[^\d.,]", "", str(col_val)).replace(",", ".")
            try:
                val_float = float(clean_val)
                if val_float > 0:
                    has_shift_activity = True
                    break
            except ValueError:
                continue

        if has_shift_activity:
            payout = await db.register_shift_payout(interview["id"], shift_date, amount=12.0)
            if payout:
                agent_tg_id = payout["agent_tg_id"]
                new_balance = payout["new_balance"]
                msg_text = (
                    f"💰 <b>Начисление за смену модели!</b>\n\n"
                    f"👤 Модель: <b>{model_name}</b>\n"
                    f"📅 Дата смены: <b>{shift_date}</b>\n"
                    f"💵 Начислено: <b>+$12.00</b> к балансу!\n"
                    f"💳 Ваш баланс: <b>${new_balance:.2f}</b>"
                )
                try:
                    await bot.send_message(agent_tg_id, msg_text, parse_mode="HTML")
                    logger.info(f"Агенту {agent_tg_id} отправлена фикса $12 за смену модели {model_name} ({shift_date})")
                except Exception as err:
                    logger.error(f"Не удалось выслать уведомление агенту {agent_tg_id}: {err}")


async def start_shift_tracker(bot: Bot, interval_seconds: int = 180):
    logger.info(f"🔄 Сервис отслеживания смен моделей запущен (интервал: {interval_seconds} сек)")
    await asyncio.sleep(10)
    while True:
        try:
            interviews = await db.get_interviews_with_report_sheets()
            if interviews:
                async with aiohttp.ClientSession() as session:
                    tasks = [check_single_report_sheet(bot, session, i) for i in interviews]
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.exception(f"Ошибка в цикле shift_tracker: {e}")
        
        await asyncio.sleep(interval_seconds)
