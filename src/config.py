import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Список каналов живёт в channels.txt в корне репозитория (по одному @username
# на строку). Переменная CHANNELS, если задана, переопределяет файл.
CHANNELS_FILE = Path(__file__).resolve().parent.parent / "channels.txt"

RSS_DEFAULTS = [
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("One Useful Thing", "https://www.oneusefulthing.org/feed"),
    ("Lilian Weng", "https://lilianweng.github.io/index.xml"),
    ("Import AI", "https://importai.substack.com/feed"),
    ("HuggingFace Blog", "https://huggingface.co/blog/feed.xml"),
    ("Pragmatic Engineer", "https://newsletter.pragmaticengineer.com/feed"),
]


def _load_channels(raw_env: str) -> tuple:
    """Каналы из переменной CHANNELS (если задана), иначе из channels.txt.

    В обоих случаях срезаются пробелы и ведущий @, дубли убираются с сохранением
    порядка. В файле игнорируются пустые строки и комментарии (#...).
    """
    if raw_env.strip():
        items = raw_env.split(",")
    elif CHANNELS_FILE.exists():
        items = [
            line for line in CHANNELS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    else:
        raise RuntimeError(
            "Список каналов пуст: задайте переменную CHANNELS или заполните channels.txt"
        )

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        ch = item.strip().lstrip("@")
        if ch and ch not in seen:
            seen.add(ch)
            result.append(ch)
    if not result:
        raise RuntimeError("Список каналов пуст после разбора CHANNELS/channels.txt")
    return tuple(result)


def _parse_rss_feeds(raw: str) -> list[tuple[str, str]]:
    """
    Парсит RSS_FEEDS формата "Name1|url1,Name2|url2,...".
    Если пусто — возвращает RSS_DEFAULTS.
    """
    raw = raw.strip()
    if not raw:
        return RSS_DEFAULTS
    result = []
    for item in raw.split(","):
        item = item.strip()
        if "|" in item:
            name, url = item.split("|", 1)
            result.append((name.strip(), url.strip()))
        elif item:
            result.append((item, item))
    return result or RSS_DEFAULTS


@dataclass(frozen=True)
class Config:
    bot_token: str
    openrouter_api_key: str
    target_chat_id: int
    channels: tuple
    lookback_hours: int
    llm_model: str
    rss_feeds: tuple  # tuple of (name, url)
    rss_lookback_hours: int

    @classmethod
    def from_env(cls) -> "Config":
        def req(key: str) -> str:
            v = os.environ.get(key, "").strip()
            if not v:
                raise RuntimeError(f"Не задана переменная окружения {key}")
            return v

        channels = _load_channels(os.environ.get("CHANNELS", ""))

        rss_feeds = tuple(_parse_rss_feeds(os.environ.get("RSS_FEEDS", "")))

        return cls(
            bot_token=req("BOT_TOKEN"),
            openrouter_api_key=req("OPENROUTER_API_KEY"),
            target_chat_id=int(req("TARGET_CHAT_ID")),
            channels=channels,
            lookback_hours=int(os.environ.get("LOOKBACK_HOURS", "24")),
            llm_model=os.environ.get("LLM_MODEL", "nousresearch/hermes-3-llama-3.1-405b:free"),
            rss_feeds=rss_feeds,
            rss_lookback_hours=int(os.environ.get("RSS_LOOKBACK_HOURS", "48")),
        )
