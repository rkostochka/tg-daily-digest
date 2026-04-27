"""Суммаризация и тематическая группировка через OpenRouter (free-модель)."""
from __future__ import annotations

import json
import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .tme_reader import TgMessage

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MAX_MSG_CHARS = 1200
MAX_LINK_CHARS = 1200

SYSTEM_PROMPT = """Ты — аналитик-редактор. Делаешь ежедневный дайджест из Telegram-каналов.

Жёсткие правила:
1. ИГНОРИРУЙ мелочёвку: реклама, мемы, "доброе утро", анонсы стримов, скидки, локальные новости без значимости, мелкие апдейты продуктов, личные посты.
2. ОСТАВЛЯЙ только ключевые события: значимые экономические/политические/технологические/индустриальные события, крупные сделки, регуляторные решения, прорывы в науке/технологиях, серьёзные исследования и отчёты.
3. ДЕДУПЛИЦИРУЙ: если одна новость встречается в нескольких каналах — один пункт, не повторяй.
4. ГРУППИРУЙ по тематическим блокам. Блок без значимых новостей — пропускай целиком.
5. По каждому пункту: краткая суть (1–2 предложения) + строка "Влияние:" — что это меняет или на что влияет (1 предложение, конкретно, без воды).
6. Ссылки: если есть прямая ссылка на пост или внешняя ссылка — добавь в конце пункта [источник](url). Не более 1 ссылки на пункт.
7. Русский язык, деловой тон, без оценочных эпитетов и воды.
8. Формат: Markdown. Никаких преамбул и постскриптумов.

Тематические блоки (использовать только нужные):
- 🌍 Геополитика и регуляторика
- 💰 Финансы и рынки
- 🤖 AI и технологии
- 🏢 Индустрия и продукт (martech / CDP / retention)
- 📊 Бизнес и управление
- 🔬 Наука и продуктивность
- 📌 Прочее значимое

Формат вывода:

**Дайджест за <дата>**
<одна строка — главное за день, 1–2 предложения>

## <блок>

- **<заголовок>.** <Суть.> [источник](url)
  Влияние: <последствия>.
"""


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _build_corpus(messages: list[TgMessage], link_texts: dict[str, str]) -> str:
    by_channel: dict[str, list[TgMessage]] = {}
    for m in messages:
        by_channel.setdefault(m.channel, []).append(m)

    parts: list[str] = []
    for channel, msgs in by_channel.items():
        parts.append(f"\n\n## @{channel}\n")
        for m in msgs:
            date_str = m.date.strftime("%Y-%m-%d %H:%M") if m.date else ""
            parts.append(f"\n[{date_str}]")
            if m.post_url:
                parts.append(f" {m.post_url}")
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


# Список моделей для авто-fallback. Первая — основная, остальные — резервы при 404/429.
FALLBACK_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-120b:free",
    "z-ai/glm-4.5-air:free",
    "google/gemma-3-27b-it:free",
    "minimax/minimax-m2.5:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]


async def _post_once(client: httpx.AsyncClient, api_key: str, model: str,
                     system: str, user: str) -> tuple[int, str]:
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
    r = await client.post(OPENROUTER_URL, headers=headers, json=payload)
    return r.status_code, r.text


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=10))
async def _call_openrouter(api_key: str, primary_model: str, system: str, user: str) -> str:
    candidates: list[str] = [primary_model]
    for m in FALLBACK_MODELS:
        if m != primary_model:
            candidates.append(m)

    async with httpx.AsyncClient(timeout=120.0) as c:
        last_err: str = ""
        for model in candidates:
            status, body = await _post_once(c, api_key, model, system, user)
            if status == 200:
                try:
                    content = json.loads(body)["choices"][0]["message"]["content"]
                    log.info("Сработала модель: %s", model)
                    return content
                except Exception as e:
                    last_err = f"parse error from {model}: {e}; body={body[:300]}"
                    log.warning(last_err)
                    continue
            log.warning("Модель %s вернула %s — пробуем следующую", model, status)
            last_err = f"{model}: {status} {body[:300]}"
            # 401 — ключ не работает, нет смысла перебирать дальше
            if status == 401:
                break
        raise RuntimeError(f"Все модели не сработали. Последняя ошибка: {last_err}")


async def make_digest(
    api_key: str,
    model: str,
    messages: list[TgMessage],
    link_texts: dict[str, str],
    date_label: str,
) -> str:
    if not messages:
        return f"**Дайджест за {date_label}**\n\nЗа прошедшие сутки значимых сообщений не найдено."

    corpus = _build_corpus(messages, link_texts)
    user_prompt = (
        f"Сегодня: {date_label}.\n"
        f"Ниже — сообщения из Telegram-каналов за сутки. Сделай дайджест по правилам.\n"
        f"---\n{corpus}\n---"
    )
    log.info("Корпус: ~%d символов, %d сообщений", len(corpus), len(messages))
    return (await _call_openrouter(api_key, model, SYSTEM_PROMPT, user_prompt)).strip()
