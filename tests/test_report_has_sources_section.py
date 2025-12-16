from __future__ import annotations

from app.services.validators import validate_report_has_sources


def test_sources_section_case_insensitive() -> None:
    assert validate_report_has_sources("ИСТОЧНИКИ\n- https://example.com") is True


