from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.prompts import SYSTEM_PROMPT_PERPLEXITY, USER_PROMPT_PERPLEXITY_TEMPLATE
from app.core.schemas import ClaimItem, Evidence, SourceItem


log = get_logger(__name__)


_RE_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Perplexity ответ не содержит JSON-объекта")
    return json.loads(text[start : end + 1])


@dataclass(frozen=True)
class PerplexityClient:
    api_key: str
    endpoint: str
    model: str
    timeout_seconds: float
    retry_max_attempts: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float
    use_response_format: bool = True

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )

    async def fetch_raw(self, query: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_PERPLEXITY},
                {"role": "user", "content": USER_PROMPT_PERPLEXITY_TEMPLATE.format(query=query)},
            ],
            "temperature": 0.0,
            "stream": False,
        }

        # В Perplexity retrieval может игнорировать system, поэтому правила поиска в user.
        # response_format опционален (можно выключить env PERPLEXITY_USE_RESPONSE_FORMAT=false)
        if self.use_response_format:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "evidence_search_result",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "sources": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "url": {"type": "string"},
                                        "title": {"type": "string"},
                                        "publisher": {"type": ["string", "null"]},
                                        "published_at": {"type": ["string", "null"]},
                                        "snippet": {"type": ["string", "null"]},
                                    },
                                    "required": ["url", "title"],
                                },
                            },
                            "claims": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "claim_text": {"type": "string"},
                                        "sources": {"type": "array", "items": {"type": "string"}},
                                    },
                                    "required": ["claim_text", "sources"],
                                },
                            },
                            "gaps": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["sources", "claims", "gaps"],
                    },
                },
            }

        async with self._client() as client:
            delay = self.retry_base_delay_seconds
            for attempt in range(1, self.retry_max_attempts + 1):
                try:
                    r = await client.post(self.endpoint, json=payload)
                    if r.status_code in (429,) or 500 <= r.status_code <= 599:
                        raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                    r.raise_for_status()
                    return r.json()
                except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as e:
                    if attempt >= self.retry_max_attempts:
                        raise
                    log.warning(
                        "perplexity_retry",
                        extra={"extra": {"attempt": attempt, "delay": delay, "err": str(e)}},
                    )
                    await asyncio.sleep(delay)
                    delay = min(self.retry_max_delay_seconds, delay * 2)

    def normalize(self, query: str, raw: dict[str, Any]) -> Evidence:
        # best-effort извлечение текста/цитат из разных форматов
        content = ""
        citations: list[str] = []

        try:
            content = (
                raw.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            ) or ""
        except Exception:
            content = ""

        # Если content — JSON (structured output), используем его напрямую
        if content:
            try:
                obj = _extract_json_object(content)
                sources_obj = obj.get("sources") or []
                claims_obj = obj.get("claims") or []
                gaps_obj = obj.get("gaps") or []

                sources: list[SourceItem] = []
                seen: set[str] = set()
                for s in sources_obj:
                    if not isinstance(s, dict):
                        continue
                    u = str(s.get("url") or "").strip()
                    if not u or u in seen:
                        continue
                    seen.add(u)
                    try:
                        sources.append(
                            SourceItem(
                                url=u,
                                title=str(s.get("title") or "Источник"),
                                publisher=(str(s.get("publisher")) if s.get("publisher") else None),
                                snippet=(str(s.get("snippet")) if s.get("snippet") else None),
                            )
                        )
                    except Exception:
                        continue

                claims: list[ClaimItem] = []
                for i, c in enumerate(claims_obj, start=1):
                    if not isinstance(c, dict):
                        continue
                    txt = str(c.get("claim_text") or "").strip()
                    srcs = c.get("sources") if isinstance(c.get("sources"), list) else []
                    srcs_norm = [str(x).strip() for x in srcs if str(x).strip()]
                    if not txt:
                        continue
                    claims.append(
                        ClaimItem(
                            id=f"p{i}",
                            claim_text=txt,
                            type="fact",
                            sources=srcs_norm,
                            confidence=None,
                        )
                    )

                gaps = [str(x) for x in gaps_obj if x]
                return Evidence(topic=query, timeframe=None, claims=claims, sources=sources, gaps=gaps)
            except Exception:
                # fallback ниже
                pass

        if isinstance(raw.get("citations"), list):
            citations = [str(x) for x in raw.get("citations") if x]

        # fallback: URL из текста
        if not citations and content:
            citations = _RE_URL.findall(content)

        sources: list[SourceItem] = []
        seen: set[str] = set()
        for u in citations:
            u = u.strip().rstrip(").,]")
            if not u or u in seen:
                continue
            seen.add(u)
            try:
                parsed = urllib.parse.urlparse(u)
                title = parsed.netloc or "Источник"
                sources.append(SourceItem(url=u, title=title))
            except Exception:
                continue

        gaps = []
        if not sources:
            gaps.append("Perplexity: не удалось надёжно извлечь источники из ответа.")
        if content:
            gaps.append(f"Perplexity raw_text_excerpt: {content[:800]}")
        gaps.append("Требуется извлечение фактов/утверждений в evidence table (шаг A).")

        return Evidence(topic=query, timeframe=None, claims=[], sources=sources, gaps=gaps)


