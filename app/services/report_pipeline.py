"""
Integration of Prometheus metrics into ReportPipeline.
Tracks report generation, API calls, cache operations, and latency.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

import redis.asyncio as redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import (
    REPORTS_GENERATED,
    API_CALLS,
    CACHE_OPERATIONS,
    REPORT_DURATION,
    PERPLEXITY_DURATION,
    OPENAI_DURATION,
)
from app.core.schemas import Evidence, ReportResult
from app.services.openai_client import OpenAIClient, OpenAIRateLimitError, OpenAITemporaryError
from app.services.perplexity_client import PerplexityClient
from app.services.validators import (
    validate_claims_have_sources,
    validate_report_has_sources,
    validate_summary_has_citations,
)
from app.storage.redis import (
    cache_get_json,
    cache_set_json,
    consume_daily_quota,
    get_daily_used,
    query_cache_key,
)
from app.storage.repo import ReportRepo


log = get_logger(__name__)


class RateLimitExceeded(Exception):
    pass


StageCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ReportPipeline:
    settings: Settings
    redis: redis.Redis
    repo: ReportRepo
    perplexity: PerplexityClient
    openai: OpenAIClient

    async def get_limits(self, *, user_id: int) -> tuple[int, int]:
        if self.settings.disable_rate_limit:
            # Для тестирования: считаем лимит отключённым
            return 10**9, 10**9
        used = await get_daily_used(self.redis, user_id)
        limit = self.settings.free_daily_limit
        return max(0, limit - used), limit

    async def get_history(self, *, user_id: int):
        return await self.repo.get_history(user_id=user_id, limit=5)

    async def get_report_text(self, *, user_id: int, report_id: uuid.UUID) -> str | None:
        return await self.repo.get_report_text(user_id=user_id, report_id=report_id)
    
    async def get_last_report_text(self, *, user_id: int) -> str | None:
        return await self.repo.get_last_report_text(user_id=user_id)

    async def followup_last_report(self, *, user_id: int, instruction: str) -> str:
        prev = await self.get_last_report_text(user_id=user_id)
        if not prev:
            raise ValueError("Нет предыдущего отчёта. Сначала сформируйте отчёт запросом.")
        
        start_time = time.time()
        try:
            result = await self.openai.followup_text(previous_report_text=prev, instruction=instruction)
            API_CALLS.labels(service='openai', status='success').inc()
            return result
        except OpenAIRateLimitError:
            API_CALLS.labels(service='openai', status='rate_limit').inc()
            raise
        except OpenAITemporaryError:
            API_CALLS.labels(service='openai', status='error').inc()
            raise
        finally:
            OPENAI_DURATION.observe(time.time() - start_time)

    async def run(self, *, user_id: int, query: str, stage_cb: StageCallback | None = None) -> str:
        report_start_time = time.time()
        
        if stage_cb is None:
            async def stage_cb(_: str) -> None:  # type: ignore[no-redef]
                return None

        # Rate limiting check
        if not self.settings.disable_rate_limit:
            used = await consume_daily_quota(self.redis, user_id)
            if used > self.settings.free_daily_limit:
                REPORTS_GENERATED.labels(status='rate_limit').inc()
                raise RateLimitExceeded(f"{self.settings.free_daily_limit} отчёта в сутки (Free)")

        await stage_cb("searching")

        # Perplexity with cache and metrics
        ck = query_cache_key(query)
        cached = await cache_get_json(self.redis, ck)
        
        if cached is None:
            CACHE_OPERATIONS.labels(operation='get', result='miss').inc()
            
            perplexity_start = time.time()
            try:
                raw = await self.perplexity.fetch_raw(query)
                API_CALLS.labels(service='perplexity', status='success').inc()
            except Exception as e:
                API_CALLS.labels(service='perplexity', status='error').inc()
                REPORTS_GENERATED.labels(status='perplexity_error').inc()
                raise
            finally:
                PERPLEXITY_DURATION.observe(time.time() - perplexity_start)
            
            await cache_set_json(self.redis, ck, raw, ttl_seconds=self.settings.perplexity_cache_ttl_seconds)
            CACHE_OPERATIONS.labels(operation='set', result='success').inc()
        else:
            CACHE_OPERATIONS.labels(operation='get', result='hit').inc()
            raw = cached

        evidence = self.perplexity.normalize(query, raw)

        # OpenAI evidence table with metrics
        openai_start = time.time()
        try:
            evidence_table = await self.openai.generate_evidence_table(evidence)
            API_CALLS.labels(service='openai', status='success').inc()
        except OpenAIRateLimitError:
            API_CALLS.labels(service='openai', status='rate_limit').inc()
            REPORTS_GENERATED.labels(status='openai_rate_limit').inc()
            raise
        except OpenAITemporaryError:
            API_CALLS.labels(service='openai', status='error').inc()
            REPORTS_GENERATED.labels(status='openai_error').inc()
            raise
        finally:
            OPENAI_DURATION.observe(time.time() - openai_start)
        
        if not validate_claims_have_sources(evidence_table):
            REPORTS_GENERATED.labels(status='validation_error').inc()
            raise ValueError("Evidence table без источников у части утверждений")

        await stage_cb("generating")

        # OpenAI report generation with metrics
        openai_start = time.time()
        try:
            report = await self.openai.generate_report(evidence_table)
            API_CALLS.labels(service='openai', status='success').inc()
        except OpenAIRateLimitError:
            API_CALLS.labels(service='openai', status='rate_limit').inc()
            REPORTS_GENERATED.labels(status='openai_rate_limit').inc()
            raise
        except OpenAITemporaryError:
            API_CALLS.labels(service='openai', status='error').inc()
            REPORTS_GENERATED.labels(status='openai_error').inc()
            raise
        finally:
            OPENAI_DURATION.observe(time.time() - openai_start)
        
        # Validation and potential rewrite
        issues = self._validate_report(report)
        if issues:
            report.quality_flags.extend([f"validator_fail:{x}" for x in issues])
            
            openai_start = time.time()
            try:
                report = await self.openai.rewrite_report(evidence_table=evidence_table, report=report, issues=issues)
                API_CALLS.labels(service='openai', status='success').inc()
            except Exception:
                API_CALLS.labels(service='openai', status='error').inc()
                raise
            finally:
                OPENAI_DURATION.observe(time.time() - openai_start)
            
            issues2 = self._validate_report(report)
            if issues2:
                report.quality_flags.extend([f"validator_fail_after_rewrite:{x}" for x in issues2])

        # Save to database
        report_id = await self.repo.save_run(
            user_id=user_id,
            query=query,
            topic=evidence_table.topic or evidence.topic,
            evidence=evidence,
            evidence_table=evidence_table,
            report=report,
        )

        # Track successful report
        REPORTS_GENERATED.labels(status='success').inc()
        REPORT_DURATION.observe(time.time() - report_start_time)
        
        log.info("report_ready", extra={"extra": {"report_id": str(report_id), "duration": time.time() - report_start_time}})

        return report.report_text

    @staticmethod
    def _validate_report(report: ReportResult) -> list[str]:
        issues: list[str] = []
        if not validate_report_has_sources(report.report_text):
            issues.append("missing_sources_section")
        if not validate_summary_has_citations(report.report_text):
            issues.append("summary_without_citations")
        return issues
