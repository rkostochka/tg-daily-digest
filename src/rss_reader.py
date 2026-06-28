"""Читаем RSS/Atom-ленты блогов и возвращаем TgMessage — совместимо с tme_reader."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from .tme_reader import TgMessage

_HTML_TAG = re.compile(r"<[^>]+>")

log = logging.getLogger(__name__)

TIMEOUT = 15.0
MAX_PARALLEL = 6
MAX_TITLE_CHARS = 300
MAX_SUMMARY_CHARS = 600


def _parse_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _strip_html(text: str) -> str:
    text = _HTML_TAG.sub(" ", text)
    # collapse multiple spaces/newlines
    return re.sub(r"\s{2,}", " ", text).strip()


def _entry_to_message(source_name: str, entry, cutoff: datetime) -> TgMessage | None:
    date = _parse_date(entry)
    if date and date < cutoff:
        return None

    link = getattr(entry, "link", "") or ""
    title = _strip_html(getattr(entry, "title", "") or "")
    summary = _strip_html(getattr(entry, "summary", "") or "")

    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0] + "…"
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"

    text = title
    if summary and summary.strip() != title.strip():
        text = f"{title}\n{summary}"

    if not text and not link:
        return None

    return TgMessage(
        channel=source_name,
        text=text,
        urls=[link] if link else [],
        date=date,
        post_url=link or None,
    )


async def _fetch_feed(
    client: httpx.AsyncClient,
    url: str,
    source_name: str,
    cutoff: datetime,
    sem: asyncio.Semaphore,
) -> list[TgMessage]:
    async with sem:
        try:
            r = await client.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            raw = r.content
        except Exception as e:
            log.warning("RSS fetch failed %s: %s", url, e)
            return []

    try:
        feed = feedparser.parse(raw)
    except Exception as e:
        log.warning("RSS parse failed %s: %s", url, e)
        return []

    messages: list[TgMessage] = []
    for entry in feed.entries:
        msg = _entry_to_message(source_name, entry, cutoff)
        if msg:
            messages.append(msg)

    log.info("RSS @%s: %d записей за window", source_name, len(messages))
    return messages


async def collect_rss(
    feeds: list[tuple[str, str]],  # [(source_name, url), ...]
    lookback_hours: int,
) -> list[TgMessage]:
    """Возвращает TgMessage-объекты из RSS-лент — совместимы с остальным пайплайном."""
    if not feeds:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    sem = asyncio.Semaphore(MAX_PARALLEL)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; tg-daily-digest/1.0; +https://github.com/)",
        "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9",
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        tasks = [_fetch_feed(client, url, name, cutoff, sem) for name, url in feeds]
        results = await asyncio.gather(*tasks)

    all_msgs: list[TgMessage] = []
    for msgs in results:
        all_msgs.extend(msgs)

    log.info("RSS итого: %d записей из %d лент", len(all_msgs), len(feeds))
    return all_msgs
