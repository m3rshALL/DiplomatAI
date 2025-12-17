@echo off
REM Local testing script for DiplomatAI (Windows version)
REM Run this to test the bot locally before deploying

echo.
echo 🧪 DiplomatAI Local Testing Script
echo ==================================
echo.

REM Check if .env exists
if not exist .env (
    echo ❌ .env file not found!
    echo Please create .env from env.example:
    echo   copy env.example .env
    echo   REM Then edit .env and fill in your API keys
    exit /b 1
)

echo ✅ .env file found

echo.
echo 📋 Checking required environment variables...

findstr /C:"TELEGRAM_BOT_TOKEN=" .env >nul
if errorlevel 1 (
    echo   ❌ TELEGRAM_BOT_TOKEN is missing in .env
    exit /b 1
)
echo   ✅ TELEGRAM_BOT_TOKEN is set

findstr /C:"PERPLEXITY_API_KEY=" .env >nul
if errorlevel 1 (
    echo   ❌ PERPLEXITY_API_KEY is missing in .env
    exit /b 1
)
echo   ✅ PERPLEXITY_API_KEY is set

findstr /C:"OPENAI_API_KEY=" .env >nul
if errorlevel 1 (
    echo   ❌ OPENAI_API_KEY is missing in .env
    exit /b 1
)
echo   ✅ OPENAI_API_KEY is set

echo.
echo 🐳 Starting infrastructure (Postgres + Redis)...
docker-compose up -d postgres redis

echo.
echo ⏳ Waiting for databases to be healthy...
timeout /t 5 /nobreak >nul

:wait_postgres
docker exec diplomatai-postgres-1 pg_isready -U postgres >nul 2>&1
if errorlevel 1 (
    echo   Waiting for Postgres...
    timeout /t 2 /nobreak >nul
    goto wait_postgres
)
echo   ✅ Postgres is ready

:wait_redis
docker exec diplomatai-redis-1 redis-cli ping >nul 2>&1
if errorlevel 1 (
    echo   Waiting for Redis...
    timeout /t 2 /nobreak >nul
    goto wait_redis
)
echo   ✅ Redis is ready

echo.
echo 📊 Applying database indexes...
if exist migrations\001_add_indexes.sql (
    docker exec -i diplomatai-postgres-1 psql -U postgres -d diplomat < migrations\001_add_indexes.sql
    echo   ✅ Indexes applied
) else (
    echo   ⚠️  migrations\001_add_indexes.sql not found, skipping
)

echo.
echo 🧪 Running tests...
where pytest >nul 2>&1
if errorlevel 1 (
    echo   ⚠️  pytest not installed, skipping tests
    echo   Install with: pip install pytest pytest-asyncio
) else (
    pytest tests\ -v --tb=short
    echo   ✅ Tests passed
)

echo.
echo 🤖 Starting bot locally...
echo ==================================
echo.
echo The bot will start now. Press Ctrl+C to stop.
echo You can test it by sending messages to your bot in Telegram.
echo.
echo Available commands:
echo   /start - Show welcome message
echo   /help - Show help
echo   /history - View report history
echo   /limits - Check daily limits
echo   /focus ^<topic^> - Set focus for next queries
echo   /continue - Continue last report
echo   /clarify ^<instruction^> - Clarify last report
echo.
echo Or just send any text to generate a report!
echo.

python -m app.main
