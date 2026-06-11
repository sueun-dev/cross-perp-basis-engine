from decimal import Decimal

from models import Leg, SymbolExposure
from state_store import load_exposures, save_exposures


def _leg(orphaned=False):
    return Leg(
        pacifica_side="long",
        pacifica_amount=Decimal("0.2"),
        extended_side="short",
        extended_amount=0.2,
        usd_size=Decimal("20.2"),
        entry_ratio=Decimal("0.01"),
        orphaned=orphaned,
    )


def test_load_missing_state_returns_empty(tmp_path):
    assert load_exposures(tmp_path / "missing.json") == {}


def test_save_and_load_exposures_round_trips_decimal_fields(tmp_path):
    path = tmp_path / "state" / "exposures.json"
    exposure = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    exposure.append_leg(_leg(orphaned=True))

    save_exposures({"BTC": exposure}, path)
    loaded = load_exposures(path)

    assert list(loaded) == ["BTC"]
    loaded_exposure = loaded["BTC"]
    assert loaded_exposure.direction == ("extended", "pacifica")
    assert loaded_exposure.extended_symbol == "BTC-USD"
    assert len(loaded_exposure.legs) == 1
    leg = loaded_exposure.legs[0]
    assert leg.pacifica_amount == Decimal("0.2")
    assert leg.usd_size == Decimal("20.2")
    assert leg.entry_ratio == Decimal("0.01")
    assert leg.orphaned is True


def test_save_exposures_drops_empty_symbols(tmp_path):
    path = tmp_path / "state" / "exposures.json"
    empty = SymbolExposure("ETH", "ETH-USD", ("extended", "pacifica"))

    save_exposures({"ETH": empty}, path)

    assert load_exposures(path) == {}
