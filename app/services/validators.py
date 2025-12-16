from __future__ import annotations

import re

from app.core.schemas import Evidence


_RE_HAS_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_RE_CITATION = re.compile(r"\[\d+\]")


def validate_report_has_sources(report_text: str) -> bool:
    return "источники" in report_text.lower()


def validate_claims_have_sources(evidence_table: Evidence) -> bool:
    for c in evidence_table.claims:
        if not c.sources or not any(s.strip() for s in c.sources):
            return False
    return True


def validate_summary_has_citations(report_text: str) -> bool:
    # Best-effort: найдём секцию "Краткое резюме" до следующего заголовка или "Источники"
    low = report_text.lower()
    idx = low.find("краткое резюме")
    if idx == -1:
        return False
    tail = report_text[idx:]

    stop = len(tail)
    for marker in ("\n2)", "\n2.", "\nконтекст", "\nисточники"):
        j = tail.lower().find(marker)
        if j != -1:
            stop = min(stop, j)
    section = tail[:stop]

    bullets = []
    for line in section.splitlines():
        s = line.strip()
        if s.startswith(("-", "•", "*")):
            bullets.append(s)
    if not bullets:
        return False

    for b in bullets:
        if not (_RE_HAS_URL.search(b) or _RE_CITATION.search(b)):
            return False
    return True


