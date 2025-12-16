## DiplomatAI — Telegram-бот “AI-аналитик” (MVP)

Production-ready MVP бота на **Python 3.11+**, **aiogram 3.x**, **httpx**, **PostgreSQL (async SQLAlchemy)**, **Redis**.

Критичное требование: отчёт **“без выдумок”** — любые утверждения должны быть подтверждены источниками; при нехватке данных бот явно указывает пробелы.

### Быстрый старт (Docker)

1) Скопируйте `env.example` в `.env` и заполните секреты:

- `TELEGRAM_BOT_TOKEN`
- `PERPLEXITY_API_KEY`
- `OPENAI_API_KEY`

2) Запуск:

```bash
docker-compose up --build
```

### Переменные окружения

- **TELEGRAM_BOT_TOKEN**: токен Telegram-бота
- **PERPLEXITY_API_KEY**: ключ Perplexity
- **OPENAI_API_KEY**: ключ OpenAI
- **DATABASE_URL**: `postgresql+asyncpg://...`
- **REDIS_URL**: `redis://...`
- **OPENAI_MODEL**: например `gpt-4.1-mini`
- **PERPLEXITY_ENDPOINT / PERPLEXITY_MODEL**: endpoint/model Perplexity

### Команды бота

- **/start**: приветствие и примеры
- **/help**: формат отчётов
- **/history**: последние 5 отчётов (кнопки “Открыть”)
- **/limits**: остаток лимита на сутки (Free)

### Примечания по Perplexity

`app/services/perplexity_client.py` содержит **best-effort адаптер**. Если формат ответа Perplexity отличается — настройте извлечение `content/citations` (TODO-комментарий в коде).


