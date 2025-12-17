#!/bin/bash
# Local testing script for DiplomatAI
# Run this to test the bot locally before deploying

set -e

echo "🧪 DiplomatAI Local Testing Script"
echo "=================================="
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo "Please create .env from env.example:"
    echo "  cp env.example .env"
    echo "  # Then edit .env and fill in your API keys"
    exit 1
fi

echo "✅ .env file found"

# Check required environment variables
echo ""
echo "📋 Checking required environment variables..."

required_vars=("TELEGRAM_BOT_TOKEN" "PERPLEXITY_API_KEY" "OPENAI_API_KEY")

for var in "${required_vars[@]}"; do
    if grep -q "^${var}=" .env && ! grep -q "^${var}=$" .env; then
        echo "  ✅ $var is set"
    else
        echo "  ❌ $var is missing or empty in .env"
        exit 1
    fi
done

echo ""
echo "🐳 Starting infrastructure (Postgres + Redis)..."
docker-compose up -d postgres redis

echo ""
echo "⏳ Waiting for databases to be healthy..."
sleep 5

# Check if postgres is ready
until docker exec diplomatai-postgres-1 pg_isready -U postgres > /dev/null 2>&1; do
    echo "  Waiting for Postgres..."
    sleep 2
done
echo "  ✅ Postgres is ready"

# Check if redis is ready
until docker exec diplomatai-redis-1 redis-cli ping > /dev/null 2>&1; do
    echo "  Waiting for Redis..."
    sleep 2
done
echo "  ✅ Redis is ready"

echo ""
echo "📊 Applying database indexes..."
if [ -f migrations/001_add_indexes.sql ]; then
    docker exec -i diplomatai-postgres-1 psql -U postgres -d diplomat < migrations/001_add_indexes.sql
    echo "  ✅ Indexes applied"
else
    echo "  ⚠️  migrations/001_add_indexes.sql not found, skipping"
fi

echo ""
echo "🧪 Running tests..."
if command -v pytest &> /dev/null; then
    pytest tests/ -v --tb=short
    echo "  ✅ Tests passed"
else
    echo "  ⚠️  pytest not installed, skipping tests"
    echo "  Install with: pip install pytest pytest-asyncio"
fi

echo ""
echo "🤖 Starting bot locally..."
echo "=================================="
echo ""
echo "The bot will start now. Press Ctrl+C to stop."
echo "You can test it by sending messages to your bot in Telegram."
echo ""
echo "Available commands:"
echo "  /start - Show welcome message"
echo "  /help - Show help"
echo "  /history - View report history"
echo "  /limits - Check daily limits"
echo "  /focus <topic> - Set focus for next queries"
echo "  /continue - Continue last report"
echo "  /clarify <instruction> - Clarify last report"
echo ""
echo "Or just send any text to generate a report!"
echo ""

python -m app.main
