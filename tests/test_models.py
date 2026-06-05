from decimal import Decimal

from models import Leg, SymbolExposure


def _leg(usd: str) -> Leg:
    return Leg(
        pacifica_side="long",
        pacifica_amount=Decimal("1"),
        extended_side="short",
        extended_amount=1.0,
        usd_size=Decimal(usd),
        entry_ratio=Decimal("0.01"),
    )


def test_total_usd_sums_legs():
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    assert exp.total_usd == Decimal("0")
    exp.append_leg(_leg("100"))
    exp.append_leg(_leg("50.5"))
    assert exp.total_usd == Decimal("150.5")


def test_pop_leg_returns_last_and_shrinks():
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    first = _leg("100")
    second = _leg("200")
    exp.append_leg(first)
    exp.append_leg(second)
    popped = exp.pop_leg()
    assert popped is second
    assert exp.legs == [first]


def test_pop_leg_on_empty_returns_none():
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    assert exp.pop_leg() is None


def test_clear_returns_copy_and_empties():
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    exp.append_leg(_leg("100"))
    exp.append_leg(_leg("200"))
    cleared = exp.clear()
    assert len(cleared) == 2
    assert exp.legs == []
    # Returned list is a snapshot, not a live view.
    cleared.clear()
    assert len(exp.legs) == 0
