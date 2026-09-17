import asyncio
import csv
import io
import logging
import os
import re
import threading
import aiohttp
import gspread
from aiogram import Bot

import db

logger = logging.getLogger(__name__)

SEP = "━━━━━━━━━━━━━━━"

SERVICE_ACCOUNT_FILE = "service_account.json"
_gspread_client = None
_gspread_client_lock = threading.Lock()
MAX_CONCURRENT_REPORTS = 5


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
    if _gspread_client is not None:
        return _gspread_client

    # Несколько рабочих задач могут одновременно запросить клиент после старта.
    with _gspread_client_lock:
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


async def fetch_sheet_rows_and_worksheet(model: dict, session: aiohttp.ClientSession) -> tuple[list[list[str]] | None, any]:
    report_url = model["report_sheet_url"]
    sheet_id = extract_sheet_id(report_url)
    if not sheet_id:
        return None, None

    gc = await asyncio.to_thread(get_gspread_client)
    if gc:
        try:
            # Читаем через авторизованный Service Account (без ошибок 401!)
            def read_sheet():
                sh = gc.open_by_key(sheet_id)
                worksheet = sh.sheet1
                return worksheet.get_all_values(), worksheet

            rows, worksheet = await asyncio.to_thread(read_sheet)
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
                logger.warning(f"Не удалось получить CSV отчётника модели {model['model_code']} (status: {resp.status}, URL: {csv_url})")
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


async def check_single_report_sheet(bot: Bot, session: aiohttp.ClientSession, model: dict):
    rows, worksheet = await fetch_sheet_rows_and_worksheet(model, session)
    if not rows:
        return

    model_name = model["name"] or extract_model_name_from_text(model["application_text"])
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
                    await asyncio.to_thread(worksheet.update_cell, r_idx, 4, payout_formula)
                await asyncio.to_thread(worksheet.update_cell, r_idx, 5, amount)
            except Exception as e_up:
                logger.warning(f"Не удалось обновить ячейки в отчётнике модели {model['model_code']} (строка {r_idx}): {e_up}")

        hours_display = f"{hours:.1f}".replace(".0", "") if hours is not None else "0"

        # Регистрируем выплату или фиксируем смену без выплаты
        payout = await db.register_shift_payout(model["id"], shift_date, amount=amount)
        if payout:
            agent_tg_id = payout["agent_tg_id"]
            new_balance = payout["new_balance"]

            model_id_line = f"🟩 ID модели: <b>{model['model_code']}</b>\n"
            if amount > 0:
                msg_text = (
                    f"💵 <b>НАЧИСЛЕНИЕ ЗА СМЕНУ</b>\n"
                    f"{SEP}\n"
                    f"💚 Модель: <b>{model_name}</b>\n"
                    f"{model_id_line}"
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
                                shift_num = await db.get_model_processed_shifts_count(model["id"])
                                target_topic = None
                                shift_header = None

                                if shift_num == 1:
                                    target_topic = p_dict.get("topic_shift1")
                                    shift_header = f"✅ <b>ПЕРВАЯ СМЕНА · ID МОДЕЛИ {model['model_code']} · {shift_date}</b>"
                                elif shift_num == 2:
                                    target_topic = p_dict.get("topic_shift2")
                                    shift_header = f"💵 <b>ВТОРАЯ СМЕНА · ID МОДЕЛИ {model['model_code']} · {shift_date}</b>"

                                if shift_header:
                                    p_shift_msg = await db.format_anketa_topic_message(model, shift_header)
                                    p_kwargs = {"message_thread_id": target_topic} if target_topic else {}
                                    await bot.send_message(p_dict["chat_id"], p_shift_msg, parse_mode="HTML", **p_kwargs)
                    except Exception as p_err:
                        logger.error(f"Ошибка отправки уведомления о смене в топик партнёра: {p_err}")


_collection_lock = asyncio.Lock()


async def collect_shifts(bot: Bot) -> bool:
    """Собирает все отчётники в отдельной async-задаче и не блокирует polling."""
    if _collection_lock.locked():
        logger.info("Сбор отчётников уже выполняется, повторный запуск пропущен")
        return False

    async with _collection_lock:
        try:
            models = await db.get_models_with_report_sheets()
            if not models:
                logger.info("Отчётники для сбора не найдены")
                return True

            timeout = aiohttp.ClientTimeout(total=60)
            connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_REPORTS, limit_per_host=MAX_CONCURRENT_REPORTS)
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_REPORTS)

            async def check_with_limit(model):
                async with semaphore:
                    return await check_single_report_sheet(bot, session, model)

            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                tasks = [asyncio.create_task(check_with_limit(i)) for i in models]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.error("Ошибка обработки отчётника: %s", result)
            logger.info("Сбор отчётников завершён: %s (пул: %s)", len(models), MAX_CONCURRENT_REPORTS)
            return True
        except Exception:
            logger.exception("Ошибка сбора отчётников")
            return False


async def start_shift_tracker(bot: Bot):
    """Ежедневно запускает сбор в настроенное администратором время."""
    from datetime import datetime, timedelta, timezone

    moscow_tz = timezone(timedelta(hours=3), name="MSK")

    logger.info("🔄 Планировщик сбора смен запущен")
    while True:
        configured_time = await db.get_setting("shift_collection_time") or "23:00"
        try:
            hour, minute = (int(part) for part in configured_time.split(":", 1))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (TypeError, ValueError):
            logger.warning("Некорректное время сбора смен: %r", configured_time)
            hour, minute = 23, 0

        now = datetime.now(moscow_tz)
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep(max(1, (next_run - now).total_seconds()))
        asyncio.create_task(collect_shifts(bot))
