"""Точка входа ежедневного запуска."""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config
from .link_fetcher import fetch_links
from .summarizer import make_digest
from .tg_reader import collect_messages, send_digest


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("digest")


async def run() -> None:
    cfg = Config.from_env()
    today = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
    log.info("Запуск дайджеста за %s, папки: %s", today, cfg.folders)

    messages = await collect_messages(
        api_id=cfg.tg_api_id,
        api_hash=cfg.tg_api_hash,
        session=cfg.tg_session,
        folders_wanted=cfg.folders,
        lookback_hours=cfg.lookback_hours,
    )

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
        api_id=cfg.tg_api_id,
        api_hash=cfg.tg_api_hash,
        session=cfg.tg_session,
        target_chat_id=cfg.target_chat_id,
        text=digest,
    )
    log.info("Готово.")


if __name__ == "__main__":
    asyncio.run(run())
