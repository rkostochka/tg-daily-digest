"""Интерактивный бот: отвечает на реплаи к дайджесту развёрнутым анализом."""
from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from .deep_diver import deep_dive

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("bot_listener")

# Триггерные слова — реплай с любым из них запускает анализ.
# Пустой реплай (просто ответ на сообщение) тоже работает.
TRIGGER_WORDS = {"?", "подробнее", "почему", "расскажи", "объясни", "разбери",
                 "deep", "детали", "контекст", "почему так", "что это значит"}


def _is_trigger(text: str) -> bool:
    """Любой реплай считается триггером — пользователь явно выбрал новость."""
    return True  # reply to bot message is enough signal


async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    # Только реплаи
    if not message.reply_to_message:
        return

    # Только реплаи на сообщения самого бота
    bot_user = await context.bot.get_me()
    if message.reply_to_message.from_user.id != bot_user.id:
        return

    digest_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not digest_text.strip():
        return

    user_query = (message.text or "").strip()
    # Если реплай пустой или только точка — значит хотят анализ всего сообщения
    if not user_query or user_query in {".", "!", "+"}:
        user_query = "подробнее о главной теме"

    log.info("Запрос deep_dive: user=%s query=%r", message.from_user.id, user_query[:80])

    # Показываем "печатает..." пока думаем
    await context.bot.send_chat_action(
        chat_id=message.chat_id,
        action=ChatAction.TYPING,
    )

    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ.get("LLM_MODEL", "nousresearch/hermes-3-llama-3.1-405b:free")

    try:
        analysis = await deep_dive(
            api_key=api_key,
            model=model,
            digest_text=digest_text,
            user_query=user_query,
        )
    except Exception as e:
        log.error("deep_dive failed: %s", e)
        analysis = "⚠️ Произошла ошибка при анализе. Попробуй ещё раз."

    await message.reply_text(
        analysis,
        parse_mode=ParseMode.MARKDOWN,
        reply_to_message_id=message.message_id,
    )


def main() -> None:
    bot_token = os.environ.get("BOT_TOKEN", "")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не задан")

    app = Application.builder().token(bot_token).build()

    # Реагируем на текстовые сообщения (реплаи фильтруются внутри handle_reply)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply))

    log.info("Бот запущен, жду реплаев на дайджест...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
