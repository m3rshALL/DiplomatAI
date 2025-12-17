"""
Pytest configuration and shared fixtures for DiplomatAI tests.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock


# Allow `import app...` when running pytest from repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from app.core.config import Settings
from app.core.schemas import Evidence, EvidenceTable, ReportResult, ClaimItem, SourceItem


@pytest.fixture
def mock_settings():
    """Mock Settings object with test configuration."""
    return Settings(
        telegram_bot_token="test_token_123",
        perplexity_api_key="test_perplexity_key",
        openai_api_key="test_openai_key",
        database_url="postgresql+asyncpg://test:test@localhost/test_db",
        redis_url="redis://localhost:6379/1",
        free_daily_limit=3,
        disable_rate_limit=True,
        openai_model="gpt-4o-mini",
        perplexity_model="sonar-pro",
        perplexity_endpoint="https://api.perplexity.ai/chat/completions",
        perplexity_timeout_seconds=30.0,
        openai_timeout_seconds=60.0,
        retry_max_attempts=3,
        retry_base_delay_seconds=0.5,
        retry_max_delay_seconds=5.0,
        session_max_turns=3,
        session_ttl_seconds=3600,
        perplexity_cache_ttl_seconds=1800,
    )


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.incr = AsyncMock(return_value=1)
    mock.setex = AsyncMock(return_value=True)
    mock.expire = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.lrange = AsyncMock(return_value=[])
    mock.lpush = AsyncMock(return_value=1)
    return mock


@pytest.fixture
def mock_repo():
    """Mock ReportRepo."""
    mock = MagicMock()
    mock.get_history = AsyncMock(return_value=[])
    mock.get_report_text = AsyncMock(return_value=None)
    mock.get_last_report_text = AsyncMock(return_value=None)
    mock.save_run = AsyncMock(return_value="test-uuid-123")
    return mock


@pytest.fixture
def sample_evidence():
    """Sample Evidence object for testing."""
    return Evidence(
        query="Test query about AI",
        topic="Artificial Intelligence",
        claims=[
            ClaimItem(
                claim="AI is growing rapidly",
                sources=["https://example.com/source1"]
            )
        ],
        sources=[
            SourceItem(
                url="https://example.com/source1",
                title="AI Growth Report",
                snippet="AI technology is expanding..."
            )
        ]
    )


@pytest.fixture
def sample_evidence_table():
    """Sample EvidenceTable for testing."""
    return EvidenceTable(
        query="Test query about AI",
        topic="Artificial Intelligence",
        claims=[
            ClaimItem(
                claim="AI is growing rapidly",
                sources=["https://example.com/source1"]
            )
        ],
        sources=[
            SourceItem(
                url="https://example.com/source1",
                title="AI Growth Report",
                snippet="AI technology is expanding..."
            )
        ]
    )


@pytest.fixture
def sample_report():
    """Sample ReportResult for testing."""
    return ReportResult(
        report_text="""<b>Краткое резюме</b>
AI is growing rapidly [1].

<b>Источники</b>
[1] https://example.com/source1 - AI Growth Report""",
        quality_flags=[]
    )


@pytest.fixture
def mock_perplexity():
    """Mock PerplexityClient."""
    mock = MagicMock()
    mock.fetch_raw = AsyncMock(return_value={
        "choices": [{
            "message": {
                "content": "AI is growing rapidly",
                "citations": ["https://example.com/source1"]
            }
        }]
    })
    mock.normalize = MagicMock()
    return mock


@pytest.fixture
def mock_openai():
    """Mock OpenAIClient."""
    mock = MagicMock()
    mock.generate_evidence_table = AsyncMock()
    mock.generate_report = AsyncMock()
    mock.rewrite_report = AsyncMock()
    mock.followup_text = AsyncMock(return_value="Updated report text")
    return mock
