from __future__ import annotations

from pathlib import Path

import pytest

from financial_advisor.db.connection import connect, initialize
from financial_advisor.db.store import create_account, find_account

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """No test may read or write the real data directory: it holds personal data.

    Rules loading reads securities.local.yml from there, so without this a future
    overlay listing real holdings would silently leak into test runs.
    """
    monkeypatch.setenv("FA_DATA_DIR", str(tmp_path / "fa-data"))
    monkeypatch.delenv("FA_DATABASE", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A fresh database per test, redirected away from the real data directory."""
    monkeypatch.setenv("FA_DATA_DIR", str(tmp_path))
    path = initialize(tmp_path / "test.db")
    with connect(path) as conn:
        yield conn


@pytest.fixture()
def checking(db):
    create_account(db, name="Everyday Checking", type_code="checking", institution="Test Bank")
    return find_account(db, "Everyday Checking")


@pytest.fixture()
def card(db):
    create_account(db, name="Rewards Card", type_code="credit_card", institution="Test Bank")
    return find_account(db, "Rewards Card")
