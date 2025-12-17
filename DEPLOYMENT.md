# DiplomatAI — Руководство по Запуску и Тестированию

## 🎯 Быстрый Старт

### Шаг 1: Подготовка Окружения

```bash
# 1. Клонируйте репозиторий (если ещё не сделали)
cd DiplomatAI

# 2. Создайте .env файл
copy env.example .env
# Или на Linux/Mac: cp env.example .env

# 3. Откройте .env и заполните обязательные переменные:
# TELEGRAM_BOT_TOKEN=your_bot_token_here
# PERPLEXITY_API_KEY=your_perplexity_key
# OPENAI_API_KEY=your_openai_key
```

**Как получить токены:**

- **TELEGRAM_BOT_TOKEN**: Напишите [@BotFather](https://t.me/BotFather) в Telegram, создайте бота командой `/newbot`
- **PERPLEXITY_API_KEY**: Зарегистрируйтесь на [perplexity.ai](https://www.perplexity.ai/)
- **OPENAI_API_KEY**: Получите на [platform.openai.com](https://platform.openai.com/api-keys)

---

### Шаг 2: Локальное Тестирование

**Windows:**
```cmd
scripts\test_local.bat
```

**Linux/Mac:**
```bash
chmod +x scripts/test_local.sh
./scripts/test_local.sh
```

Этот скрипт автоматически:
1. ✅ Проверит наличие `.env` файла
2. ✅ Запустит Postgres и Redis в Docker
3. ✅ Дождётся готовности баз данных
4. ✅ Применит database indexes
5. ✅ Запустит тесты (если pytest установлен)
6. ✅ Запустит бота локально

---

### Шаг 3: Тестирование Бота в Telegram

После запуска откройте вашего бота в Telegram и протестируйте:

**Основные команды:**
```
/start          - Приветствие и примеры
/help           - Справка по формату отчётов
/history        - Последние 5 отчётов
/limits         - Остаток лимита на сутки
```

**Дополнительные команды:**
```
/focus trade impact only    - Установить фокус на trade impact
/continue                   - Продолжить предыдущий отчёт
/clarify уточни методологию - Уточнить предыдущий отчёт
/app                        - Открыть Mini App
```

**Тестовые запросы:**
```
Аналитика по рынку СПГ в Европе за 12 месяцев
Краткий отчёт по динамике инфляции в РФ в 2024 с источниками
Обзор санкций ЕС против РФ: последние изменения
```

---

## 🧪 Полное Тестирование

### Проверка Всех Компонентов

#### 1. Telegram Bot ✅

**Тест команд:**
- [ ] `/start` — показывает приветствие и кнопки
- [ ] `/help` — показывает формат отчётов
- [ ] `/history` — показывает историю (или "История пуста")
- [ ] `/limits` — показывает лимиты (остаток/всего)

**Тест генерации отчёта:**
- [ ] Отправьте текстовый запрос → получите статус "Ищу источники…"
- [ ] Дождитесь "Формирую отчёт…"
- [ ] Получите готовый отчёт с источниками
- [ ] Проверьте, что в отчёте есть раздел "Источники"
- [ ] Проверьте, что факты имеют ссылки на источники

**Тест дополнительных функций:**
- [ ] `/focus trade impact` → отправьте запрос → проверьте что фокус учтён
- [ ] Сгенерируйте отчёт → `/continue` → получите продолжение
- [ ] `/clarify сделай короче` → получите уточнённую версию
- [ ] `/history` → нажмите "Открыть" на одном из отчётов

#### 2. Mini App (WebApp) ✅

**Запуск WebApp:**
```bash
# Получить HTTPS URL от Cloudflare Tunnel
docker-compose logs tunnel | grep "https://"

# Добавить URL в .env
# WEBAPP_URL=https://your-tunnel-url.trycloudflare.com

# Перезапустить сервисы
docker-compose restart bot web
```

**Тест WebApp:**
- [ ] `/app` в боте → нажать "Открыть Mini App"
- [ ] Webapp должен открыться внутри Telegram
- [ ] Введите запрос → нажмите "Сформировать отчёт"
- [ ] Статус должен обновляться: "Ищу источники…" → "Формирую отчёт…" → "Готово"
- [ ] Результат должен появиться на странице
- [ ] Если "отправить результат в чат" включен, отчёт должен прийти в чат
- [ ] Вкладка "История" → должны быть предыдущие отчёты
- [ ] Нажмите "Открыть" на любом отчёте → должен открыться текст

#### 3. Admin Panel ✅

**Доступ:**
```bash
# Админка доступна на http://localhost:8501
```

**Тест админки:**
- [ ] Открыть http://localhost:8501
- [ ] Вкладка "Обзор":
  - [ ] Метрики: Всего отчётов, За 24ч, Последний отчёт
  - [ ] Таблица "Последние отчёты" с текстами
  - [ ] "Топ пользователей" с user_id и количеством
  - [ ] "Лимиты (Redis)" - проверить для user_id
- [ ] Вкладка "Сервисы":
  - [ ] Postgres = OK
  - [ ] Redis = OK  
  - [ ] OpenAI = OK (или WARN/FAIL с пояснением)
  - [ ] Perplexity = OK
- [ ] Вкладка "Графики":
  - [ ] График "Отчёты/день"
  - [ ] График "Уникальные пользователи/день"
- [ ] Вкладка "Ошибки":
  - [ ] Список ошибок (если были)

---

## 📊 Мониторинг (Опционально)

### Добавление Prometheus + Grafana

```bash
# 1. Запустить мониторинг стек
docker-compose -f docker-compose.monitoring.yml up -d

# 2. Открыть Grafana
# URL: http://localhost:3000
# Login: admin
# Password: admin

# 3. Открыть дашборд "DiplomatAI Monitoring"
```

**Метрики для отслеживания:**
- Reports Generated (Total)
- Reports by Status (success/error/rate_limit)
- Report Generation Rate (per minute)
- Average Report Duration (p50, p95)
- API Call Success Rate (Perplexity vs OpenAI)
- API Latency
- Cache Hit Rate
- Active Users (24h)
- Error Rate by Type

---

## 🚀 Production Deployment

### Подготовка к Production

1. **Создать production .env:**
```bash
copy .env.production .env.prod
# Заполните:
# - Сильный пароль для DATABASE_URL
# - Production API ключи
# - WEBAPP_URL (ваш домен с HTTPS)
```

2. **Проверить настройки:**
```bash
# В .env.prod должно быть:
FREE_DAILY_LIMIT=10              # Увеличено для production
OPENAI_MODEL=gpt-4o              # Более качественная модель
PERPLEXITY_TIMEOUT_SECONDS=60.0  # Больше времени
OPENAI_TIMEOUT_SECONDS=120.0     # Больше времени
RETRY_MAX_ATTEMPTS=5             # Более агрессивный retry
```

3. **Деплой:**
```bash
chmod +x scripts/deploy_production.sh
./scripts/deploy_production.sh
```

Скрипт автоматически:
- ✅ Создаст backup базы данных
- ✅ Соберёт Docker images
- ✅ Запустит новые контейнеры
- ✅ Проверит health checks
- ✅ Применит миграции
- ✅ Покажет логи

---

## 🔍 Troubleshooting

### Проблема: Бот не отвечает

**Решение:**
```bash
# 1. Проверить логи
docker-compose logs bot

# 2. Проверить что бот запущен
docker-compose ps

# 3. Проверить TELEGRAM_BOT_TOKEN
# Отправьте запрос к Telegram API:
curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe

# Если ошибка - токен неверный
```

### Проблема: "Не удалось подключиться к инфраструктуре"

**Решение:**
```bash
# 1. Проверить что Postgres и Redis запущены
docker-compose ps postgres redis

# 2. Проверить health
docker exec diplomatai-postgres-1 pg_isready -U postgres
docker exec diplomatai-redis-1 redis-cli ping

# 3. Перезапустить инфраструктуру
docker-compose restart postgres redis
```

### Проблема: OpenAI/Perplexity ошибки

**Решение:**
```bash
# 1. Проверить API ключи в .env

# 2. Проверить баланс на OpenAI
# https://platform.openai.com/usage

# 3. Проверить лимиты на Perplexity
# Админка → Сервисы → OpenAI/Perplexity статус
```

### Проблема: Mini App не открывается

**Решение:**
```bash
# Windows (автоматически обновит WEBAPP_URL в .env и перезапустит bot):
scripts\refresh_webapp_url.bat

# 1. Получить текущий Cloudflare Tunnel URL
docker-compose logs tunnel | grep "https://"

# 2. Обновить WEBAPP_URL в .env
WEBAPP_URL=https://новый-url.trycloudflare.com

# 3. Перезапустить
docker-compose restart bot web

# 4. Проверить что Tunnel работает
curl https://ваш-url.trycloudflare.com/health
# Должно вернуть: ok
```

---

## ✅ Checklist Перед Production

### Инфраструктура
- [ ] Postgres пароль изменён с дефолтного
- [ ] Redis настроен с persistence (AOF + RDB)
- [ ] Backups настроены для Postgres
- [ ] SSL/HTTPS настроен для webapp
- [ ] Firewall rules настроены

### Безопасность
- [ ] Все пароли сильные
- [ ] API ключи в безопасности (не в Git!)
- [ ] Rate limiting включен (`DISABLE_RATE_LIMIT=false`)
- [ ] Логи не содержат чувствительной информации

### Мониторинг
- [ ] Prometheus собирает метрики
- [ ] Grafana дашборд настроен
- [ ] Alerts настроены (error rate, latency)
- [ ] Logs централизованы (опционально: ELK stack)

### Тестирование
- [ ] Все unit tests проходят
- [ ] Integration tests проходят
- [ ] Протестированы все команды бота
- [ ] Протестирован WebApp
- [ ] Протестирована админка
- [ ] Load testing выполнен (опционально)

### Документация
- [ ] README.md актуален
- [ ] .env.example содержит все переменные
- [ ] Runbook для incident response создан
- [ ] Контакты on-call инженера указаны

---

## 📚 Дополнительные Ресурсы

### Документация
- [Aiogram 3](https://docs.aiogram.dev/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Streamlit](https://docs.streamlit.io/)
- [Prometheus](https://prometheus.io/docs/)
- [Grafana](https://grafana.com/docs/)

### Полезные Команды

```bash
# Просмотр логов
docker-compose logs -f bot
docker-compose logs -f web
docker-compose logs -f admin

# Перезапуск сервиса
docker-compose restart bot

# Остановка всего
docker-compose down

# Очистка (осторожно! удалит данные)
docker-compose down -v

# Backup базы
docker exec diplomatai-postgres-1 pg_dump -U postgres diplomat > backup.sql

# Restore базы
cat backup.sql | docker exec -i diplomatai-postgres-1 psql -U postgres -d diplomat

# Применить миграции
docker exec -i diplomatai-postgres-1 psql -U postgres -d diplomat < migrations/001_add_indexes.sql
```

---

**Готово! Теперь ваш DiplomatAI бот полностью готов к работе.** 🎉

Если возникнут вопросы - проверьте раздел Troubleshooting или логи сервисов.
