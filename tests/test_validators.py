from __future__ import annotations

from datetime import datetime, timezone

from app.core.schemas import ClaimItem, Evidence
from app.services.validators import (
    validate_claims_have_sources,
    validate_report_has_sources,
    validate_summary_has_citations,
)


def test_validate_report_has_sources() -> None:
    assert validate_report_has_sources("...\nИсточники\n- https://example.com") is True
    assert validate_report_has_sources("без секции") is False


def test_validate_summary_has_citations() -> None:
    txt = """Краткое резюме
- Пункт 1 [1]
- Пункт 2 https://example.com

Источники
- https://example.com
"""
    assert validate_summary_has_citations(txt) is True

    bad = """Краткое резюме
- Пункт 1 без ссылок

Источники
- https://example.com
"""
    assert validate_summary_has_citations(bad) is False


def test_validate_claims_have_sources() -> None:
    ev = Evidence(
        topic="t",
        timeframe=None,
        claims=[
            ClaimItem(
                id="c1",
                claim_text="Факт",
                type="fact",
                date=datetime.now(timezone.utc),
                region=None,
                sources=["https://example.com/a"],
                confidence=0.9,
            )
        ],
        sources=[],
        gaps=[],
    )
    assert validate_claims_have_sources(ev) is True

    ev2 = ev.model_copy(deep=True)
    ev2.claims[0].sources = []
    assert validate_claims_have_sources(ev2) is False


