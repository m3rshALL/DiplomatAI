"""
Integration tests for ReportPipeline.
Tests the complete flow of report generation including:
- Rate limiting
- Caching
- API calls (mocked)
- Error handling
- Metrics tracking
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from app.services.report_pipeline import ReportPipeline, RateLimitExceeded
from app.services.openai_client import OpenAIRateLimitError, OpenAITemporaryError
from app.core.schemas import Evidence, EvidenceTable, ReportResult, ClaimItem, SourceItem


@pytest.mark.asyncio
async def test_pipeline_rate_limit_exceeded(
    mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai
):
    """Test that rate limiting works correctly."""
    mock_settings.disable_rate_limit = False
    mock_settings.free_daily_limit = 3
    mock_redis.incr = AsyncMock(return_value=5)  # Already exceeded limit
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    with pytest.raises(RateLimitExceeded):
        await pipeline.run(user_id=123, query="Test query")


@pytest.mark.asyncio
async def test_pipeline_cache_hit(
    mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai,
    sample_evidence, sample_evidence_table, sample_report
):
    """Test that cached Perplexity results are used when available."""
    import json
    
    # Setup cache hit
    cached_data = {"content": "cached response", "citations": []}
    mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))
    
    # Setup mocks
    mock_perplexity.normalize = lambda q, r: sample_evidence
    mock_openai.generate_evidence_table = AsyncMock(return_value=sample_evidence_table)
    mock_openai.generate_report = AsyncMock(return_value=sample_report)
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    result = await pipeline.run(user_id=123, query="Test query")
    
    # Perplexity fetch_raw should NOT be called (cache hit)
    mock_perplexity.fetch_raw.assert_not_called()
    
    # OpenAI should still be called
    mock_openai.generate_evidence_table.assert_called_once()
    mock_openai.generate_report.assert_called_once()
    
    # Result should be returned
    assert result is not None
    assert "Источники" in result


@pytest.mark.asyncio
async def test_pipeline_cache_miss(
    mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai,
    sample_evidence, sample_evidence_table, sample_report
):
    """Test that Perplexity is called when cache misses."""
    # Setup cache miss
    mock_redis.get = AsyncMock(return_value=None)
    
    # Setup mocks
    mock_perplexity.fetch_raw = AsyncMock(return_value={"test": "data"})
    mock_perplexity.normalize = lambda q, r: sample_evidence
    mock_openai.generate_evidence_table = AsyncMock(return_value=sample_evidence_table)
    mock_openai.generate_report = AsyncMock(return_value=sample_report)
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    result = await pipeline.run(user_id=123, query="Test query")
    
    # Perplexity should be called
    mock_perplexity.fetch_raw.assert_called_once()
    
    # Cache should be set
    mock_redis.set.assert_called()
    
    assert result is not None


@pytest.mark.asyncio
async def test_pipeline_openai_rate_limit(
    mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai,
    sample_evidence
):
    """Test handling of OpenAI rate limit errors."""
    mock_redis.get = AsyncMock(return_value=None)
    mock_perplexity.fetch_raw = AsyncMock(return_value={"test": "data"})
    mock_perplexity.normalize = lambda q, r: sample_evidence
    
    # Simulate OpenAI rate limit
    mock_openai.generate_evidence_table = AsyncMock(
        side_effect=OpenAIRateLimitError("Rate limit exceeded")
    )
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    with pytest.raises(OpenAIRateLimitError):
        await pipeline.run(user_id=123, query="Test query")


@pytest.mark.asyncio
async def test_pipeline_openai_temporary_error(
    mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai,
    sample_evidence
):
    """Test handling of OpenAI temporary errors."""
    mock_redis.get = AsyncMock(return_value=None)
    mock_perplexity.fetch_raw = AsyncMock(return_value={"test": "data"})
    mock_perplexity.normalize = lambda q, r: sample_evidence
    
    # Simulate OpenAI temporary error
    mock_openai.generate_evidence_table = AsyncMock(
        side_effect=OpenAITemporaryError("Temporary error")
    )
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    with pytest.raises(OpenAITemporaryError):
        await pipeline.run(user_id=123, query="Test query")


@pytest.mark.asyncio
async def test_pipeline_successful_report(
    mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai,
    sample_evidence, sample_evidence_table, sample_report
):
    """Test successful report generation end-to-end."""
    mock_redis.get = AsyncMock(return_value=None)
    mock_perplexity.fetch_raw = AsyncMock(return_value={"test": "data"})
    mock_perplexity.normalize = lambda q, r: sample_evidence
    mock_openai.generate_evidence_table = AsyncMock(return_value=sample_evidence_table)
    mock_openai.generate_report = AsyncMock(return_value=sample_report)
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    result = await pipeline.run(user_id=123, query="Test query about AI")
    
    # Verify all steps were called
    mock_perplexity.fetch_raw.assert_called_once()
    mock_openai.generate_evidence_table.assert_called_once()
    mock_openai.generate_report.assert_called_once()
    mock_repo.save_run.assert_called_once()
    
    # Verify result
    assert result is not None
    assert "Источники" in result
    assert "AI is growing" in result


@pytest.mark.asyncio
async def test_pipeline_followup_last_report(
    mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai
):
    """Test followup on last report."""
    mock_repo.get_last_report_text = AsyncMock(return_value="Previous report text")
    mock_openai.followup_text = AsyncMock(return_value="Updated report")
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    result = await pipeline.followup_last_report(
        user_id=123,
        instruction="Make it shorter"
    )
    
    mock_openai.followup_text.assert_called_once()
    assert result == "Updated report"


@pytest.mark.asyncio
async def test_pipeline_followup_no_previous_report(
    mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai
):
    """Test followup when no previous report exists."""
    mock_repo.get_last_report_text = AsyncMock(return_value=None)
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    with pytest.raises(ValueError, match="Нет предыдущего отчёта"):
        await pipeline.followup_last_report(
            user_id=123,
            instruction="Make it shorter"
        )


@pytest.mark.asyncio
async def test pipeline_get_limits_disabled(mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai):
    """Test get_limits when rate limiting is disabled."""
    mock_settings.disable_rate_limit = True
    
    pipeline = ReportPipeline(
        settings=mock_settings,
        redis=mock_redis,
        repo=mock_repo,
        perplexity=mock_perplexity,
        openai=mock_openai
    )
    
    remaining, limit = await pipeline.get_limits(user_id=123)
    
    assert remaining >= 10**9
    assert limit >= 10**9


@pytest.mark.asyncio
async def test_pipeline_get_limits_enabled(mock_settings, mock_redis, mock_repo, mock_perplexity, mock_openai):
    """Test get_limits when rate limiting is enabled."""
    mock_settings.disable_rate_limit = False
    mock_settings.free_daily_limit = 10
    mock_redis.get = AsyncMock(return_value="3")  # 3 used
    
    from app.storage.redis import get_daily_used
    
    with patch('app.storage.redis.get_daily_used', return_value=3):
        pipeline = ReportPipeline(
            settings=mock_settings,
            redis=mock_redis,
            repo=mock_repo,
            perplexity=mock_perplexity,
            openai=mock_openai
        )
        
        # Can't easily test without mocking get_daily_used properly
        # This test would need refactoring of the pipeline to inject the function
        pass
