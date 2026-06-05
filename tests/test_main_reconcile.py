"""Integration tests for the orphaned-leg lifecycle in ``main``.

These prove the audit-#1/#2/#3 fixes end to end: a leg that fills on Pacifica
but whose hedge (and rollback) fail is *recorded* and *counted in risk caps*
rather than silently dropped, and is then force-reconciled against actual venue
positions on the following cycle.

``trade_operations``/``main`` import the two exchange clients at module load;
``conftest`` installs lightweight stubs into ``sys.modules`` first.
"""

from decimal import Decimal

import main
import trade_operations as ops
from models import Leg, SymbolExposure


def _leg():
    return Leg(
        pacifica_side="long",
        pacifica_amount=Decimal("0.2"),
        extended_side="short",
        extended_amount=0.2,
        usd_size=Decimal("20.2"),
        entry_ratio=Decimal("0.01"),
    )


def test_evaluate_entries_records_orphan_and_counts_in_caps(
    monkeypatch, pacifica_stub, extended_stub
):
    """Audit #1/#3: a naked Pacifica fill is tracked and counted, not dropped."""
    # Force execute_open_leg to report an orphaned (live, un-hedged) Pacifica leg.
    orphan = ops.Leg(
        pacifica_side="long",
        pacifica_amount=Decimal("0.2"),
        extended_side="short",
        extended_amount=0.0,
        usd_size=Decimal("20.2"),
        entry_ratio=Decimal("0.01"),
        orphaned=True,
    )

    def fake_open(symbol, extended_symbol, leg):
        raise ops.OrphanedLegError(orphan, RuntimeError("rollback failed"))

    monkeypatch.setattr(main, "execute_open_leg", fake_open)

    exposures: dict = {}
    exp = SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))

    # Drive the single-symbol path of _evaluate_entries by calling the same
    # bookkeeping it performs. We invoke the real function with a hand-built
    # opportunity to exercise the except-branch.
    from models import Opportunity

    opp = Opportunity(
        base_symbol="BTC",
        extended_symbol="BTC-USD",
        high_exchange="extended",
        low_exchange="pacifica",
        sell_price=101.0,
        buy_price=100.0,
        ratio=Decimal("0.05"),  # well above ENTRY_THRESHOLD
    )

    # Funding favourable for this direction (short extended - long pacifica >= 0).
    pac_funding = {"BTC": Decimal("0")}
    ext_funding = {"BTC": Decimal("0.001")}

    # compute_trade_leg needs venue lot sizes.
    pacifica_stub.LOTS["BTC"] = Decimal("0.01")
    extended_stub.LOTS["BTC-USD"] = Decimal("0.001")

    main._evaluate_entries(exposures, [opp], pac_funding, ext_funding)

    # The orphaned leg is now tracked under BTC and counts in cap accounting.
    assert "BTC" in exposures
    assert len(exposures["BTC"].legs) == 1
    assert exposures["BTC"].legs[0].orphaned is True
    assert ops.total_exposure_usd(exposures) == Decimal("20.2")  # counted in caps
    assert exp  # silence unused


def test_reconcile_orphans_flattens_and_drops_on_next_cycle(pacifica_stub, extended_stub):
    """Audit #1/#2: the orphan is force-reconciled against venue truth."""
    exposures = {"BTC": SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))}
    orphan = _leg()
    orphan.extended_amount = 0.0
    orphan.orphaned = True
    exposures["BTC"].append_leg(orphan)

    # Venue truth: the naked Pacifica long is still live; reconcile must close it.
    pacifica_stub.POSITIONS = {"data": [{"symbol": "BTC", "side": "bid", "amount": "0.2"}]}
    extended_stub.POSITIONS = {"data": []}

    main._reconcile_orphans(exposures)

    # Closed against venue truth and the now-clean exposure is dropped.
    assert ("close", "BTC", "long", Decimal("0.2")) in pacifica_stub.CALLS
    assert "BTC" not in exposures


def test_reconcile_orphans_keeps_unverifiable_orphan(pacifica_stub, extended_stub):
    """If positions can't be fetched, keep the orphan and retry next cycle."""
    exposures = {"BTC": SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))}
    orphan = _leg()
    orphan.orphaned = True
    exposures["BTC"].append_leg(orphan)

    # get_positions raises -> live amount unknown -> reported live -> close attempted.
    pacifica_stub.RAISE_ON.add("get_positions")
    extended_stub.RAISE_ON.add("get_positions")

    main._reconcile_orphans(exposures)
    # Could not confirm flat: orphan is retained for a later cycle.
    assert "BTC" in exposures
    assert exposures["BTC"].legs[0].orphaned is True


def test_reconcile_orphans_ignores_clean_exposures(pacifica_stub, extended_stub):
    """Exposures with no orphaned legs are untouched (no spurious venue calls)."""
    exposures = {"BTC": SymbolExposure("BTC", "BTC-USD", ("extended", "pacifica"))}
    exposures["BTC"].append_leg(_leg())  # clean, both legs hedged
    main._reconcile_orphans(exposures)
    assert "BTC" in exposures
    assert pacifica_stub.CALLS == []
    assert extended_stub.CALLS == []
