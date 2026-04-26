"""Отправка дайджеста через Bot API."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_CHUNK = 4000  # Telegram лимит 4096, чуть меньше для запаса


async def send_digest(bot_token: str, chat_id: int, text: str) -> None:
    url = _API.format(token=bot_token, method="sendMessage")

    # Бьём по абзацам, не разрывая их посередине
    chunks: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        candidate = (buf + "\n\n" + para).lstrip() if buf else para
        if len(candidate) > _CHUNK:
            if buf:
                chunks.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        chunks.append(buf)

    async with httpx.AsyncClient(timeout=30.0) as c:
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            r = await c.post(url, json=payload)
            if r.status_code != 200:
                # Markdown иногда ломается из-за спецсимволов — ретраим без parse_mode
                payload.pop("parse_mode")
                r = await c.post(url, json=payload)
            r.raise_for_status()
            if i == 0:
                log.info("Дайджест отправлен в чат %s (%d частей)", chat_id, len(chunks))
