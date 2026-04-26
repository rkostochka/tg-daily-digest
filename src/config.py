import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    tg_api_id: int
    tg_api_hash: str
    tg_session: str
    openrouter_api_key: str
    target_chat_id: int
    folders: tuple[str, ...]
    lookback_hours: int
    llm_model: str

    @classmethod
    def from_env(cls) -> "Config":
        def req(key: str) -> str:
            v = os.environ.get(key, "").strip()
            if not v:
                raise RuntimeError(f"Не задана переменная окружения {key}")
            return v

        return cls(
            tg_api_id=int(req("TG_API_ID")),
            tg_api_hash=req("TG_API_HASH"),
            tg_session=req("TG_SESSION"),
            openrouter_api_key=req("OPENROUTER_API_KEY"),
            target_chat_id=int(req("TARGET_CHAT_ID")),
            folders=tuple(f.strip() for f in req("FOLDERS").split(",") if f.strip()),
            lookback_hours=int(os.environ.get("LOOKBACK_HOURS", "24")),
            llm_model=os.environ.get("LLM_MODEL", "google/gemini-2.0-flash-exp:free"),
        )
