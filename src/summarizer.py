"""Суммаризация и тематическая группировка через OpenRouter (free-модель)."""
from __future__ import annotations

import json
import logging
import re

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .tme_reader import TgMessage

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MAX_MSG_CHARS = 1200
MAX_LINK_CHARS = 1200

SYSTEM_PROMPT = """Ты — аналитик-редактор. Делаешь ежедневный дайджест из Telegram-каналов для отправки в Telegram.

ГЛАВНАЯ ЦЕЛЬ: НЕ дублировать поток новостей, а оставить только то, что влияет на ключевые решения людей в компаниях / государствах / на рынках. Читатель не должен тратить время на новости, которые не сдвигают его план/стратегию.

Шкала значимости (significance) 1–5. В дайджест попадают ТОЛЬКО ≥4:
- 5 — меняет глобальный/национальный расклад (войны, ключевые регуляторы, ЦБ-ставки, M&A >$1B, технопрорывы уровня GPT-4-момента).
- 4 — меняет отрасль или класс активов (крупные сделки $100M+, новый закон, прорывная модель, structural shift).
- 3 — interesting, но не actionable → ОТСЕКАТЬ.
- 1–2 — шум → ОТСЕКАТЬ.

Жёсткие правила содержания:
1. ИГНОРИРУЙ мелочёвку: реклама, мемы, "доброе утро", анонсы стримов, скидки, локальные новости без значимости, мелкие апдейты продуктов, личные посты.
2. ОСТАВЛЯЙ только события уровня ≥4 по шкале выше.
3. ДЕДУПЛИЦИРУЙ: если одна новость встречается в нескольких каналах — один пункт, не повторяй.
4. ГРУППИРУЙ по тематическим блокам. Блок без значимых новостей — пропускай целиком.
5. По каждому пункту: краткая суть с конкретными цифрами (1–2 предложения) + строка с эмодзи ⚡ — что это меняет, для какой аудитории (CEO/инвестор/гражданин/отрасль). Без воды, без общих слов.
6. Ссылки: если есть — в конце пункта [источник](url). Не более 1 ссылки на пункт.
7. Русский язык, деловой тон, без оценочных эпитетов.
8. Целевой объём: 4–10 пунктов в сутки. Если значимого нет — короткий дайджест с пометкой «Без значимых событий».

ОТСЕКАЙ обязательно:
- Тактические сводки (отдельные удары/пожары/ДТП без стратегического сдвига).
- Внутренние планы компаний без pivot'а («компания N расширяется в регион»).
- Lifestyle / AI-гимиксы (питомцы в Codex, новые UI-фичи).
- Ranking-новости («самые богатые», «топ-10 рынка»).
- Generic консалтинговые прогнозы без numeric anchor от tier-1 источника.
- Стандартные квартальные отчёты компаний БЕЗ существенной динамики или surprise (см. ниже исключение для RU).

ОТЧЁТНОСТЬ КОМПАНИЙ (исключение):
- M&A >$1B, банкротство, первый убыток за долгое время, превышение/недостижение консенсуса >20%, structural shift → значимость 4, оставляй.
- Российские компании из top-200 (РБК-500 / Forbes RU): банки (Сбер/ВТБ/Альфа/Т-Банк/Газпромбанк), ритейл (X5/Магнит/М.Видео/Озон/WB/Лента/Fix Price), телеком (МТС/Мегафон/Билайн), девелоперы (ПИК/Самолёт/ЛСР/Эталон/Setl), сырьё (Газпром/Роснефть/Лукойл/Норникель/Северсталь/НЛМК/Татнефть/Сургутнефтегаз), tech (Яндекс/VK/HeadHunter/Whoosh/Cian), FMCG, страхование. Их финрезультаты с любой динамикой (+/− YoY или QoQ) → ВСЕГДА оставлять, даже если "стандартные". Минимум: название, выручка с динамикой, прибыль с динамикой, ключевая причина.
- Всегда указывай к чему сравнение: YoY, QoQ, или к консенсусу аналитиков.

ПРОГНОЗЫ И ИССЛЕДОВАНИЯ:
- Tier-1 (McKinsey/BCG/Bain/Goldman/JPM/MS/IMF/ECB/World Bank/ЦБ РФ/Минфин/Росстат) с конкретным numeric landmark, меняющим консенсус → 4.
- Прочие — отсекай.

КРИТИЧНО — формат под Telegram Markdown (legacy):
- НЕЛЬЗЯ использовать заголовки `#`, `##`, `###` — Telegram их не понимает.
- НЕЛЬЗЯ использовать двойные звёздочки `**bold**` — только одиночные `*bold*`.
- Допустимо: `*жирный*`, `_курсив_`, `` `моно` ``, `[текст](url)`.
- Никаких преамбул и постскриптумов.

Тематические блоки (использовать только нужные, в этом порядке):
🌍 Геополитика и регуляторика
💰 Финансы и рынки
🇷🇺 Российский крупный бизнес — финрезы (отдельный блок, ВСЕГДА показывай если есть отчёты top-200 RU за день; даже если 1 пункт)
🤖 AI и технологии
🏢 Индустрия и продукт (martech / CDP / retention)
📊 Бизнес и управление
🔬 Наука и продуктивность
📌 Прочее значимое

Формат вывода (придерживайся точно):

🗓 *Дайджест за <дата>*

*Что изменилось за день*
• <конкретный сдвиг, не просто факт: "X меняет Y" или "теперь Z иначе">
• <ещё один>
• <3–5 пунктов, не больше>

➖➖➖➖➖➖➖➖➖➖

🌍 *Геополитика и регуляторика*

▸ *<Заголовок пункта>.* <Суть с цифрами в 1–2 предложениях.> [источник](url)
⚡ _<Что это меняет, для какой аудитории.>_

➖➖➖➖➖➖➖➖➖➖

💰 *Финансы и рынки*

▸ ...

➖➖➖➖➖➖➖➖➖➖

🇷🇺 *Российский крупный бизнес — финрезы*

▸ *<Компания>.* Выручка <±% YoY/QoQ> до <значение>, прибыль/убыток <значение, ±% YoY>. Причина: <короткая>. [источник](url)
⚡ _<Сигнал по сегменту/отрасли.>_

(и так далее по блокам, разделитель ➖ между блоками)
"""


# Чинит распространённые ошибки markdown-формата от LLM под Telegram legacy Markdown.
def _normalize_for_telegram(text: str) -> str:
    # **bold** -> *bold*
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"*\1*", text)
    # ### Заголовок / ## Заголовок / # Заголовок -> *Заголовок* (на отдельной строке)
    text = re.sub(r"^#{1,6}\s+(.+?)\s*$", r"*\1*", text, flags=re.MULTILINE)
    # Лишние более чем 2 переноса -> ровно 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


# Цепочка fallback по убыванию «мощности». Берётся primary из env, остальные — резервы.
FALLBACK_MODELS = [
    "nousresearch/hermes-3-llama-3.1-405b:free",   # 405B dense — лучший reasoning
    "inclusionai/ling-2.6-1t:free",                # 1T MoE
    "openai/gpt-oss-120b:free",                    # 120B dense, стабильна
    "nvidia/nemotron-3-super-120b-a12b:free",      # 120B MoE
    "minimax/minimax-m2.5:free",                   # 196k контекст
    "z-ai/glm-4.5-air:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
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
        "usage": {"include": True},
    }
    r = await client.post(OPENROUTER_URL, headers=headers, json=payload)
    return r.status_code, r.text


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=10))
async def _call_openrouter(api_key: str, primary_model: str, system: str, user: str) -> dict:
    """Возвращает {content, model, total_tokens, prompt_tokens, completion_tokens}."""
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
                    data = json.loads(body)
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage") or {}
                    actual_model = data.get("model") or model
                    log.info("Сработала модель: %s, токенов: %s",
                             actual_model, usage.get("total_tokens"))
                    return {
                        "content": content,
                        "model": actual_model,
                        "total_tokens": usage.get("total_tokens"),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "cost": usage.get("cost"),
                    }
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
    result = await _call_openrouter(api_key, model, SYSTEM_PROMPT, user_prompt)
    digest = _normalize_for_telegram(result["content"])

    # Футер со справочной информацией
    model_short = result["model"].split("/", 1)[-1].replace(":free", "")
    total = result.get("total_tokens")
    prompt_t = result.get("prompt_tokens")
    completion_t = result.get("completion_tokens")
    parts = [f"Модель: `{model_short}`"]
    if total is not None:
        if prompt_t is not None and completion_t is not None:
            parts.append(f"Токены: {total} ({prompt_t} вход + {completion_t} ответ)")
        else:
            parts.append(f"Токены: {total}")
    cost = result.get("cost")
    if cost is not None:
        if cost == 0:
            parts.append("Стоимость: $0 (free)")
        elif cost < 0.01:
            parts.append(f"Стоимость: ${cost:.6f}")
        else:
            parts.append(f"Стоимость: ${cost:.4f}")
    parts.append(f"Сообщений: {len(messages)}")
    footer = "\n\n➖➖➖➖➖➖➖➖➖➖\n_" + " · ".join(parts) + "_"
    return digest + footer
