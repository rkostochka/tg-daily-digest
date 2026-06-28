"""Точка входа ежедневного запуска."""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config
from .link_fetcher import fetch_links
from .rss_reader import collect_rss
from .sender import send_digest
from .summarizer import make_digest
from .tme_reader import collect_messages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("digest")


async def run() -> None:
    cfg = Config.from_env()
    today = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
    log.info("Запуск дайджеста за %s, каналов: %d, RSS-лент: %d",
             today, len(cfg.channels), len(cfg.rss_feeds))

    tg_messages, rss_messages = await asyncio.gather(
        collect_messages(channels=cfg.channels, lookback_hours=cfg.lookback_hours),
        collect_rss(feeds=list(cfg.rss_feeds), lookback_hours=cfg.rss_lookback_hours),
    )
    messages = tg_messages + rss_messages

    all_urls: list[str] = []
    for m in messages:
        all_urls.extend(m.urls)
    link_texts = await fetch_links(all_urls)

    digest = await make_digest(
        api_key=cfg.openrouter_api_key,
        model=cfg.llm_model,
        messages=messages,
        link_texts=link_texts,
        date_label=today,
    )

    await send_digest(
        bot_token=cfg.bot_token,
        chat_id=cfg.target_chat_id,
        text=digest,
    )
    log.info("Готово.")


if __name__ == "__main__":
    asyncio.run(run())
