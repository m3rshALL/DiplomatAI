from __future__ import annotations

import pytest

from app.core.schemas import SourceItem


def test_sourceitem_url_validation_ok() -> None:
    s = SourceItem(url="https://example.com/x", title="Example")
    assert str(s.url) == "https://example.com/x"


def test_sourceitem_url_validation_fail() -> None:
    with pytest.raises(Exception):
        SourceItem(url="not-a-url", title="Bad")


