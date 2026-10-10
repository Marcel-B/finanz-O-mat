from datetime import date
from decimal import Decimal

import pytest

from ustva.db import Datenbank
from ustva.waehrung import Umrechner


@pytest.fixture
def db():
    return Datenbank(":memory:")


@pytest.fixture
def umrechner(db):
    db.kurs_speichern("USD", "2026-09", Decimal("1.1600"), "BMF")
    return Umrechner(db, ezb_abrufen=False, heute=date(2026, 10, 10))
