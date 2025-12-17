-- Database indexes for DiplomatAI
-- Purpose: Improve query performance for common operations
-- Apply with: docker exec -i diplomatai-postgres-1 psql -U postgres -d diplomat < migrations/001_add_indexes.sql

-- Index for user history queries (most common query in bot)
-- Used by: GET /history, pipeline.get_history()
CREATE INDEX IF NOT EXISTS idx_report_runs_user_created 
ON report_runs(user_id, created_at DESC);

-- Index for admin dashboard (last reports)
-- Used by: admin panel recent runs
CREATE INDEX IF NOT EXISTS idx_report_runs_created 
ON report_runs(created_at DESC);

-- Full-text search index for topics (Russian language)
-- Used by: potential search functionality
CREATE INDEX IF NOT EXISTS idx_report_runs_topic_fts 
ON report_runs USING gin(to_tsvector('russian', topic));

-- Index for query search
CREATE INDEX IF NOT EXISTS idx_report_runs_query_fts 
ON report_runs USING gin(to_tsvector('russian', query));

-- Analyze tables to update statistics
ANALYZE report_runs;

-- Show index sizes
SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY pg_relation_size(indexrelid) DESC;
