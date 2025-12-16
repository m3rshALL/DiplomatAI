from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SourceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    publisher: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    snippet: str | None = Field(default=None, max_length=2000)


class ClaimItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    claim_text: str = Field(min_length=1, max_length=2000)
    type: Literal["fact", "estimate"]
    date: datetime | None = None
    region: str | None = Field(default=None, max_length=100)
    sources: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=300)
    timeframe: str | None = Field(default=None, max_length=200)
    claims: list[ClaimItem] = Field(default_factory=list)
    sources: list[SourceItem] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ReportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_text: str = Field(min_length=1)
    sources_used: list[SourceItem] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


