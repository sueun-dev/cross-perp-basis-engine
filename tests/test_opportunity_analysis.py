from decimal import Decimal

import pytest

from opportunity_analysis import (
    compute_net_funding,
    evaluate_opportunities,
    funding_is_favorable,
)


def _ext(bid, ask, mid=None):
    return ("BTC-USD", {"best_bid": bid, "best_ask": ask, "mid_price": mid})


def test_evaluate_picks_extended_high_direction():
    # Extended bid (101) > Pacifica ask (100) -> sell on extended, buy on pacifica.
    ext = {"BTC": _ext(101.0, 102.0)}
    pac = {"BTC": {"best_bid": 99.0, "best_ask": 100.0}}
    opps = evaluate_opportunities(ext, pac)
    assert "BTC" in opps
    opp = opps["BTC"]
    assert opp.high_exchange == "extended"
    assert opp.low_exchange == "pacifica"
    # ratio = 101/100 - 1 = 0.01, computed precisely in Decimal.
    assert opp.ratio == Decimal("0.01")
    assert opp.sell_price == 101.0 and opp.buy_price == 100.0


def test_evaluate_picks_pacifica_high_direction():
    ext = {"BTC": _ext(99.0, 100.0)}
    pac = {"BTC": {"best_bid": 103.0, "best_ask": 104.0}}
    opps = evaluate_opportunities(ext, pac)
    opp = opps["BTC"]
    assert opp.high_exchange == "pacifica"
    assert opp.low_exchange == "extended"
    # ratio = 103/100 - 1 = 0.03
    assert opp.ratio == Decimal("0.03")


def test_evaluate_chooses_best_of_two_candidates():
    # Both directions positive; the larger spread must win.
    ext = {"BTC": _ext(110.0, 100.0)}   # ext bid 110 vs pac ask 100 -> +0.10
    pac = {"BTC": {"best_bid": 105.0, "best_ask": 100.0}}  # pac bid 105 vs ext ask 100 -> +0.05
    opp = evaluate_opportunities(ext, pac)["BTC"]
    assert opp.high_exchange == "extended"
    assert opp.ratio == Decimal("0.1")


def test_evaluate_skips_symbol_missing_on_pacifica():
    ext = {"ETH": _ext(101.0, 102.0)}
    pac = {}
    assert evaluate_opportunities(ext, pac) == {}


def test_evaluate_skips_non_positive_prices():
    ext = {"BTC": _ext(0.0, 0.0)}
    pac = {"BTC": {"best_bid": 0.0, "best_ask": 0.0}}
    assert evaluate_opportunities(ext, pac) == {}


@pytest.mark.parametrize(
    "high,short_rate,long_rate,expected",
    [
        ("extended", Decimal("0.0010"), Decimal("0.0002"), Decimal("0.0008")),
        ("pacifica", Decimal("0.0010"), Decimal("0.0002"), Decimal("0.0008")),
    ],
)
def test_compute_net_funding_sign(high, short_rate, long_rate, expected):
    # Net funding = funding earned on the short leg minus paid on the long leg.
    if high == "extended":
        pac_funding = {"BTC": long_rate}   # long pacifica (low exchange)
        ext_funding = {"BTC": short_rate}  # short extended (high exchange)
        low = "pacifica"
    else:
        pac_funding = {"BTC": short_rate}  # short pacifica (high exchange)
        ext_funding = {"BTC": long_rate}   # long extended (low exchange)
        low = "extended"
    net = compute_net_funding("BTC", high, low, pac_funding, ext_funding)
    assert net == expected


def test_compute_net_funding_missing_returns_none():
    assert compute_net_funding("BTC", "extended", "pacifica", {}, {"BTC": Decimal("0")}) is None


class _Opp:
    def __init__(self):
        self.base_symbol = "BTC"
        self.high_exchange = "extended"
        self.low_exchange = "pacifica"


def test_funding_is_favorable_true_when_net_positive():
    opp = _Opp()
    assert funding_is_favorable(opp, {"BTC": Decimal("0")}, {"BTC": Decimal("0.001")}) is True


def test_funding_is_favorable_false_when_net_negative():
    opp = _Opp()
    assert funding_is_favorable(opp, {"BTC": Decimal("0.002")}, {"BTC": Decimal("0.001")}) is False


def test_funding_is_favorable_false_when_missing():
    opp = _Opp()
    assert funding_is_favorable(opp, {}, {}) is False
