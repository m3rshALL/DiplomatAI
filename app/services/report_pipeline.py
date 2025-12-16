from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

import redis.asyncio as redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.schemas import Evidence, ReportResult
from app.services.openai_client import OpenAIClient
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
        used = await get_daily_used(self.redis, user_id)
        limit = self.settings.free_daily_limit
        return max(0, limit - used), limit

    async def get_history(self, *, user_id: int):
        return await self.repo.get_history(user_id=user_id, limit=5)

    async def get_report_text(self, *, user_id: int, report_id: uuid.UUID) -> str | None:
        return await self.repo.get_report_text(user_id=user_id, report_id=report_id)

    async def run(self, *, user_id: int, query: str, stage_cb: StageCallback | None = None) -> str:
        if stage_cb is None:
            async def stage_cb(_: str) -> None:  # type: ignore[no-redef]
                return None

        used = await consume_daily_quota(self.redis, user_id)
        if used > self.settings.free_daily_limit:
            raise RateLimitExceeded("3 отчёта в сутки (Free)")

        await stage_cb("searching")

        # Perplexity cache
        ck = query_cache_key(query)
        cached = await cache_get_json(self.redis, ck)
        if cached is None:
            raw = await self.perplexity.fetch_raw(query)
            await cache_set_json(self.redis, ck, raw, ttl_seconds=self.settings.perplexity_cache_ttl_seconds)
        else:
            raw = cached

        evidence = self.perplexity.normalize(query, raw)

        evidence_table = await self.openai.generate_evidence_table(evidence)
        if not validate_claims_have_sources(evidence_table):
            # критично для "без выдумок"
            raise ValueError("Evidence table без источников у части утверждений")

        await stage_cb("generating")

        report = await self.openai.generate_report(evidence_table)
        issues = self._validate_report(report)
        if issues:
            report.quality_flags.extend([f"validator_fail:{x}" for x in issues])
            report = await self.openai.rewrite_report(evidence_table=evidence_table, report=report, issues=issues)
            issues2 = self._validate_report(report)
            if issues2:
                report.quality_flags.extend([f"validator_fail_after_rewrite:{x}" for x in issues2])

        report_id = await self.repo.save_run(
            user_id=user_id,
            query=query,
            topic=evidence_table.topic or evidence.topic,
            evidence=evidence,
            evidence_table=evidence_table,
            report=report,
        )

        log.info("report_ready", extra={"extra": {"report_id": str(report_id)}})

        # Telegram-friendly final text
        return report.report_text

    @staticmethod
    def _validate_report(report: ReportResult) -> list[str]:
        issues: list[str] = []
        if not validate_report_has_sources(report.report_text):
            issues.append("missing_sources_section")
        if not validate_summary_has_citations(report.report_text):
            issues.append("summary_without_citations")
        return issues



