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

SEP = "━━━━━━━━━━━━━━━"

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
        l = re.sub(r"^(?:имя|фио)\s*:\s*", "", l, flags=re.IGNORECASE).strip()
        if len(l) > 1 and "позиция" not in l.lower() and "собес" not in l.lower():
            return l
    return lines[0] or "Модель"


async def fetch_sheet_rows_and_worksheet(interview: dict, session: aiohttp.ClientSession) -> tuple[list[list[str]] | None, any]:
    report_url = interview["report_sheet_url"]
    sheet_id = extract_sheet_id(report_url)
    if not sheet_id:
        return None, None

    gc = get_gspread_client()
    if gc:
        try:
            # Читаем через авторизованный Service Account (без ошибок 401!)
            sh = gc.open_by_key(sheet_id)
            worksheet = sh.sheet1
            rows = worksheet.get_all_values()
            return rows, worksheet
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
                return None, None
            content = await resp.text()
            reader = csv.reader(io.StringIO(content))
            return list(reader), None
    except Exception as e:
        logger.error(f"Ошибка HTTP запроса отчётника {sheet_id}: {e}")
        return None, None


def parse_hours(val_str: str) -> float | None:
    if not val_str:
        return None
    val_str = str(val_str).strip().replace(",", ".")
    time_match = re.search(r"(\d{1,2}):(\d{2})", val_str)
    if time_match:
        hh, mm = int(time_match.group(1)), int(time_match.group(2))
        return hh + (mm / 60.0)
    num_match = re.search(r"(\d+(?:\.\d+)?)", val_str)
    if num_match:
        try:
            return float(num_match.group(1))
        except ValueError:
            pass
    return None


async def check_single_report_sheet(bot: Bot, session: aiohttp.ClientSession, interview: dict):
    rows, worksheet = await fetch_sheet_rows_and_worksheet(interview, session)
    if not rows:
        return

    model_name = extract_model_name_from_text(interview["text"])
    from datetime import datetime
    now = datetime.now()

    for r_idx, row in enumerate(rows, start=1):
        if not row or len(row) < 1:
            continue
        
        # 1. Извлекаем дату из первой ячейки (например "31.01.2026", "31.01" или "31")
        first_cell = str(row[0]).strip()
        shift_date = None
        
        full_date_match = re.search(r"\b(\d{1,2})[\.\/](\d{1,2})[\.\/](\d{2,4})\b", first_cell)
        short_date_match = re.search(r"\b(\d{1,2})[\.\/](\d{1,2})\b", first_cell)
        
        if full_date_match:
            d_str, m_str, y_str = full_date_match.groups()
            d, m = int(d_str), int(m_str)
            if 1 <= d <= 31 and 1 <= m <= 12:
                if len(y_str) == 2:
                    y_str = f"20{y_str}"
                shift_date = f"{d:02d}.{m:02d}.{y_str}"
        elif short_date_match:
            d_str, m_str = short_date_match.groups()
            d, m = int(d_str), int(m_str)
            if 1 <= d <= 31 and 1 <= m <= 12:
                shift_date = f"{d:02d}.{m:02d}.{now.year}"
        elif first_cell.isdigit():
            day_num = int(first_cell)
            if 1 <= day_num <= 31:
                shift_date = f"{day_num:02d}.{now.month:02d}.{now.year}"
        
        if not shift_date:
            continue

        # 2. Читаем данные по смене (столбцы: Число[0], Тотал[1], Время работы[2], Выплата[3], Реф[4])
        total_val = str(row[1]).strip() if len(row) > 1 else ""
        work_time_val = str(row[2]).strip() if len(row) > 2 else ""
        payout_val_sheet = str(row[3]).strip() if len(row) > 3 else ""
        ref_val = str(row[4]).strip() if len(row) > 4 else ""

        hours = parse_hours(work_time_val)
        
        # Если нет данных по времени, тоталу или рефу — пропускаем пустую строку
        if hours is None and not total_val and not ref_val:
            continue

        # Рассчитываем сумму рефа: $12 если отработано >= 2 часов, иначе $0
        amount = 0.0
        if hours is not None:
            if hours >= 2.0:
                amount = 12.0
            else:
                amount = 0.0
        elif ref_val:
            clean_ref = re.sub(r"[^\d.,]", "", ref_val).replace(",", ".")
            try:
                ref_float = float(clean_ref)
                amount = 12.0 if ref_float > 0 else 0.0
            except ValueError:
                amount = 0.0

        # Записываем Реф числом, а Выплату формулой: 12% от Тотала минус Реф, минимум 0.
        tot_clean = re.sub(r"[^\d.,]", "", total_val).replace(",", ".")

        # Записываем формулу и Реф обратно в Google Таблицу если она открыта через gspread.
        if worksheet:
            try:
                if tot_clean:
                    payout_formula = f"=MAX(0,B{r_idx}*12%-E{r_idx})"
                    worksheet.update_cell(r_idx, 4, payout_formula)
                worksheet.update_cell(r_idx, 5, amount)
            except Exception as e_up:
                logger.warning(f"Не удалось обновить ячейки в отчётнике {interview['id']} (строка {r_idx}): {e_up}")

        hours_display = f"{hours:.1f}".replace(".0", "") if hours is not None else "0"

        # Регистрируем выплату или фиксируем смену без выплаты
        payout = await db.register_shift_payout(interview["id"], shift_date, amount=amount)
        if payout:
            agent_tg_id = payout["agent_tg_id"]
            new_balance = payout["new_balance"]

            model_row = await db.get_model_by_interview(interview["id"])
            model_id_line = f"🟩 ID модели: <b>{model_row['id']}</b>\n" if model_row else ""
            if amount > 0:
                msg_text = (
                    f"💵 <b>НАЧИСЛЕНИЕ ЗА СМЕНУ</b>\n"
                    f"{SEP}\n"
                    f"💚 Модель: <b>{model_name}</b>\n"
                    f"{model_id_line}"
                    f"📗 Заявка: <b>№{interview['id']}</b>\n"
                    f"✅ Дата смены: <b>{shift_date}</b>\n"
                    f"🟢 Время работы: <b>{hours_display} ч</b>\n"
                    f"{SEP}\n"
                    f"💵 Начислено: <b>+${amount:.2f}</b>\n"
                    f"💳 Ваш баланс: <b>${new_balance:.2f}</b>"
                )
            else:
                msg_text = (
                    f"✳️ <b>СМЕНА БЕЗ ВЫПЛАТЫ</b>\n"
                    f"{SEP}\n"
                    f"💚 Модель: <b>{model_name}</b>\n"
                    f"{model_id_line}"
                    f"📗 Заявка: <b>№{interview['id']}</b>\n"
                    f"✅ Дата смены: <b>{shift_date}</b>\n"
                    f"🟢 Время работы: <b>{hours_display} ч</b>\n"
                    f"{SEP}\n"
                    f"Модель отработала меньше 2 часов, поэтому реферальная выплата за смену не начисляется"
                )

            try:
                await bot.send_message(agent_tg_id, msg_text, parse_mode="HTML")
                logger.info(f"Агенту {agent_tg_id} отправлено уведомление по смене модели {model_name} ({shift_date}, amount=${amount})")
            except Exception as err:
                logger.error(f"Не удалось выслать уведомление агенту {agent_tg_id}: {err}")

            # Уведомление в топик партнёра (только при зачислении выплаты >= 2 часов)
            if amount > 0:
                partner_name = payout.get("partner")
                if partner_name and partner_name != "-":
                    try:
                        partner_obj = await db.get_partner_by_name(partner_name)
                        if partner_obj:
                            p_dict = dict(partner_obj)
                            if p_dict.get("chat_id"):
                                shift_num = await db.get_interview_processed_shifts_count(interview["id"])
                                target_topic = None
                                shift_header = None

                                if shift_num == 1:
                                    target_topic = p_dict.get("topic_shift1")
                                    shift_header = f"✅ <b>ПЕРВАЯ СМЕНА · ЗАЯВКА №{interview['id']} · {shift_date}</b>"
                                elif shift_num == 2:
                                    target_topic = p_dict.get("topic_shift2")
                                    shift_header = f"💵 <b>ВТОРАЯ СМЕНА · ЗАЯВКА №{interview['id']} · {shift_date}</b>"

                                if shift_header:
                                    p_shift_msg = await db.format_anketa_topic_message(interview, shift_header)
                                    p_kwargs = {"message_thread_id": target_topic} if target_topic else {}
                                    await bot.send_message(p_dict["chat_id"], p_shift_msg, parse_mode="HTML", **p_kwargs)
                    except Exception as p_err:
                        logger.error(f"Ошибка отправки уведомления о смене в топик партнёра: {p_err}")


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
