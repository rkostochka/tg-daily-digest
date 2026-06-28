"""Глубокий анализ конкретной новости по запросу пользователя."""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Актуальные ID из GET /api/v1/models (проверено 2026-06-28).
# Разные провайдеры — разные rate-limit квоты.
FALLBACK_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",          # 550B, 1M ctx
    "nvidia/nemotron-3-super-120b-a12b:free",          # 120B, 1M ctx
    "google/gemma-4-31b-it:free",                      # Google, 262k ctx
    "google/gemma-4-26b-a4b-it:free",                  # Google MoE, 262k ctx
    "qwen/qwen3-next-80b-a3b-instruct:free",           # Qwen, 262k ctx
    "qwen/qwen3-coder:free",                           # Qwen, 1M ctx
    "nousresearch/hermes-3-llama-3.1-405b:free",       # 405B, 131k ctx
    "openai/gpt-oss-120b:free",                        # 120B, 131k ctx
    "openai/gpt-oss-20b:free",                         # 20B, 131k ctx
    "meta-llama/llama-3.3-70b-instruct:free",          # Llama, 131k ctx
    "meta-llama/llama-3.2-3b-instruct:free",           # маленькая, запасная
    "nvidia/nemotron-3-nano-30b-a3b:free",             # 30B MoE
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
]

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


def _fix_markdown(text: str) -> str:
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    return text.strip()


def _format_footer(model: str, usage: dict, key_usage: float | None) -> str:
    model_short = model.split("/", 1)[-1].replace(":free", "")

    prompt_t = usage.get("prompt_tokens") or usage.get("input_tokens")
    completion_t = usage.get("completion_tokens") or usage.get("output_tokens")
    total_t = usage.get("total_tokens")

    parts = [f"`{model_short}`"]

    if prompt_t is not None and completion_t is not None:
        parts.append(f"Токены: {prompt_t}+{completion_t}={prompt_t+completion_t}")
    elif total_t is not None:
        parts.append(f"Токены: {total_t}")

    cost = usage.get("cost")
    if cost is not None:
        if cost == 0:
            parts.append("Стоимость: бесплатно")
        elif cost < 0.001:
            parts.append(f"Стоимость: ${cost:.6f}")
        else:
            parts.append(f"Стоимость: ${cost:.4f}")

    if key_usage is not None:
        if key_usage == 0:
            parts.append("Баланс: $0 (free)")
        else:
            parts.append(f"Потрачено всего: ${key_usage:.4f}")

    return "\n\n➖➖➖➖➖➖➖➖➖➖\n_" + " · ".join(parts) + "_"


async def _fetch_key_usage(client: httpx.AsyncClient, api_key: str) -> float | None:
    try:
        r = await client.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5.0,
        )
        if r.status_code == 200:
            return r.json().get("data", {}).get("usage")
    except Exception:
        pass
    return None


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

    seen: set[str] = set()
    models: list[str] = []
    for m in [model] + FALLBACK_MODELS:
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
                "usage": {"include": True},
            }
            try:
                r = await client.post(OPENROUTER_URL, headers=headers, json=payload)

                if r.status_code == 200:
                    data = r.json()
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage") or {}
                    actual_model = data.get("model") or m
                    log.info("deep_dive OK: модель=%s токенов=%s стоимость=%s",
                             actual_model, usage.get("total_tokens"), usage.get("cost"))

                    key_usage = await _fetch_key_usage(client, api_key)
                    footer = _format_footer(actual_model, usage, key_usage)
                    return _fix_markdown(content) + footer

                if r.status_code == 401:
                    raise RuntimeError("OpenRouter: неверный API-ключ")

                if r.status_code == 429:
                    retry_after = int(r.headers.get("retry-after", 3))
                    wait = min(retry_after, 5)
                    log.warning("deep_dive: 429 от %s, жду %ds...", m, wait)
                    await asyncio.sleep(wait)
                    continue

                log.warning("deep_dive: модель %s вернула %s", m, r.status_code)

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                log.warning("deep_dive: сетевая ошибка для %s: %s", m, e)

    return "⚠️ Не удалось получить анализ — все модели недоступны. Попробуй чуть позже."
