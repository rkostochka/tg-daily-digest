"""Однократная локальная авторизация в Telegram → выдаёт TG_SESSION для GitHub Secrets.

Запуск:
    python auth.py

Спросит TG_API_ID и TG_API_HASH (если они не в .env), затем номер телефона и код из SMS.
В конце выведет StringSession — скопируйте его в GitHub Secret TG_SESSION.
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    load_dotenv()
    api_id = os.environ.get("TG_API_ID") or input("TG_API_ID: ").strip()
    api_hash = os.environ.get("TG_API_HASH") or input("TG_API_HASH: ").strip()

    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        # Telethon сам спросит телефон / код / 2FA-пароль в интерактиве
        me = await client.get_me()
        print()
        print("=" * 70)
        print(f"Авторизованы как: {me.first_name} (id={me.id})")
        print("=" * 70)
        print("Скопируйте строку ниже в GitHub Secret TG_SESSION:")
        print()
        print(client.session.save())
        print()


if __name__ == "__main__":
    asyncio.run(main())
