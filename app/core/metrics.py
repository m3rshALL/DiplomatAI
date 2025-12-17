"""
Prometheus metrics for DiplomatAI monitoring.

Metrics exported:
- diplomatai_reports_generated_total: Counter for total reports (by status)
- diplomatai_report_duration_seconds: Histogram for report generation time
- diplomatai_api_calls_total: Counter for external API calls (by service and status)
- diplomatai_telegram_messages_total: Counter for Telegram messages (by type)
- diplomatai_perplexity_duration_seconds: Histogram for Perplexity API latency
- diplomatai_openai_duration_seconds: Histogram for OpenAI API latency
- diplomatai_active_users_24h: Gauge for active users count
- diplomatai_redis_queue_size: Gauge for job queue size

Usage:
    from app.core.metrics import REPORTS_GENERATED, REPORT_DURATION
    
    REPORTS_GENERATED.labels(status='success').inc()
    with REPORT_DURATION.time():
        result = await generate_report()
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge

# Counters
REPORTS_GENERATED = Counter(
    'diplomatai_reports_generated_total',
    'Total number of reports generated',
    ['status']  # success, rate_limit, openai_error, perplexity_error, internal_error
)

API_CALLS = Counter(
    'diplomatai_api_calls_total',
    'Total external API calls',
    ['service', 'status']  # service: perplexity/openai, status: success/error/timeout/rate_limit
)

TELEGRAM_MESSAGES = Counter(
    'diplomatai_telegram_messages_total',
    'Total Telegram messages received',
    ['command_type']  # text, start, help, history, limits, focus, continue, clarify, callback
)

CACHE_OPERATIONS = Counter(
    'diplomatai_cache_operations_total',
    'Total cache operations',
    ['operation', 'result']  # operation: get/set, result: hit/miss/error
)

# Histograms (latency tracking)
REPORT_DURATION = Histogram(
    'diplomatai_report_duration_seconds',
    'Time to generate a complete report',
    buckets=[5, 10, 20, 30, 45, 60, 90, 120, 180, 240, 300]
)

PERPLEXITY_DURATION = Histogram(
    'diplomatai_perplexity_duration_seconds',
    'Perplexity API call duration',
    buckets=[1, 2, 5, 10, 15, 20, 30, 45, 60, 90]
)

OPENAI_DURATION = Histogram(
    'diplomatai_openai_duration_seconds',
    'OpenAI API call duration',
    buckets=[5, 10, 15, 20, 30, 45, 60, 90, 120, 180]
)

DB_QUERY_DURATION = Histogram(
    'diplomatai_db_query_duration_seconds',
    'Database query duration',
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5]
)

# Gauges (current state)
ACTIVE_USERS_24H = Gauge(
    'diplomatai_active_users_24h',
    'Number of unique active users in last 24 hours'
)

REDIS_QUEUE_SIZE = Gauge(
    'diplomatai_redis_queue_size',
    'Number of jobs currently in Redis queue'
)

TELEGRAM_QUEUE_SIZE = Gauge(
    'diplomatai_telegram_queue_size',
    'Number of updates in Telegram dispatcher queue'
)

TOTAL_REPORTS = Gauge(
    'diplomatai_total_reports',
    'Total number of reports in database'
)
