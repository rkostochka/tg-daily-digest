"""Глубокий анализ конкретной новости по запросу пользователя."""
from __future__ import annotations

import json
import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """Ты — аналитик, который объясняет новости глубоко и по делу.

Пользователь видел дайджест и хочет разобраться в одной конкретной теме.
Твоя задача: найти в дайджесте то, о чём спрашивают, и дать развёрнутый анализ.

Формат ответа (строго):

🔍 *[Короткое название темы]*

📌 *Что произошло*
2–3 предложения: суть события с конкретными деталями (цифры, имена, даты).

🧩 *Почему именно сейчас*
Контекст и предпосылки — что накапливалось, что стало триггером.
• Фактор 1
• Фактор 2
• (не больше 4 факторов)

📈 *Что это меняет*
_Краткосрочно (недели–месяцы):_ конкретно для кого и как.
_Долгосрочно:_ структурный сдвиг или разовое событие?

👁 *Чего ждать дальше*
1–2 сигнала, за которыми стоит следить.

Правила:
- Деловой тон, без воды и общих слов.
- Если в дайджесте недостаточно деталей — честно скажи что именно неизвестно, не придумывай.
- Только Telegram Markdown: *жирный*, _курсив_, без ## и **.
- Если не понимаешь о какой теме спрашивают — попроси уточнить одним коротким предложением.
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def deep_dive(
    api_key: str,
    model: str,
    digest_text: str,
    user_query: str,
) -> str:
    """Возвращает глубокий анализ конкретной темы из дайджеста."""
    user_prompt = (
        f"Вот дайджест:\n---\n{digest_text}\n---\n\n"
        f"Пользователь хочет разобраться: «{user_query}»\n\n"
        f"Найди в дайджесте соответствующую тему и дай развёрнутый анализ."
    )

    candidates = [model] + [
        "nousresearch/hermes-3-llama-3.1-405b:free",
        "openai/gpt-oss-120b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-3-27b-it:free",
    ]
    # deduplicate, keep order
    seen: set[str] = set()
    models: list[str] = []
    for m in candidates:
        if m not in seen:
            seen.add(m)
            models.append(m)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "tg-daily-digest-bot",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        for m in models:
            payload = {
                "model": m,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
            }
            try:
                r = await client.post(OPENROUTER_URL, headers=headers, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    content = data["choices"][0]["message"]["content"]
                    # fix common markdown issues for Telegram
                    import re
                    content = re.sub(r"\*\*([^*\n]+?)\*\*", r"*\1*", content)
                    content = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", content, flags=re.MULTILINE)
                    log.info("deep_dive: модель %s, токенов %s",
                             m, data.get("usage", {}).get("total_tokens"))
                    return content.strip()
                if r.status_code == 401:
                    raise RuntimeError("OpenRouter: неверный API-ключ")
                log.warning("deep_dive: модель %s вернула %s", m, r.status_code)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                log.warning("deep_dive: сетевая ошибка для %s: %s", m, e)

    return "⚠️ Не удалось получить анализ — все модели недоступны. Попробуй чуть позже."
