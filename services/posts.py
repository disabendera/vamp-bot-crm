"""Посты разделов (Условия сети, Офферы, Магазин): текст ИЛИ файл/фото/гифка/видео с подписью.
Хранится в settings строкой: обычный текст (старый формат) или JSON {"type","file_id","caption"}."""
import json
import re

from aiogram import Bot
from aiogram.types import Message

CAPTION_LIMIT = 1024


def serialize_post(message: Message) -> str:
    """Что прислал админ -> строка для settings"""
    caption = message.html_text if (message.caption or message.text) else ""
    if message.document:
        return json.dumps({"type": "document", "file_id": message.document.file_id, "caption": caption})
    if message.photo:
        return json.dumps({"type": "photo", "file_id": message.photo[-1].file_id, "caption": caption})
    if message.animation:
        return json.dumps({"type": "animation", "file_id": message.animation.file_id, "caption": caption})
    if message.video:
        return json.dumps({"type": "video", "file_id": message.video.file_id, "caption": caption})
    return caption


def parse_post(stored: str | None) -> dict:
    if not stored:
        return {"type": "empty"}
    try:
        data = json.loads(stored)
        if isinstance(data, dict) and data.get("type"):
            return data
    except (ValueError, TypeError):
        pass
    return {"type": "text", "text": stored}


def is_empty(stored: str | None) -> bool:
    return parse_post(stored)["type"] == "empty"


async def send_post(bot: Bot, chat_id: int, stored: str | None, header: str = "",
                    empty_text: str = "Пост ещё не задан", reply_markup=None):
    """Показывает пост: заголовок + текст, либо файл/медиа с подписью"""
    post = parse_post(stored)
    if post["type"] == "empty":
        return await bot.send_message(chat_id, f"{header}{empty_text}" if header else empty_text,
                                      parse_mode="HTML", reply_markup=reply_markup)
    if post["type"] == "text":
        return await bot.send_message(chat_id, f"{header}{post['text']}", parse_mode="HTML",
                                      disable_web_page_preview=True, reply_markup=reply_markup)

    caption = f"{header}{post.get('caption', '')}"
    visible = len(re.sub(r"<[^>]+>", "", caption))
    if visible > CAPTION_LIMIT:
        # подпись не влезает - текст отдельно, файл следом
        await bot.send_message(chat_id, caption, parse_mode="HTML", disable_web_page_preview=True)
        caption, markup = None, reply_markup
    else:
        caption, markup = caption or None, reply_markup

    kwargs = dict(caption=caption, parse_mode="HTML", reply_markup=markup)
    if post["type"] == "document":
        return await bot.send_document(chat_id, post["file_id"], **kwargs)
    if post["type"] == "photo":
        return await bot.send_photo(chat_id, post["file_id"], **kwargs)
    if post["type"] == "animation":
        return await bot.send_animation(chat_id, post["file_id"], **kwargs)
    if post["type"] == "video":
        return await bot.send_video(chat_id, post["file_id"], **kwargs)
    return await bot.send_message(chat_id, caption or empty_text, parse_mode="HTML")
