from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.prompts import SYSTEM_PROMPT_EVIDENCE, SYSTEM_PROMPT_FOLLOWUP, SYSTEM_PROMPT_REPORT, SYSTEM_PROMPT_REWRITE
from app.core.schemas import Evidence, ReportResult


log = get_logger(__name__)

class OpenAIRateLimitError(Exception):
    """OpenAI вернул 429 Too Many Requests и ретраи исчерпаны."""


class OpenAITemporaryError(Exception):
    """Временная ошибка OpenAI/сети (таймауты/5xx), ретраи исчерпаны."""



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
                    if r.status_code == 429:
                        # Часто 429 означает не только rate limit, но и недостаток квоты/нулевой баланс.
                        # В таком случае нет смысла ретраить — надо сразу показать пользователю причину.
                        err_code: str | None = None
                        err_msg: str | None = None
                        try:
                            j = r.json()
                            err = j.get("error") if isinstance(j, dict) else None
                            if isinstance(err, dict):
                                err_code = str(err.get("code") or "").strip() or None
                                err_msg = str(err.get("message") or "").strip() or None
                        except Exception:
                            err_code = None
                            err_msg = None

                        if err_code in {"insufficient_quota", "billing_hard_limit_reached"}:
                            raise OpenAIRateLimitError(
                                "OpenAI: недостаточно квоты/средств по ключу (0$ / quota). "
                                "Пополните баланс/включите биллинг и повторите."
                            )

                        if attempt >= self.retry_max_attempts:
                            raise OpenAIRateLimitError(
                                (err_msg or "OpenAI вернул 429 Too Many Requests (лимит). Попробуйте позже.")
                            )
                        retry_after = r.headers.get("retry-after")
                        try:
                            retry_after_s = float(retry_after) if retry_after else None
                        except ValueError:
                            retry_after_s = None
                        sleep_s = min(
                            self.retry_max_delay_seconds,
                            retry_after_s if retry_after_s is not None else delay,
                        )
                        log.warning(
                            "openai_retry",
                            extra={
                                "extra": {
                                    "attempt": attempt,
                                    "delay": sleep_s,
                                    "status_code": 429,
                                    "err": err_code or "rate_limited",
                                }
                            },
                        )
                        await asyncio.sleep(sleep_s)
                        delay = min(self.retry_max_delay_seconds, delay * 2)
                        continue

                    if 500 <= r.status_code <= 599:
                        if attempt >= self.retry_max_attempts:
                            raise OpenAITemporaryError(
                                f"OpenAI временно недоступен (HTTP {r.status_code}). Попробуйте позже."
                            )
                        log.warning(
                            "openai_retry",
                            extra={
                                "extra": {
                                    "attempt": attempt,
                                    "delay": delay,
                                    "status_code": r.status_code,
                                    "err": "server_error",
                                }
                            },
                        )
                        await asyncio.sleep(delay)
                        delay = min(self.retry_max_delay_seconds, delay * 2)
                        continue
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
                        raise OpenAITemporaryError("OpenAI/сеть: ошибка запроса. Попробуйте позже.") from e
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
    
    async def followup_text(self, *, previous_report_text: str, instruction: str, temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_FOLLOWUP},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"instruction": instruction, "previous_report_text": previous_report_text},
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            "temperature": temperature,
        }

        async with self._client() as client:
            delay = self.retry_base_delay_seconds
            for attempt in range(1, self.retry_max_attempts + 1):
                try:
                    r = await client.post("/v1/chat/completions", json=payload)

                    if r.status_code == 429:
                        err_code: str | None = None
                        err_msg: str | None = None
                        try:
                            j = r.json()
                            err = j.get("error") if isinstance(j, dict) else None
                            if isinstance(err, dict):
                                err_code = str(err.get("code") or "").strip() or None
                                err_msg = str(err.get("message") or "").strip() or None
                        except Exception:
                            err_code = None
                            err_msg = None

                        if err_code in {"insufficient_quota", "billing_hard_limit_reached"}:
                            raise OpenAIRateLimitError(
                                "OpenAI: недостаточно квоты/средств по ключу (0$ / quota). "
                                "Пополните баланс/включите биллинг и повторите."
                            )

                        if attempt >= self.retry_max_attempts:
                            raise OpenAIRateLimitError(err_msg or "OpenAI вернул 429 Too Many Requests (лимит).")

                        retry_after = r.headers.get("retry-after")
                        try:
                            retry_after_s = float(retry_after) if retry_after else None
                        except ValueError:
                            retry_after_s = None
                        sleep_s = min(
                            self.retry_max_delay_seconds,
                            retry_after_s if retry_after_s is not None else delay,
                        )
                        log.warning(
                            "openai_retry",
                            extra={
                                "extra": {
                                    "attempt": attempt,
                                    "delay": sleep_s,
                                    "status_code": 429,
                                    "err": err_code or "rate_limited",
                                }
                            },
                        )
                        await asyncio.sleep(sleep_s)
                        delay = min(self.retry_max_delay_seconds, delay * 2)
                        continue

                    if 500 <= r.status_code <= 599:
                        if attempt >= self.retry_max_attempts:
                            raise OpenAITemporaryError(
                                f"OpenAI временно недоступен (HTTP {r.status_code}). Попробуйте позже."
                            )
                        log.warning(
                            "openai_retry",
                            extra={
                                "extra": {
                                    "attempt": attempt,
                                    "delay": delay,
                                    "status_code": r.status_code,
                                    "err": "server_error",
                                }
                            },
                        )
                        await asyncio.sleep(delay)
                        delay = min(self.retry_max_delay_seconds, delay * 2)
                        continue

                    r.raise_for_status()
                    data = r.json()
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    ) or ""
                    return str(content).strip()
                except (httpx.TimeoutException, httpx.RequestError) as e:
                    if attempt >= self.retry_max_attempts:
                        raise OpenAITemporaryError("OpenAI/сеть: ошибка запроса. Попробуйте позже.") from e
                    log.warning(
                        "openai_retry",
                        extra={"extra": {"attempt": attempt, "delay": delay, "err": str(e)}},
                    )
                    await asyncio.sleep(delay)
                    delay = min(self.retry_max_delay_seconds, delay * 2)

        raise RuntimeError("unreachable")


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


