"""Глубокий анализ конкретной новости по запросу пользователя."""
from __future__ import annotations

import asyncio
import logging
import os
import re

import httpx

from .link_fetcher import fetch_links
from .searcher import is_trusted, web_research

log = logging.getLogger(__name__)

# Максимум ссылок, которые качаем на один запрос — чтобы ограничить задержку
# и объём контекста. Реплай идёт на один кусок дайджеста (~4000 симв.), там
# обычно 2–6 источников, так что лимита с запасом хватает.
MAX_DIVE_LINKS = 10
_BARE_URL_RE = re.compile(r"https?://[^\s)\]]+")

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

SYSTEM_PROMPT = """Ты — аналитик, который помогает разобраться в новостях.

Пользователь видел дайджест и написал реплай по одной из тем. Твоя ГЛАВНАЯ задача —
ответить именно на его вопрос, а не пересказать новость по шаблону.

Сначала пойми, что спрашивают, и выбери режим:

• КОНКРЕТНЫЙ вопрос («кому это выгодно?», «чем грозит рублю?», «сколько стоит?»,
  «это надолго?») — начни с блока «💬 Ответ» и ответь прямо по существу. Дальше
  добавляй только те блоки структуры, которые реально помогают раскрыть ответ;
  нерелевантные — пропускай. Не выдавай все 4 блока по инерции.

• ОБЩИЙ или пустой запрос («подробнее», «расскажи», «разбери») — блок «💬 Ответ»
  не нужен, дай полный разбор по всей структуре ниже.

Отвечай по СУТИ вопроса, а не только тем, что есть в дайджесте: привлекай общие
знания, причинно-следственные связи и контекст, рассуждай. Но:
- не выдумывай конкретные цифры, имена и даты, которых нет в источниках и в которых
  ты не уверен — так и скажи, что этого нет;
- отделяй факты из новости от собственных рассуждений и оценок вероятностей.

Если ниже приложены ПОЛНЫЕ ТЕКСТЫ СТАТЕЙ по ссылкам из дайджеста — опирайся в
первую очередь на них: там детали, цифры и цитаты, которых нет в короткой выжимке.
Бери из статьи ровно ту, что относится к вопросу. Если нужной статьи среди
приложенных нет или её не удалось загрузить — используй РЕЗУЛЬТАТЫ ЖИВОГО ПОИСКА
(если они есть ниже), а если и их нет — честно скажи, что данных под рукой не было.

Если ниже есть РЕЗУЛЬТАТЫ ЖИВОГО ПОИСКА — используй их, чтобы ответить на то, чего
не хватает в дайджесте и статьях (например, цифры за прошлый год для сравнения г/г).
Правила доверия и проверки (обязательны):
- Источник с пометкой «✅ надёжный» (ЦБ РФ, Росстат, Минфин, Минпромторг, Минэк и
  др. ведомства, крупные аналитические/деловые издания, рейтинговые агентства) —
  ему можно доверять даже по одному упоминанию.
- Остальным («◽ прочий») верь ТОЛЬКО если один и тот же факт совпадает минимум в
  двух независимых источниках. Если факт держится на одном ненадёжном источнике —
  подавай как «по данным X, не подтверждено», не выдавай за установленный факт.
- У ключевых цифр всегда указывай источник. Если поиск противоречив или пуст —
  так и скажи, не выдумывай числа.

Формат (полный вид — для общих запросов; для конкретных бери только нужные блоки):

🔍 *[Короткое название темы]*

💬 *Ответ*
Прямой ответ на вопрос, 2–4 предложения. (Только если задан конкретный вопрос.)

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
- Приоритет — ответ на вопрос. Структура служит ответу, а не наоборот.
- Только Telegram Markdown: *жирный*, _курсив_, без ## и **.
- Если не понимаешь о какой теме спрашивают — попроси уточнить одним коротким предложением.
"""


def _fix_markdown(text: str) -> str:
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    return text.strip()


def _format_footer(model: str, usage: dict, key_usage: float | None,
                   search_marker: str = "") -> str:
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

    if search_marker:
        parts.append(search_marker)

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


def _collect_urls(digest_text: str, source_urls: list[str] | None) -> list[str]:
    """Ссылки из entities реплая (source_urls) + голые URL из текста, без дублей."""
    urls: list[str] = []
    seen: set[str] = set()
    for u in list(source_urls or []) + _BARE_URL_RE.findall(digest_text):
        u = u.rstrip(".,);]")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls[:MAX_DIVE_LINKS]


async def _build_articles_block(digest_text: str, source_urls: list[str] | None) -> str:
    """Качает статьи по ссылкам из дайджеста и собирает их в блок для промпта."""
    urls = _collect_urls(digest_text, source_urls)
    if not urls:
        return ""
    fetched = await fetch_links(urls)
    parts: list[str] = []
    for i, url in enumerate(urls, 1):
        text = (fetched.get(url) or "").strip()
        if text:
            parts.append(f"[{i}] {url}\n{text}")
    if not parts:
        return ""
    log.info("deep_dive: подтянуто %d/%d статей", len(parts), len(urls))
    return (
        "\n\nПОЛНЫЕ ТЕКСТЫ СТАТЕЙ ПО ССЫЛКАМ ИЗ ДАЙДЖЕСТА:\n---\n"
        + "\n\n---\n".join(parts)
        + "\n---"
    )


def _search_enabled() -> bool:
    return os.getenv("ENABLE_WEB_SEARCH", "1").strip().lower() not in {"0", "false", "no"}


async def _build_search_block(api_key: str, user_query: str) -> tuple[str, str]:
    """Живой веб-поиск фактов по вопросу.

    Возвращает (block, marker): block — контекст для LLM с источниками и пометкой
    доверия; marker — короткая строка для футера ответа (чтобы видеть, сработал ли
    поиск). Оба пустые, если поиск выключен.
    """
    if not _search_enabled():
        return "", ""
    model = os.getenv("SEARCH_MODEL", "perplexity/sonar")
    res = await web_research(api_key, user_query, model=model)
    if res["error"] == "no_credit":
        log.warning("Живой поиск пропущен: нет баланса OpenRouter")
        return "", "🔎 поиск: пропущен (нет баланса OpenRouter)"
    if res["error"]:
        return "", "🔎 поиск: недоступен"
    if not res["text"]:
        return "", "🔎 поиск: без результатов"

    lines = []
    n_trusted = 0
    for url in res["citations"]:
        trusted = is_trusted(url)
        n_trusted += trusted
        lines.append(f"- {url} ({'✅ надёжный' if trusted else '◽ прочий'})")
    sources = ("\n\nИСТОЧНИКИ:\n" + "\n".join(lines)) if lines else ""
    block = (
        "\n\nРЕЗУЛЬТАТЫ ЖИВОГО ПОИСКА (Perplexity Sonar):\n---\n"
        + res["text"]
        + sources
        + "\n---"
    )
    n = len(res["citations"])
    marker = f"🔎 поиск: {n} источн., {n_trusted} надёжных" if n else "🔎 поиск: выполнен"
    return block, marker


async def deep_dive(
    api_key: str,
    model: str,
    digest_text: str,
    user_query: str,
    source_urls: list[str] | None = None,
) -> str:
    """Возвращает глубокий анализ конкретной темы из дайджеста.

    Если в дайджесте есть ссылки (source_urls из entities реплая или голые URL
    в тексте) — их полный текст подтягивается и добавляется в контекст, чтобы
    отвечать по самой статье, а не только по короткой выжимке.
    """
    # Статьи по ссылкам и живой веб-поиск тянем параллельно — не ждём одно за другим.
    articles_block, (search_block, search_marker) = await asyncio.gather(
        _build_articles_block(digest_text, source_urls),
        _build_search_block(api_key, user_query),
    )

    user_prompt = (
        f"Вот дайджест:\n---\n{digest_text}\n---"
        f"{articles_block}"
        f"{search_block}\n\n"
        f"Вопрос пользователя: «{user_query}»\n\n"
        f"Определи, к какой теме дайджеста относится вопрос, и ответь именно на него "
        f"в заданном формате. Опирайся на тексты статей и результаты живого поиска "
        f"(если приложены), соблюдая правила доверия к источникам. "
        f"Если вопрос конкретный — начни с блока «💬 Ответ» и не "
        f"выдавай остальные блоки формально; если общий — дай полный разбор."
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
                    footer = _format_footer(actual_model, usage, key_usage, search_marker)
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
