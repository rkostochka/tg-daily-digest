"""Fetch + извлечение основного текста ссылок. Параллельно, с тайм-аутами."""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx
import trafilatura

log = logging.getLogger(__name__)

MAX_CHARS_PER_LINK = 1500
TIMEOUT = 12.0
MAX_PARALLEL = 8

# t.me-ссылки и подобные не имеет смысла фетчить — это просто пересылки
SKIP_HOSTS = {"t.me", "telegram.me", "telegram.org"}
SKIP_EXT = re.compile(r"\.(pdf|zip|rar|jpg|jpeg|png|gif|mp4|mp3|webm|webp|svg)(\?|$)", re.I)


def _should_skip(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        if any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
            return True
        if SKIP_EXT.search(url):
            return True
        return False
    except Exception:
        return True


async def _fetch_one(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> tuple[str, str]:
    async with sem:
        try:
            r = await client.get(url, follow_redirects=True, timeout=TIMEOUT)
            r.raise_for_status()
            html = r.text
        except Exception as e:
            log.debug("fetch fail %s: %s", url, e)
            return url, ""

    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        ) or ""
    except Exception as e:
        log.debug("extract fail %s: %s", url, e)
        return url, ""

    text = text.strip()
    if len(text) > MAX_CHARS_PER_LINK:
        text = text[:MAX_CHARS_PER_LINK].rsplit(" ", 1)[0] + "…"
    return url, text


async def fetch_links(urls: list[str]) -> dict[str, str]:
    """Возвращает словарь {url: extracted_text}. Пустая строка — если не удалось."""
    unique = []
    seen: set[str] = set()
    for u in urls:
        if u not in seen and not _should_skip(u):
            seen.add(u)
            unique.append(u)

    if not unique:
        return {}

    sem = asyncio.Semaphore(MAX_PARALLEL)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "ru,en;q=0.9",
    }
    async with httpx.AsyncClient(headers=headers, http2=True) as client:
        results = await asyncio.gather(*[_fetch_one(client, u, sem) for u in unique])
    out = {url: text for url, text in results}
    log.info("Скачано %d/%d ссылок (с непустым текстом)", sum(1 for t in out.values() if t), len(unique))
    return out
