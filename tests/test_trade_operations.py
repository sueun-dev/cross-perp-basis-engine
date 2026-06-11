from decimal import Decimal

import pytest

import trade_operations as ops
from models import Leg, Opportunity, SymbolExposure


def _opp(high_exchange, sell_price, buy_price, ratio="0.01"):
    return Opportunity(
        base_symbol="BTC",
        extended_symbol="BTC-USD",
        high_exchange=high_exchange,
        low_exchange="pacifica" if high_exchange == "extended" else "extended",
        sell_price=sell_price,
        buy_price=buy_price,
        ratio=Decimal(ratio),
    )


def test_compute_leg_extended_high(pacifica_stub, extended_stub):
    pacifica_stub.LOTS["BTC"] = Decimal("0.01")
    extended_stub.LOTS["BTC-USD"] = Decimal("0.001")
    # sell on extended @101 (best bid), buy on pacifica @100 (best ask)
    opp = _opp("extended", sell_price=101.0, buy_price=100.0)
    leg = ops.compute_trade_leg(opp, Decimal("20"))
    assert leg is not None
    assert leg.pacifica_side == "long" and leg.extended_side == "short"
    assert leg.pacifica_amount == Decimal("0.2")
    assert leg.extended_amount == pytest.approx(0.2)
    # usd_size is the larger of the two legs: 0.2 * 101 = 20.2
    assert leg.usd_size == Decimal("20.2")
    assert leg.entry_ratio == Decimal("0.01")


def test_compute_leg_pacifica_high(pacifica_stub, extended_stub):
    pacifica_stub.LOTS["BTC"] = Decimal("0.01")
    extended_stub.LOTS["BTC-USD"] = Decimal("0.001")
    # sell on pacifica @103 (best bid), buy on extended @100 (best ask)
    opp = _opp("pacifica", sell_price=103.0, buy_price=100.0)
    leg = ops.compute_trade_leg(opp, Decimal("20"))
    assert leg is not None
    assert leg.pacifica_side == "short" and leg.extended_side == "long"
    assert leg.pacifica_amount == Decimal("0.2")
    assert leg.extended_amount == pytest.approx(0.2)
    assert leg.usd_size == Decimal("20.6")  # 0.2 * 103


def test_compute_leg_converges_to_common_base(pacifica_stub, extended_stub):
    # Coarse pacifica lot forces both legs up to a base both venues accept.
    pacifica_stub.LOTS["BTC"] = Decimal("0.03")
    extended_stub.LOTS["BTC-USD"] = Decimal("0.001")
    opp = _opp("extended", sell_price=101.0, buy_price=100.0)
    leg = ops.compute_trade_leg(opp, Decimal("20"))
    assert leg is not None
    # 0.2 rounded up to a 0.03 multiple -> 0.21; both legs share that base.
    assert leg.pacifica_amount == Decimal("0.21")
    assert leg.extended_amount == pytest.approx(0.21)
    # Both are valid multiples of their venue step.
    assert (leg.pacifica_amount % Decimal("0.03")) == Decimal("0")
    assert (Decimal(str(leg.extended_amount)) % Decimal("0.001")) == Decimal("0")


def test_compute_leg_zero_notional_returns_none(pacifica_stub, extended_stub):
    opp = _opp("extended", sell_price=101.0, buy_price=100.0)
    assert ops.compute_trade_leg(opp, Decimal("0")) is None


def test_compute_leg_invalid_price_returns_none(pacifica_stub, extended_stub):
    opp = _opp("extended", sell_price=101.0, buy_price=0.0)
    assert ops.compute_trade_leg(opp, Decimal("20")) is None


def _make_leg():
    return Leg(
        pacifica_side="long",
        pacifica_amount=Decimal("0.2"),
        extended_side="short",
        extended_amount=0.2,
        usd_size=Decimal("20.2"),
        entry_ratio=Decimal("0.01"),
    )


def test_execute_open_leg_happy_path(pacifica_stub, extended_stub):
    ops.execute_open_leg("BTC", "BTC-USD", _make_leg())
    assert pacifica_stub.CALLS == [("open", "BTC", "long", Decimal("0.2"))]
    assert extended_stub.CALLS == [("open", "BTC-USD", "short", 0.2)]


def test_execute_open_leg_rolls_back_pacifica_when_extended_fails(pacifica_stub, extended_stub):
    extended_stub.RAISE_ON.add("open")
    with pytest.raises(RuntimeError):
        ops.execute_open_leg("BTC", "BTC-USD", _make_leg())
    # Pacifica leg opened, then rolled back (closed) after extended failed.
    assert pacifica_stub.CALLS == [
        ("open", "BTC", "long", Decimal("0.2")),
        ("close", "BTC", "long", Decimal("0.2")),
    ]


def test_close_all_legs_empties_on_success(pacifica_stub, extended_stub):
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    exp.append_leg(_make_leg())
    exp.append_leg(_make_leg())
    ops.close_all_legs(exp)
    assert exp.legs == []
    # Two legs closed on each venue.
    assert sum(1 for c in pacifica_stub.CALLS if c[0] == "close") == 2
    assert sum(1 for c in extended_stub.CALLS if c[0] == "close") == 2


def test_close_all_legs_keeps_leg_when_close_fails(pacifica_stub, extended_stub):
    # If the pacifica close fails, execute_close_leg re-raises and the leg is kept.
    pacifica_stub.RAISE_ON.add("close")
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    exp.append_leg(_make_leg())
    ops.close_all_legs(exp)
    assert len(exp.legs) == 1  # leg restored after failure


def test_exposure_accounting_helpers():
    exposures = {
        "BTC": SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica")),
        "ETH": SymbolExposure("ETH", "ETH-USD", ("pacifica", "extended")),
    }
    exposures["BTC"].append_leg(_make_leg())  # 20.2
    assert list(ops.active_symbols(exposures)) == ["BTC"]  # ETH has no legs
    assert ops.total_exposure_usd(exposures) == Decimal("20.2")


def test_assert_startup_flat_passes_when_both_venues_flat(pacifica_stub, extended_stub):
    pacifica_stub.POSITIONS = {"data": []}
    extended_stub.POSITIONS = {"data": []}
    ops.assert_startup_flat()


def test_assert_startup_flat_aborts_on_existing_pacifica_position(
    pacifica_stub, extended_stub
):
    pacifica_stub.POSITIONS = {
        "data": [{"symbol": "BTC", "side": "bid", "amount": "0.2"}]
    }
    extended_stub.POSITIONS = {"data": []}
    with pytest.raises(ops.StartupPositionError, match="Pacifica BTC bid 0.2"):
        ops.assert_startup_flat()


def test_assert_startup_flat_aborts_on_existing_extended_position(
    pacifica_stub, extended_stub
):
    pacifica_stub.POSITIONS = {"data": []}
    extended_stub.POSITIONS = {
        "data": [{"market": "BTC-USD", "side": "SHORT", "size": "0.2"}]
    }
    with pytest.raises(ops.StartupPositionError, match="Extended BTC-USD short 0.2"):
        ops.assert_startup_flat()


# --- Audit #1: a filled Pacifica leg must never be silently dropped ----------


def test_open_leg_orphans_when_hedge_and_rollback_both_fail(pacifica_stub, extended_stub):
    # Pacifica fills, Extended hedge fails, AND the Pacifica rollback also fails.
    extended_stub.RAISE_ON.add("open")
    pacifica_stub.RAISE_ON.add("close")  # rollback throws
    with pytest.raises(ops.OrphanedLegError) as excinfo:
        ops.execute_open_leg("BTC", "BTC-USD", _make_leg())
    orphan = excinfo.value.leg
    # The live, un-hedged Pacifica position is surfaced, not dropped.
    assert orphan.orphaned is True
    assert orphan.pacifica_side == "long"
    assert orphan.pacifica_amount == Decimal("0.2")
    assert orphan.extended_amount == 0.0  # extended never filled
    # It still carries USD size so it counts against risk caps.
    assert orphan.usd_size == Decimal("20.2")
    # Pacifica was opened then a rollback close was attempted (which threw).
    assert pacifica_stub.CALLS == [("open", "BTC", "long", Decimal("0.2"))]


def test_open_leg_clean_when_rollback_succeeds_does_not_orphan(pacifica_stub, extended_stub):
    # Hedge fails but rollback succeeds -> both venues flat -> plain raise, no orphan.
    extended_stub.RAISE_ON.add("open")
    with pytest.raises(Exception) as excinfo:
        ops.execute_open_leg("BTC", "BTC-USD", _make_leg())
    assert not isinstance(excinfo.value, ops.OrphanedLegError)


# --- Audit #2: a failed close must not restore a leg that is actually flat ----


def test_close_leg_orphans_extended_residual_when_reopen_fails(pacifica_stub, extended_stub):
    # Pacifica close succeeds, Extended close fails, Pacifica re-open then fails.
    extended_stub.RAISE_ON.add("close")
    pacifica_stub.RAISE_ON.add("open")  # re-open throws
    with pytest.raises(ops.OrphanedLegError) as excinfo:
        ops.execute_close_leg("BTC", "BTC-USD", _make_leg())
    residual = excinfo.value.leg
    # Pacifica is actually flat -> residual zeroes the Pacifica side so the next
    # pass will not double-close it; only the still-open Extended side remains.
    assert residual.orphaned is True
    assert residual.pacifica_amount == Decimal("0")
    assert residual.usd_size == Decimal("0")
    assert residual.extended_side == "short"
    # Pacifica got exactly one close (the successful one) then a failed re-open.
    assert ("close", "BTC", "long", Decimal("0.2")) in pacifica_stub.CALLS


def test_close_all_legs_records_reconciled_residual_not_pristine_leg(pacifica_stub, extended_stub):
    extended_stub.RAISE_ON.add("close")
    pacifica_stub.RAISE_ON.add("open")
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    exp.append_leg(_make_leg())
    ops.close_all_legs(exp)
    # The kept leg reflects venue truth (Pacifica flat), not the original both-open leg.
    assert len(exp.legs) == 1
    kept = exp.legs[0]
    assert kept.orphaned is True
    assert kept.pacifica_amount == Decimal("0")


# --- Reconciliation against live venue positions ------------------------------


def _orphan_leg():
    return Leg(
        pacifica_side="long",
        pacifica_amount=Decimal("0.2"),
        extended_side="short",
        extended_amount=0.0,
        usd_size=Decimal("20.2"),
        entry_ratio=Decimal("0.01"),
        orphaned=True,
    )


def test_reconcile_orphan_flattens_live_pacifica_and_returns_flat(pacifica_stub, extended_stub):
    # Venue truth: a naked long Pacifica position is still live; Extended flat.
    pacifica_stub.POSITIONS = {"data": [{"symbol": "BTC", "side": "bid", "amount": "0.2"}]}
    extended_stub.POSITIONS = {"data": []}
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    flat = ops.reconcile_orphan(exp, _orphan_leg())
    assert flat is True
    # The actual live size was closed reduce-only.
    assert ("close", "BTC", "long", Decimal("0.2")) in pacifica_stub.CALLS


def test_reconcile_orphan_keeps_leg_when_close_fails(pacifica_stub, extended_stub):
    pacifica_stub.POSITIONS = {"data": [{"symbol": "BTC", "side": "bid", "amount": "0.2"}]}
    pacifica_stub.RAISE_ON.add("close")  # cannot flatten this cycle
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    flat = ops.reconcile_orphan(exp, _orphan_leg())
    assert flat is False  # still live -> caller must retry


def test_reconcile_orphan_flat_when_nothing_live(pacifica_stub, extended_stub):
    # Both venues already report flat (e.g. the rollback actually went through).
    pacifica_stub.POSITIONS = {"data": []}
    extended_stub.POSITIONS = {"data": []}
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    flat = ops.reconcile_orphan(exp, _orphan_leg())
    assert flat is True
    # No close issued because nothing was live.
    assert all(c[0] != "close" for c in pacifica_stub.CALLS)


def test_reconcile_orphan_closes_live_extended_residual(pacifica_stub, extended_stub):
    # The #2 case: Pacifica flat, Extended short still live.
    pacifica_stub.POSITIONS = {"data": []}
    extended_stub.POSITIONS = {"data": [{"market": "BTC-USD", "side": "SHORT", "size": "0.2"}]}
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))
    residual = Leg(
        pacifica_side="long",
        pacifica_amount=Decimal("0"),
        extended_side="short",
        extended_amount=0.2,
        usd_size=Decimal("0"),
        entry_ratio=Decimal("0.01"),
        orphaned=True,
    )
    flat = ops.reconcile_orphan(exp, residual)
    assert flat is True
    assert ("close", "BTC-USD", "short", 0.2) in extended_stub.CALLS
