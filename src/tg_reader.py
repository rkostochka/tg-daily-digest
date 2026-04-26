"""Чтение сообщений из заданных Telegram-папок за окно lookback_hours."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import (
    DialogFilter,
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
    Message,
)

log = logging.getLogger(__name__)


@dataclass
class CollectedMessage:
    chat_title: str
    folder: str
    sender: str
    text: str
    urls: list[str] = field(default_factory=list)
    date: datetime | None = None
    message_link: str | None = None


def _peer_key(peer) -> tuple[str, int]:
    """Уникальный ключ пира для дедупликации в маппинге папок."""
    if isinstance(peer, InputPeerChannel):
        return ("channel", peer.channel_id)
    if isinstance(peer, InputPeerChat):
        return ("chat", peer.chat_id)
    if isinstance(peer, InputPeerUser):
        return ("user", peer.user_id)
    return ("other", id(peer))


def _extract_urls(message: Message) -> list[str]:
    urls: list[str] = []
    if message.entities:
        text = message.message or ""
        for ent in message.entities:
            cls = type(ent).__name__
            if cls == "MessageEntityUrl":
                urls.append(text[ent.offset : ent.offset + ent.length])
            elif cls == "MessageEntityTextUrl":
                url = getattr(ent, "url", None)
                if url:
                    urls.append(url)
    if message.web_preview and getattr(message.web_preview, "url", None):
        urls.append(message.web_preview.url)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def collect_messages(
    api_id: int,
    api_hash: str,
    session: str,
    folders_wanted: tuple[str, ...],
    lookback_hours: int,
) -> list[CollectedMessage]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    folders_lower = {f.lower() for f in folders_wanted}

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TG_SESSION недействителен — нужна повторная авторизация (auth.py)")

    try:
        filters_resp = await client(GetDialogFiltersRequest())
        # В новых версиях Telethon результат — объект DialogFilters, а не list
        raw_filters = getattr(filters_resp, "filters", filters_resp)

        peer_to_folder: dict[tuple[str, int], str] = {}
        for f in raw_filters:
            if not isinstance(f, DialogFilter):
                continue
            title = getattr(f.title, "text", None) or str(getattr(f, "title", ""))
            if title.lower() not in folders_lower:
                continue
            for p in (f.include_peers or []):
                peer_to_folder[_peer_key(p)] = title

        if not peer_to_folder:
            log.warning("Не найдено ни одной нужной папки. Доступные: %s",
                        [getattr(getattr(f, "title", None), "text", None) for f in raw_filters if isinstance(f, DialogFilter)])
            return []

        collected: list[CollectedMessage] = []
        async for dialog in client.iter_dialogs():
            input_peer = await client.get_input_entity(dialog.id)
            key = _peer_key(input_peer)
            folder = peer_to_folder.get(key)
            if folder is None:
                continue

            chat_title = dialog.name or "Без названия"
            try:
                async for msg in client.iter_messages(dialog.entity, offset_date=None, limit=300):
                    if msg.date and msg.date < cutoff:
                        break
                    text = (msg.message or "").strip()
                    urls = _extract_urls(msg)
                    if not text and not urls:
                        continue

                    sender_name = ""
                    try:
                        sender = await msg.get_sender()
                        if sender is not None:
                            sender_name = (
                                getattr(sender, "title", None)
                                or " ".join(filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]))
                                or getattr(sender, "username", None)
                                or ""
                            )
                    except Exception:
                        pass

                    link = None
                    if hasattr(dialog.entity, "username") and dialog.entity.username:
                        link = f"https://t.me/{dialog.entity.username}/{msg.id}"

                    collected.append(CollectedMessage(
                        chat_title=chat_title,
                        folder=folder,
                        sender=sender_name,
                        text=text,
                        urls=urls,
                        date=msg.date,
                        message_link=link,
                    ))
            except Exception as e:
                log.warning("Не смогли прочитать %s: %s", chat_title, e)

        log.info("Собрано %d сообщений из %d чатов", len(collected), len({m.chat_title for m in collected}))
        return collected
    finally:
        await client.disconnect()


async def send_digest(
    api_id: int,
    api_hash: str,
    session: str,
    target_chat_id: int,
    text: str,
) -> None:
    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    try:
        # Telegram ограничение: 4096 символов на сообщение. Бьём по абзацам.
        chunks: list[str] = []
        buf = ""
        for para in text.split("\n\n"):
            if len(buf) + len(para) + 2 > 4000:
                if buf:
                    chunks.append(buf)
                buf = para
            else:
                buf = (buf + "\n\n" + para) if buf else para
        if buf:
            chunks.append(buf)
        for i, chunk in enumerate(chunks):
            await client.send_message(target_chat_id, chunk, link_preview=False)
            if i == 0:
                log.info("Дайджест отправлен в %s", target_chat_id)
    finally:
        await client.disconnect()
