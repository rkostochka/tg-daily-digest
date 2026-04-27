import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    openrouter_api_key: str
    target_chat_id: int
    channels: tuple
    lookback_hours: int
    llm_model: str

    @classmethod
    def from_env(cls) -> "Config":
        def req(key: str) -> str:
            v = os.environ.get(key, "").strip()
            if not v:
                raise RuntimeError(f"Не задана переменная окружения {key}")
            return v

        raw_channels = req("CHANNELS")
        channels = tuple(c.strip().lstrip("@") for c in raw_channels.split(",") if c.strip())

        return cls(
            bot_token=req("BOT_TOKEN"),
            openrouter_api_key=req("OPENROUTER_API_KEY"),
            target_chat_id=int(req("TARGET_CHAT_ID")),
            channels=channels,
            lookback_hours=int(os.environ.get("LOOKBACK_HOURS", "24")),
            llm_model=os.environ.get("LLM_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free"),
        )
