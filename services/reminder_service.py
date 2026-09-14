import asyncio
import logging
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import db
from services.shift_tracker import extract_model_name_from_text

logger = logging.getLogger(__name__)

SEP = "━━━━━━━━━━━━━━━"

last_morning_digest_date = ""


def build_confirm_buttons(interview_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Модель придёт", callback_data=f"agent_confirm:{interview_id}"),
        InlineKeyboardButton(text="🚫 Отклонить", callback_data=f"agent_reject:{interview_id}"),
    ]])


async def check_6h_confirm_reminders(bot: Bot):
    """
    Проверяет заявки со статусом 'Принято', для которых кнопка подтверждения еще не отправлялась.
    Если до собеседования осталось <= 6 часов, отправляет агенту сообщение с кнопками подтверждения.
    """
    try:
        # заявки, где партнёр принял, а агент ещё не ответил (статус всё ещё «Принято»)
        accepted_interviews = await db.get_unanswered_accepted_interviews()
        if not accepted_interviews:
            return

        msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)

        for inv in accepted_interviews:
            i_dict = dict(inv)
            interview_id = i_dict["id"]
            agent_tg_id = i_dict["tg_id"]
            sobes_date = i_dict.get("sobes_date") or ""
            sobes_time = i_dict.get("sobes_time") or ""
            text = i_dict.get("text") or ""

            sobes_dt = db.parse_interview_datetime(sobes_date, sobes_time, text)

            # Если время собеседования удалось распарсить и до него > 6 часов, ждём
            if sobes_dt and (sobes_dt - msk_now) > timedelta(hours=6):
                continue

            # До собеса осталось <= 6 часов (или дата не распарсилась/прошла) -> отправляем кнопки!
            model_nm = extract_model_name_from_text(text)
            msg_text = (
                f"🟢 <b>СОБЕСЕДОВАНИЕ №{interview_id} ЧЕРЕЗ 6 ЧАСОВ</b>\n"
                f"{SEP}\n"
                + (f"💚 Модель: <b>{model_nm}</b>\n" if model_nm else "")
                + f"📗 Собеседование: <b>{sobes_date or '-'} в {sobes_time or '-'} МСК</b>\n"
                f"{SEP}\n"
                f"Свяжитесь с моделью и подтвердите, что она <b>придёт на собеседование</b> "
                f"в назначенное время. Если модель не выходит на связь или отказалась - нажмите «Не придёт»"
            )

            try:
                await bot.send_message(
                    agent_tg_id,
                    msg_text,
                    parse_mode="HTML",
                    reply_markup=build_confirm_buttons(interview_id)
                )
                await db.mark_reminder_6h_sent(interview_id)
                logger.info(f"Агенту {agent_tg_id} отправлено 6-часовое напоминание по собеседованию №{interview_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки кнопок подтверждения агенту {agent_tg_id}: {e}")

    except Exception as err:
        logger.error(f"Ошибка в check_6h_confirm_reminders: {err}")


async def check_morning_digest(bot: Bot):
    """
    Отправляет агентам с утра (в 09:00 МСК) дайджест собеседований на сегодня.
    """
    global last_morning_digest_date
    try:
        msk_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)
        today_str = msk_now.strftime("%d.%m.%Y")

        # Отправляем дайджест в районе 09:00 МСК
        if msk_now.hour == 9 and last_morning_digest_date != today_str:
            last_morning_digest_date = today_str
            agents_interviews = await db.get_today_interviews_grouped_by_agent(today_str)

            for agent_id, interviews in agents_interviews.items():
                if not interviews:
                    continue

                lines = [f"🟢 <b>СОБЕСЕДОВАНИЯ НА СЕГОДНЯ · {today_str}</b>\n{SEP}"]
                for idx, inv in enumerate(interviews, 1):
                    s_time = inv.get("sobes_time") or "-"
                    status = inv.get("app_status") or "Новая"
                    lines.append(
                        f"<b>{idx}. {s_time} МСК</b> · заявка №{inv['id']}\n"
                        f"✅ Статус: {status}"
                    )

                full_msg = "\n".join(lines)
                try:
                    await bot.send_message(agent_id, full_msg, parse_mode="HTML")
                    logger.info(f"Агенту {agent_id} отправлен утренний дайджест собеседований на сегодня ({len(interviews)} шт)")
                except Exception as e:
                    logger.error(f"Не удалось отправить утренний дайджест агенту {agent_id}: {e}")

    except Exception as err:
        logger.error(f"Ошибка в check_morning_digest: {err}")


async def start_reminder_service(bot: Bot, interval_seconds: int = 60):
    logger.info("⏰ Фоновый сервис напоминаний и дайджеста запущен")
    while True:
        try:
            await check_6h_confirm_reminders(bot)
            await check_morning_digest(bot)
        except Exception as e:
            logger.error(f"Ошибка в цикле reminder_service: {e}")
        await asyncio.sleep(interval_seconds)
