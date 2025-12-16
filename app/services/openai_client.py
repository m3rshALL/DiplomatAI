from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.prompts import SYSTEM_PROMPT_EVIDENCE, SYSTEM_PROMPT_REPORT, SYSTEM_PROMPT_REWRITE
from app.core.schemas import Evidence, ReportResult


log = get_logger(__name__)


def _extract_json_object(text: str) -> dict[str, Any]:
    # best-effort: достанем JSON-объект даже если модель "обрамляет" его текстом
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("OpenAI ответ не содержит JSON-объекта")
    chunk = text[start : end + 1]
    return json.loads(chunk)


@dataclass(frozen=True)
class OpenAIClient:
    api_key: str
    model: str
    timeout_seconds: float
    retry_max_attempts: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.openai.com",
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )

    async def _chat_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }

        async with self._client() as client:
            delay = self.retry_base_delay_seconds
            for attempt in range(1, self.retry_max_attempts + 1):
                try:
                    r = await client.post("/v1/chat/completions", json=payload)
                    if r.status_code in (429,) or 500 <= r.status_code <= 599:
                        raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                    r.raise_for_status()
                    data = r.json()
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    ) or ""
                    return _extract_json_object(content)
                except (
                    httpx.TimeoutException,
                    httpx.HTTPStatusError,
                    httpx.RequestError,
                    json.JSONDecodeError,
                    ValueError,
                ) as e:
                    if attempt >= self.retry_max_attempts:
                        raise
                    log.warning(
                        "openai_retry",
                        extra={"extra": {"attempt": attempt, "delay": delay, "err": str(e)}},
                    )
                    await asyncio.sleep(delay)
                    delay = min(self.retry_max_delay_seconds, delay * 2)

        raise RuntimeError("unreachable")

    async def generate_evidence_table(self, evidence: Evidence) -> Evidence:
        obj = await self._chat_json(
            system=SYSTEM_PROMPT_EVIDENCE,
            user=evidence.model_dump_json(),
            temperature=0.0,
        )
        return Evidence.model_validate(obj)

    async def generate_report(self, evidence_table: Evidence) -> ReportResult:
        obj = await self._chat_json(
            system=SYSTEM_PROMPT_REPORT,
            user=evidence_table.model_dump_json(),
            temperature=0.2,
        )
        return ReportResult.model_validate(obj)

    async def rewrite_report(self, *, evidence_table: Evidence, report: ReportResult, issues: list[str]) -> ReportResult:
        user = json.dumps(
            {
                "issues": issues,
                "evidence_table": evidence_table.model_dump(mode="json"),
                "current_report": report.model_dump(mode="json"),
            },
            ensure_ascii=False,
            default=str,
        )
        obj = await self._chat_json(
            system=SYSTEM_PROMPT_REWRITE,
            user=user,
            temperature=0.0,
        )
        return ReportResult.model_validate(obj)


