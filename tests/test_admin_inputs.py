"""The input helpers moved to core/inputs.py keep their behavior and their old import names."""

from __future__ import annotations

import pytest

from yokai_admin.core import inputs
from yokai_admin.core.formatting import format_number, format_signed
from yokai_admin.modules.access import service


def test_access_and_audit_still_expose_the_same_helpers_and_error_type() -> None:
    from yokai_admin.modules.audit import service as audit_service

    assert audit_service.parse_page is inputs.parse_page
    assert service.parse_user_id is inputs.parse_user_id
    assert service.clean_reason is inputs.clean_reason
    assert service.AccessRefused is inputs.InputRefused


@pytest.mark.parametrize("raw", ["", "abc", "١٢", "+5", "9" * 5000])
def test_user_id_validation_is_unchanged(raw: str) -> None:
    with pytest.raises(inputs.InputRefused):
        inputs.parse_user_id(raw)


@pytest.mark.parametrize(
    ("raw", "page"),
    [("", 1), ("abc", 1), ("0", 1), ("-3", 1), ("1.5", 1), ("9" * 5000, 1), ("7", 7), (" 12 ", 12)],
)
def test_page_parsing_is_unchanged(raw: str, page: int) -> None:
    assert inputs.parse_page(raw) == page


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0"), (999, "999"), (1_500, "1,500"), (-1_234_567, "-1,234,567"), (None, "–")],
)
def test_format_number(value: object, text: str) -> None:
    assert format_number(value) == text  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "text"), [(500, "+500"), (-250, "-250"), (0, "0"), (12_345, "+12,345")]
)
def test_format_signed(value: int, text: str) -> None:
    assert format_signed(value) == text
