# tg-daily-digest

Ежедневный AI-дайджест из ваших Telegram-папок. Запускается на GitHub Actions в 8:00 МСК, читает указанные папки за последние 24 часа, подтягивает текст по ссылкам, группирует по темам, дедуплицирует и присылает короткую выжимку с разделом «Влияние» в указанный чат.

## Как это работает

1. Telethon (MTProto под вашим аккаунтом) читает папки `Fin, Interests, Data, News`.
2. `httpx + trafilatura` параллельно качают и извлекают основной текст из ссылок в постах.
3. Один вызов LLM на OpenRouter (по умолчанию `google/gemini-2.0-flash-exp:free`, 1M контекст) — фильтрует мелочёвку, дедуплицирует, группирует по темам, добавляет «Влияние».
4. Дайджест отправляется в группу `TARGET_CHAT_ID` от вашего же аккаунта.

## Тематические блоки

- 🌍 Геополитика и регуляторика
- 💰 Финансы и рынки
- 🤖 AI и технологии
- 🏢 Индустрия и продукт (martech / CDP / retention)
- 📊 Бизнес и управление
- 🔬 Наука и продуктивность
- 📌 Прочее значимое

Блок без значимых новостей пропускается. Изменить набор блоков — в [src/summarizer.py](src/summarizer.py#L18) (`SYSTEM_PROMPT`).

## Первичная настройка (один раз, локально)

### 1. Получите Telegram API credentials

Перейдите на https://my.telegram.org → API development tools → создайте приложение. Запишите `api_id` и `api_hash`.

### 2. Получите OpenRouter API ключ

Зарегистрируйтесь на https://openrouter.ai, создайте ключ на https://openrouter.ai/keys. Бесплатные модели работают без пополнения, но лимит — ~50 запросов в день. Этого с запасом хватит на 1 запуск/сутки.

### 3. Локальная авторизация Telethon

```bash
cd tg-daily-digest
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Откройте .env и впишите TG_API_ID и TG_API_HASH
python auth.py
```

Скрипт спросит номер телефона и код из SMS, потом — пароль 2FA, если включён. В конце выведет длинную строку — это `TG_SESSION`. Скопируйте.

### 4. Создайте приватный репозиторий

На github.com → New repository → имя `tg-daily-digest`, Private, без README. Затем:

```bash
cd tg-daily-digest
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin git@github.com:<ваш-логин>/tg-daily-digest.git
git push -u origin main
```

### 5. Задайте секреты репозитория

Settings → Secrets and variables → Actions → New repository secret. Создайте по одному:

| Secret | Значение |
|---|---|
| `TG_API_ID` | из шага 1 |
| `TG_API_HASH` | из шага 1 |
| `TG_SESSION` | из шага 3 |
| `OPENROUTER_API_KEY` | из шага 2 |
| `TARGET_CHAT_ID` | `-1003957373164` |

(опционально, во вкладке Variables — если хотите менять без пересохранения секретов: `FOLDERS`, `LOOKBACK_HOURS`, `LLM_MODEL`.)

### 6. Тест-прогон

Actions → daily-digest → Run workflow. Должно отработать ~1–3 минуты, в группе появится дайджест.

Дальше будет запускаться сам в 8:00 МСК ежедневно.

## Локальный запуск (для отладки)

```bash
source .venv/bin/activate
python -m src.main
```

## Бесплатные модели OpenRouter (на случай смены)

Если `gemini-2.0-flash-exp:free` упирается в лимиты или возвращает ошибки — поменяйте `LLM_MODEL` в Variables:

- `deepseek/deepseek-chat:free` — сильный reasoning, 64k контекст
- `meta-llama/llama-3.3-70b-instruct:free` — 128k контекст
- `qwen/qwen-2.5-72b-instruct:free` — 32k

Список актуальных бесплатных моделей: https://openrouter.ai/models?max_price=0

## Ограничения и нюансы

- **Лимиты MTProto:** Telethon уважает FloodWait сам. Если ругается на flood — уменьшите `lookback_hours` или количество папок.
- **Лимиты OpenRouter free:** ~50 запросов в день при $0 на счёте. Один запуск = 1 запрос, так что норм.
- **Сессия может протухнуть:** если поменяете пароль 2FA или Telegram насильно разлогинит — повторите шаг 3 и обновите `TG_SESSION`.
- **GitHub Actions cron нестабилен:** может опаздывать на 5–30 минут под нагрузкой. Если нужна точность — используйте внешний планировщик (cron-job.org → workflow_dispatch).
- **Стоимость:** $0. GitHub Actions free tier для приватных репо — 2000 минут/месяц, 1 запуск ≈ 2 минуты, итого ~60 мин/месяц.

## Структура

```
tg-daily-digest/
├── auth.py                       # одноразовая локальная авторизация
├── requirements.txt
├── .env.example
├── .github/workflows/daily.yml   # cron-расписание
└── src/
    ├── config.py                 # env → dataclass
    ├── tg_reader.py              # MTProto: чтение папок + отправка дайджеста
    ├── link_fetcher.py           # async httpx + trafilatura
    ├── summarizer.py             # промпт + OpenRouter call
    └── main.py                   # пайплайн
```
