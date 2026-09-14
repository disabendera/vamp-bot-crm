"""Баннеры разделов: картинка + текст одним постом.
Первый раз файл грузится с диска, потом используется кэшированный file_id из settings."""
import os
import re
import logging

from aiogram import Bot
from aiogram.types import FSInputFile, Message

import db

logger = logging.getLogger(__name__)

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media")

# ключ раздела -> файл
BANNERS = {
    "profile": "profile.jpg",
    "chat": "chat.jpg",
    "training": "training.jpg",
    "wallet": "wallet.jpg",
    "shop": "shop.jpg",
}

CAPTION_LIMIT = 1024


async def send_banner(bot: Bot, chat_id: int, key: str, text: str,
                      reply_markup=None, parse_mode: str = "HTML",
                      disable_web_page_preview: bool = True):
    """Отправляет баннер раздела с текстом в подписи.
    Если текст длиннее лимита подписи - картинка с короткой подписью, текст отдельно.
    Если картинки нет - просто текст."""
    filename = BANNERS.get(key)
    path = os.path.join(MEDIA_DIR, filename) if filename else None
    if not path or not os.path.exists(path):
        return await bot.send_message(chat_id, text, parse_mode=parse_mode,
                                      reply_markup=reply_markup,
                                      disable_web_page_preview=disable_web_page_preview)

    # лимит считается по видимому тексту - HTML-теги не учитываются
    visible_len = len(re.sub(r"<[^>]+>", "", text))
    fits = visible_len <= CAPTION_LIMIT
    caption = text if fits else None
    markup_for_photo = reply_markup if fits else None

    cache_key = f"banner_fid:{key}"
    file_id = await db.get_setting(cache_key)
    media = file_id or FSInputFile(path)
    try:
        msg = await bot.send_photo(chat_id, media, caption=caption, parse_mode=parse_mode,
                                   reply_markup=markup_for_photo)
    except Exception as e:
        # file_id мог протухнуть (например, сменился бот) - шлём с диска и перекэшируем
        logger.warning(f"Баннер {key}: file_id не сработал ({e}), гружу с диска")
        msg = await bot.send_photo(chat_id, FSInputFile(path), caption=caption,
                                   parse_mode=parse_mode, reply_markup=markup_for_photo)
        file_id = None
    if not file_id and msg.photo:
        await db.set_setting(cache_key, msg.photo[-1].file_id)

    if not fits:
        msg = await bot.send_message(chat_id, text, parse_mode=parse_mode,
                                     reply_markup=reply_markup,
                                     disable_web_page_preview=disable_web_page_preview)
    return msg


async def reply_banner(message: Message, key: str, text: str, reply_markup=None, **kwargs):
    """Удобная обёртка для хендлеров: message.answer -> баннер"""
    return await send_banner(message.bot, message.chat.id, key, text, reply_markup, **kwargs)
