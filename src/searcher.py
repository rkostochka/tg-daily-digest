"""Живой веб-поиск фактов для deep-dive через Perplexity Sonar (OpenRouter).

Sonar сам ходит в веб и возвращает ответ с реальными ссылками-источниками.
Мы отдаём эти факты + список источников (с пометкой «железный/прочий») дальше в
синтезатор deep_dive, который применяет правила доверия и кросс-чека.

ВНИМАНИЕ: perplexity/* на OpenRouter — ПЛАТНЫЕ. При нулевом балансе OpenRouter
вернёт 402 — тогда web_research отдаёт error="no_credit", а deep_dive мягко
деградирует к ответу только по дайджесту/статьям.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# «Железные» источники — им доверяем даже по одному упоминанию.
# Ведомства РФ + крупные аналитические/деловые издания и рейтинговые агентства.
TRUSTED_DOMAINS = {
    # госорганы и регуляторы
    "cbr.ru", "rosstat.gov.ru", "gks.ru", "minfin.ru", "minfin.gov.ru",
    "minpromtorg.gov.ru", "economy.gov.ru", "nalog.gov.ru", "nalog.ru",
    "government.ru", "kremlin.ru", "duma.gov.ru", "council.gov.ru",
    "moex.com", "consultant.ru", "garant.ru", "pravo.gov.ru",
    # рейтинговые агентства
    "acra-ratings.ru", "raexpert.ru", "ra-national.ru",
    # деловые/аналитические издания и профильная аналитика
    "frankmedia.ru", "frankrg.com", "rbc.ru", "vedomosti.ru", "kommersant.ru",
    "interfax.ru", "tass.ru", "iz.ru", "forbes.ru",
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com",
}

SEARCH_SYSTEM = """Ты — ресёрчер. По вопросу пользователя найди КОНКРЕТНЫЕ проверяемые
факты в вебе: числа, даты, динамику. Приоритет — официальные источники РФ (ЦБ РФ,
Росстат, Минфин, Минпромторг, Минэкономразвития и др. ведомства) и авторитетные
деловые/аналитические издания.

Верни только факты: каждый — с числом, периодом и указанием, откуда он. Не рассуждай
и не делай выводов. Если достоверных данных нет — прямо скажи, что не нашёл."""


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def is_trusted(url: str) -> bool:
    """True, если домен ссылки входит в белый список «железных» источников."""
    d = _domain(url)
    return bool(d) and any(d == t or d.endswith("." + t) for t in TRUSTED_DOMAINS)


def _extract_citations(data: dict, message: dict) -> list[str]:
    """Ссылки-источники Sonar: OpenRouter кладёт их в top-level `citations`
    и/или в message.annotations (url_citation)."""
    urls: list[str] = []
    seen: set[str] = set()

    def _add(u: str | None) -> None:
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    for u in data.get("citations") or []:
        _add(u if isinstance(u, str) else (u or {}).get("url"))
    for ann in message.get("annotations") or []:
        _add((ann.get("url_citation") or {}).get("url"))
    return urls


async def web_research(
    api_key: str,
    question: str,
    model: str = "perplexity/sonar",
    timeout: float = 45.0,
) -> dict:
    """Ищет факты по вопросу. Возвращает {text, citations:[url], error:str|None}.

    error: None — успех; "no_credit" — нет баланса OpenRouter (402);
    "http_<код>" / прочее — сетевые/иные ошибки. При любой ошибке text пустой.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "tg-daily-digest-bot",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SEARCH_SYSTEM},
            {"role": "user", "content": question},
        ],
        "temperature": 0.1,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(OPENROUTER_URL, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        log.warning("web_research: сетевая ошибка: %s", e)
        return {"text": "", "citations": [], "error": str(e)}

    if r.status_code == 402:
        log.warning("web_research: 402 — недостаточно средств на OpenRouter")
        return {"text": "", "citations": [], "error": "no_credit"}
    if r.status_code != 200:
        log.warning("web_research: HTTP %s — %s", r.status_code, r.text[:200])
        return {"text": "", "citations": [], "error": f"http_{r.status_code}"}

    try:
        data = r.json()
        message = data["choices"][0]["message"]
        text = (message.get("content") or "").strip()
        citations = _extract_citations(data, message)
        log.info("web_research OK: %d симв., %d источников", len(text), len(citations))
        return {"text": text, "citations": citations, "error": None}
    except Exception as e:  # noqa: BLE001
        log.warning("web_research: не разобрал ответ: %s", e)
        return {"text": "", "citations": [], "error": "parse_error"}
