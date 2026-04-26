"""Суммаризация и тематическая группировка через OpenRouter (free-модель)."""
from __future__ import annotations

import json
import logging
import re

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .tg_reader import CollectedMessage

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Чтобы влезть в 1M контекст gemini-2.0-flash-exp:free и не раздуть промпт.
MAX_MSG_CHARS = 1200
MAX_LINK_CHARS = 1200

TOPIC_BLOCKS = [
    "🌍 Геополитика и регуляторика",
    "💰 Финансы и рынки",
    "🤖 AI и технологии",
    "🏢 Индустрия и продукт (martech / CDP / retention)",
    "📊 Бизнес и управление",
    "🔬 Наука и продуктивность",
    "📌 Прочее значимое",
]

SYSTEM_PROMPT = """Ты — аналитик-редактор. Делаешь ежедневный дайджест из Telegram-каналов.

Жёсткие правила:
1. ИГНОРИРУЙ мелочёвку: личные посты, реклама, мемы, "доброе утро", селфи каналов, мелкие апдейты, локальные новости без значимости.
2. ОСТАВЛЯЙ только ключевые события: значимые экономические/политические/технологические/индустриальные события, крупные сделки, регуляторные решения, прорывы, серьёзные исследования.
3. ДЕДУПЛИЦИРУЙ: если одна и та же новость встречается в нескольких каналах — объединяй в один пункт, не повторяй.
4. ГРУППИРУЙ по тематическим блокам (см. ниже). Если в блоке нет значимых новостей — пропускай блок целиком.
5. По каждому пункту: краткая суть (1–2 предложения) + строка "Влияние:" — что это меняет / на что влияет / каковы последствия (1 предложение, конкретно).
6. Ссылки на источники: если знаешь t.me-линк сообщения или внешнюю ссылку — добавь в скобках после пункта в формате [источник](url). Не больше 1–2 ссылок на пункт.
7. Пиши на русском, деловой тон, без воды и оценочных эпитетов.
8. Формат вывода — Markdown. Никаких преамбул и эпилогов.

Тематические блоки:
- 🌍 Геополитика и регуляторика
- 💰 Финансы и рынки
- 🤖 AI и технологии
- 🏢 Индустрия и продукт (martech / CDP / retention)
- 📊 Бизнес и управление
- 🔬 Наука и продуктивность
- 📌 Прочее значимое

Шаблон на каждый присутствующий блок:

## <название блока>

- **<заголовок пункта>.** <Суть в 1–2 предложениях.> [источник](url)
  Влияние: <одно предложение про последствия>.

В начале — строка "**Дайджест за <дата>**" и одна строчка с общей сводкой дня (1–2 предложения, что главное).
"""


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _build_corpus(messages: list[CollectedMessage], link_texts: dict[str, str]) -> str:
    """Собираем корпус для одного LLM-вызова, группируем по папкам и чатам."""
    by_folder: dict[str, dict[str, list[CollectedMessage]]] = {}
    for m in messages:
        by_folder.setdefault(m.folder, {}).setdefault(m.chat_title, []).append(m)

    parts: list[str] = []
    for folder, chats in by_folder.items():
        parts.append(f"\n\n# ПАПКА: {folder}\n")
        for chat, msgs in chats.items():
            parts.append(f"\n## Чат: {chat}\n")
            for m in msgs:
                date_str = m.date.strftime("%Y-%m-%d %H:%M") if m.date else ""
                parts.append(f"\n— [{date_str}]")
                if m.message_link:
                    parts.append(f" {m.message_link}")
                parts.append("\n")
                if m.text:
                    parts.append(_shorten(m.text, MAX_MSG_CHARS) + "\n")
                for url in m.urls:
                    fetched = link_texts.get(url, "")
                    if fetched:
                        parts.append(f"  ↳ {url}\n  «{_shorten(fetched, MAX_LINK_CHARS)}»\n")
                    else:
                        parts.append(f"  ↳ {url}\n")
    return "".join(parts)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
async def _call_openrouter(api_key: str, model: str, system: str, user: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "tg-daily-digest",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
        if r.status_code != 200:
            log.error("OpenRouter %s: %s", r.status_code, r.text[:500])
            r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


async def make_digest(
    api_key: str,
    model: str,
    messages: list[CollectedMessage],
    link_texts: dict[str, str],
    date_label: str,
) -> str:
    if not messages:
        return f"**Дайджест за {date_label}**\n\nЗа прошедшие сутки значимых сообщений не найдено."

    corpus = _build_corpus(messages, link_texts)
    user_prompt = (
        f"Сегодня: {date_label}.\n"
        f"Ниже — содержимое моих Telegram-папок за сутки. Сделай дайджест по правилам.\n"
        f"---\n{corpus}\n---"
    )
    log.info("Промпт: ~%d символов, %d сообщений", len(corpus), len(messages))
    return (await _call_openrouter(api_key, model, SYSTEM_PROMPT, user_prompt)).strip()
