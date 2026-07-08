# tg-daily-digest

Ежедневный AI-дайджест из Telegram-каналов. Запускается на GitHub Actions в 8:00 МСК, читает публичные каналы за 24 часа, подтягивает текст по ссылкам, группирует по темам, дедуплицирует и присылает выжимку с блоком «Влияние» в указанную группу.

**Не требует API-ключей Telegram** — читает публичные каналы напрямую через `t.me/s/username`.

## Как это работает

1. `httpx + lxml` параллельно парсят `t.me/s/{channel}` за последние 24 часа.
2. `httpx + trafilatura` извлекают основной текст из внешних ссылок в постах.
3. Один вызов LLM на OpenRouter (`google/gemini-2.0-flash-exp:free`, 1M контекст): фильтрует мелочёвку, дедуплицирует, группирует по 7 темам, добавляет «Влияние».
4. Дайджест отправляется в группу через Bot API.

## Интерактивный deep-dive (ответы на вопросы)

Реплай на любой пункт дайджеста → бот отвечает по существу вопроса. Помимо самого
дайджеста он:

1. подтягивает **полный текст статьи** по ссылке из поста (`link_fetcher`);
2. при нехватке данных — делает **живой веб-поиск** недостающих фактов через
   Perplexity Sonar и **проверяет их**:
   - «железным» источникам (ЦБ РФ, Росстат, Минфин, Минпромторг, Минэк, ведомства,
     крупные аналитические/деловые издания, рейтинговые агентства) доверяет сразу;
   - прочим — только при совпадении факта в ≥2 независимых источниках, иначе помечает
     «не подтверждено».

Список «железных» доменов — в [`src/searcher.py`](src/searcher.py) (`TRUSTED_DOMAINS`).

> **Стоимость:** `SEARCH_MODEL` (`perplexity/sonar`) — платный, нужен ненулевой баланс
> OpenRouter (~$0.005–0.02 за вопрос). Без баланса поиск тихо отключается, бот
> отвечает по дайджесту/статьям. Полностью выключить: `ENABLE_WEB_SEARCH=0`.
> На Fly эти переменные задаются секретами: `fly secrets set ENABLE_WEB_SEARCH=1 SEARCH_MODEL=perplexity/sonar`.

## Тематические блоки

- 🌍 Геополитика и регуляторика
- 💰 Финансы и рынки
- 🤖 AI и технологии
- 🏢 Индустрия и продукт (martech / CDP / retention)
- 📊 Бизнес и управление
- 🔬 Наука и продуктивность
- 📌 Прочее значимое

Блок без значимых новостей пропускается. Логику группировки меняйте в [src/summarizer.py](src/summarizer.py) (`SYSTEM_PROMPT`).

## Первичная настройка

### 1. Список каналов

Список каналов хранится в файле [`channels.txt`](channels.txt) — по одному `@username`
на строку. Чтобы добавить/убрать канал, отредактируйте файл и закоммитьте (обычный PR).
Работают только **публичные** каналы (у которых есть @username, читаются через `t.me/s/`).

Как узнать username: в Telegram откройте канал → Profile → скопируйте `@username`.

> Переменная окружения/секрет `CHANNELS` (через запятую) при желании переопределяет
> файл — но по умолчанию она не нужна, источник правды — `channels.txt`.

### 2. Bot Token

Если у вас ещё нет токена: откройте `@BotFather` в Telegram → `/newbot` → придумайте имя → скопируйте токен `1234567890:ABC...`. Добавьте бота в вашу группу-приёмник и дайте права на отправку сообщений.

Если бот уже создан — найдите токен через `@BotFather` → `/mybots` → выберите бота → API Token.

### 3. OpenRouter API ключ

https://openrouter.ai → Sign in (Google/GitHub) → https://openrouter.ai/keys → Create Key. Бесплатно, лимит ~50 запросов/день (нам нужен 1/день).

### 4. Создать репозиторий на GitHub

На github.com → New repository → `tg-daily-digest`, Private, без README. Затем:

```bash
cd tg-daily-digest
git remote add origin git@github.com:<ваш-логин>/tg-daily-digest.git
git push -u origin main
```

### 5. Добавить секреты

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Значение |
|---|---|
| `BOT_TOKEN` | токен от BotFather |
| `TARGET_CHAT_ID` | `-1003957373164` |
| `OPENROUTER_API_KEY` | `sk-or-v1-...` |

Список каналов задаётся в [`channels.txt`](channels.txt), а не через секрет. Секрет
`CHANNELS` нужен, только если хотите переопределить файл, не коммитя его.

(опционально, в Variables: `LOOKBACK_HOURS`, `LLM_MODEL`)

### 6. Тест-прогон

Actions → daily-digest → Run workflow. Через 1–2 минуты дайджест появится в группе.

## Локальный запуск

```bash
cp .env.example .env
# заполните .env
source .venv/bin/activate
python -m src.main
```

## Ограничения

- **Только публичные каналы** — `t.me/s/` не работает для приватных групп и каналов без @username.
- **Не более ~200 последних постов** на канал за 24 часа (10 страниц по 20 постов). Для очень активных каналов этого достаточно.
- **GitHub Actions cron** может опаздывать на 5–30 минут. Для точного времени используйте внешний триггер (cron-job.org → workflow_dispatch).
- **Бесплатный OpenRouter**: ~50 запросов/день при $0 на счёте. Один запуск = 1 запрос.

## Структура

```
tg-daily-digest/
├── requirements.txt
├── .env.example
├── .github/workflows/daily.yml   # cron 8:00 МСК
└── src/
    ├── config.py        # env → dataclass
    ├── tme_reader.py    # парсит t.me/s/{channel}
    ├── link_fetcher.py  # качает текст по внешним ссылкам
    ├── summarizer.py    # промпт + OpenRouter
    ├── sender.py        # Bot API отправка
    └── main.py          # пайплайн
```

## Бесплатные альтернативные модели

Если `gemini-2.0-flash-exp:free` недоступна — поменяйте `LLM_MODEL` в Variables:

- `deepseek/deepseek-chat:free` — сильный reasoning, 64k контекст
- `meta-llama/llama-3.3-70b-instruct:free` — 128k контекст
- `qwen/qwen-2.5-72b-instruct:free` — 32k

Актуальный список: https://openrouter.ai/models?max_price=0
