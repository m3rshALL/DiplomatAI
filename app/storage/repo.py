from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.schemas import Evidence, ReportResult
from app.storage.models import ReportRun


@dataclass(frozen=True)
class HistoryItem:
    id: uuid.UUID
    created_at_iso: str
    topic: str
    query: str


class ReportRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_run(
        self,
        *,
        user_id: int,
        query: str,
        topic: str,
        evidence: Evidence,
        evidence_table: Evidence,
        report: ReportResult,
    ) -> uuid.UUID:
        async with self._session_factory() as session:
            row = ReportRun(
                user_id=user_id,
                query=query,
                topic=topic,
                evidence_json=evidence.model_dump(mode="json"),
                evidence_table_json=evidence_table.model_dump(mode="json"),
                report_json=report.model_dump(mode="json"),
                report_text=report.report_text,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id

    async def get_history(self, *, user_id: int, limit: int = 5) -> list[HistoryItem]:
        async with self._session_factory() as session:
            stmt = (
                select(ReportRun)
                .where(ReportRun.user_id == user_id)
                .order_by(ReportRun.created_at.desc())
                .limit(limit)
            )
            res = await session.execute(stmt)
            rows = list(res.scalars().all())
            return [
                HistoryItem(
                    id=r.id,
                    created_at_iso=r.created_at.isoformat(),
                    topic=r.topic,
                    query=r.query,
                )
                for r in rows
            ]

    async def get_report_text(self, *, user_id: int, report_id: uuid.UUID) -> str | None:
        async with self._session_factory() as session:
            stmt = select(ReportRun.report_text).where(ReportRun.user_id == user_id, ReportRun.id == report_id)
            res = await session.execute(stmt)
            return res.scalar_one_or_none()

    async def get_last_report_text(self, *, user_id: int) -> str | None:
        async with self._session_factory() as session:
            stmt = (
                select(ReportRun.report_text)
                .where(ReportRun.user_id == user_id)
                .order_by(ReportRun.created_at.desc())
                .limit(1)
            )
            res = await session.execute(stmt)
            return res.scalar_one_or_none()
