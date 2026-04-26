"""Читаем сообщения с t.me/s/{username} без авторизации (только публичные каналы)."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx
from lxml import html

log = logging.getLogger(__name__)

TIMEOUT = 15.0
MAX_PARALLEL = 6
MAX_PAGES_PER_CHANNEL = 10   # не более ~200 сообщений на канал
MAX_MSG_PREVIEW = 2000       # символов на сообщение

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ru,en;q=0.9",
}


@dataclass
class TgMessage:
    channel: str
    text: str
    urls: list[str] = field(default_factory=list)
    date: datetime | None = None
    post_url: str | None = None


def _parse_page(channel: str, page_html: str, cutoff: datetime) -> tuple[list[TgMessage], int | None]:
    """
    Разбирает страницу t.me/s/channel.
    Возвращает (сообщения за нужный период, наименьший msg_id на странице для пагинации).
    Если все сообщения старше cutoff — возвращает пустой список.
    """
    tree = html.fromstring(page_html)
    messages: list[TgMessage] = []
    oldest_id: int | None = None

    for block in tree.cssselect("div.tgme_widget_message"):
        # --- дата ---
        time_el = block.cssselect("time[datetime]")
        if not time_el:
            continue
        try:
            msg_date = datetime.fromisoformat(time_el[0].get("datetime")).replace(tzinfo=timezone.utc)
        except Exception:
            continue

        # --- id для пагинации ---
        data_post = block.get("data-post", "")
        try:
            msg_id = int(data_post.rsplit("/", 1)[-1])
        except ValueError:
            msg_id = None
        if msg_id and (oldest_id is None or msg_id < oldest_id):
            oldest_id = msg_id

        if msg_date < cutoff:
            continue  # сообщение старше window — пропускаем, но продолжаем для пагинации

        # --- текст ---
        text_el = block.cssselect("div.tgme_widget_message_text")
        text = ""
        if text_el:
            text = (text_el[0].text_content() or "").strip()
        if len(text) > MAX_MSG_PREVIEW:
            text = text[:MAX_MSG_PREVIEW].rsplit(" ", 1)[0] + "…"

        # --- ссылки ---
        urls: list[str] = []
        seen_urls: set[str] = set()
        for a in block.cssselect("div.tgme_widget_message_text a[href]"):
            href = (a.get("href") or "").strip()
            if href and href not in seen_urls and not href.startswith("tg://"):
                seen_urls.add(href)
                urls.append(href)
        # web preview
        for a in block.cssselect("a.tgme_widget_message_link_preview[href]"):
            href = (a.get("href") or "").strip()
            if href and href not in seen_urls:
                seen_urls.add(href)
                urls.append(href)

        # --- прямая ссылка на пост ---
        post_url = f"https://t.me/{channel}/{msg_id}" if msg_id else None

        if not text and not urls:
            continue

        messages.append(TgMessage(
            channel=channel,
            text=text,
            urls=urls,
            date=msg_date,
            post_url=post_url,
        ))

    return messages, oldest_id


async def _fetch_channel(
    client: httpx.AsyncClient,
    channel: str,
    cutoff: datetime,
    sem: asyncio.Semaphore,
) -> list[TgMessage]:
    results: list[TgMessage] = []
    before_id: int | None = None

    for page_num in range(MAX_PAGES_PER_CHANNEL):
        url = f"https://t.me/s/{channel}"
        if before_id:
            url += f"?before={before_id}"

        async with sem:
            try:
                r = await client.get(url, timeout=TIMEOUT)
                r.raise_for_status()
                page_html = r.text
            except Exception as e:
                log.warning("Не смогли загрузить %s (стр.%d): %s", channel, page_num + 1, e)
                break

        msgs, oldest_id = _parse_page(channel, page_html, cutoff)

        # Если на странице нет ни одного валидного id → заглушка от Telegram (закрытый канал)
        if oldest_id is None and page_num == 0 and not msgs:
            log.warning("Канал @%s недоступен или приватный — пропускаем.", channel)
            break

        results.extend(msgs)

        # Если самый старый пост на странице моложе cutoff → нужно ещё страницы
        # Если oldest_id есть и все сообщения страницы уже за window — тоже стоп
        all_old = all(m.date is not None and m.date < cutoff for m in
                      _parse_page(channel, page_html, datetime.min.replace(tzinfo=timezone.utc))[0])
        if all_old or oldest_id is None:
            break
        before_id = oldest_id

    log.info("@%s: %d сообщений за window", channel, len(results))
    return results


async def collect_messages(
    channels: tuple,
    lookback_hours: int,
) -> list[TgMessage]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async with httpx.AsyncClient(headers=_HEADERS, http2=True, follow_redirects=True) as client:
        tasks = [_fetch_channel(client, ch, cutoff, sem) for ch in channels]
        results = await asyncio.gather(*tasks)

    all_msgs: list[TgMessage] = []
    for msgs in results:
        all_msgs.extend(msgs)

    log.info("Всего собрано %d сообщений из %d каналов", len(all_msgs), len(channels))
    return all_msgs
