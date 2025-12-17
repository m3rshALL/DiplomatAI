#!/bin/bash
# Production deployment script for DiplomatAI
# Handles deployment, health checks, and rollback

set -e

echo "🚀 DiplomatAI Production Deployment"
echo "===================================="
echo ""

# Check if .env.production exists
if [ ! -f .env.production ]; then
    echo "❌ .env.production file not found!"
    echo "Please create it from .env.production template"
    exit 1
fi

echo "✅ Production environment file found"

# Backup current deployment
echo ""
echo "💾 Creating backup..."
BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup database
echo "  Backing up database..."
docker exec diplomatai-postgres-1 pg_dump -U postgres diplomat > "$BACKUP_DIR/database.sql"
echo "  ✅ Database backed up to $BACKUP_DIR/database.sql"

# Pull latest code
echo ""
echo "📥 Pulling latest code..."
git pull origin main

# Build images
echo ""
echo "🔨 Building Docker images..."
docker-compose -f docker-compose.yml --env-file .env.production build

# Stop old containers
echo ""
echo "🛑 Stopping old containers..."
docker-compose down

# Start new containers
echo ""
echo "🚀 Starting new containers..."
docker-compose -f docker-compose.yml --env-file .env.production up -d

# Wait for health checks
echo ""
echo "🏥 Waiting for health checks..."
sleep 10

# Check postgres health
echo "  Checking Postgres..."
if docker exec diplomatai-postgres-1 pg_isready -U postgres > /dev/null 2>&1; then
    echo "  ✅ Postgres is healthy"
else
    echo "  ❌ Postgres health check failed!"
    echo "  Rolling back..."
    docker-compose down
    # Restore from backup
    cat "$BACKUP_DIR/database.sql" | docker exec -i diplomatai-postgres-1 psql -U postgres -d diplomat
    exit 1
fi

# Check redis health
echo "  Checking Redis..."
if docker exec diplomatai-redis-1 redis-cli ping > /dev/null 2>&1; then
    echo "  ✅ Redis is healthy"
else
    echo "  ❌ Redis health check failed!"
    exit 1
fi

# Check bot container
echo "  Checking Bot..."
if docker ps | grep -q diplomatai-bot-1; then
    echo "  ✅ Bot container is running"
else
    echo "  ❌ Bot container is not running!"
    docker-compose logs bot
    exit 1
fi

# Check webapp
echo "  Checking WebApp..."
sleep 5
if curl -f http://localhost:8080/health > /dev/null 2>&1; then
    echo "  ✅ WebApp is healthy"
else
    echo "  ⚠️  WebApp health check failed (may still be starting)"
fi

# Apply migrations
echo ""
echo "📊 Applying database migrations..."
if [ -f migrations/001_add_indexes.sql ]; then
    docker exec -i diplomatai-postgres-1 psql -U postgres -d diplomat < migrations/001_add_indexes.sql
    echo "  ✅ Migrations applied"
fi

# Show logs
echo ""
echo "📋 Recent logs:"
docker-compose logs --tail=20

echo ""
echo "✅ Deployment successful!"
echo ""
echo "Services:"
echo "  - Bot: Running"
echo "  - WebApp: http://localhost:8080"
echo "  - Admin: http://localhost:8501"
echo "  - Metrics: http://localhost:8080/metrics"
echo ""
echo "Get tunnel URL:"
echo "  docker-compose logs tunnel | grep https://"
echo ""
echo "Monitor logs:"
echo "  docker-compose logs -f"
echo ""
echo "Backup location: $BACKUP_DIR"
