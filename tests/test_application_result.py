"""Tests for ApplicationResult Literal vocabulary validation (M1-T2).

The shared ``ApplicationResult`` model must reject out-of-vocabulary
``status``/``method`` values at construction time so typos like
``"cancled"`` or ``method="robot"`` are caught instead of silently
passing through. The canonical vocabulary lives as ``RESULT_*`` /
strategy-method constants; these tests pin the runtime behaviour.
"""

from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError

from backend.applier.manual_apply import ApplicationResult

VALID_STATUSES = ["applied", "assisted", "manual", "cancelled", "failed"]
VALID_METHODS = ["auto", "assisted", "manual"]


def test_typo_status_raises() -> None:
    with pytest.raises(ValidationError):
        ApplicationResult(status="cancled", method="auto")


def test_invalid_method_raises() -> None:
    with pytest.raises(ValidationError):
        ApplicationResult(status="applied", method="robot")


@pytest.mark.parametrize(
    ("status", "method"),
    list(itertools.product(VALID_STATUSES, VALID_METHODS)),
)
def test_all_valid_combinations_construct(status: str, method: str) -> None:
    result = ApplicationResult(status=status, method=method)
    assert result.status == status
    assert result.method == method
    assert result.message == ""


def test_message_defaults_and_overrides() -> None:
    assert ApplicationResult(status="applied", method="auto").message == ""
    result = ApplicationResult(status="manual", method="manual", message="hello")
    assert result.message == "hello"
